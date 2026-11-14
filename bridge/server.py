"""The weekend-slice bridge service (1.1, 1.2, 1.4): a single HTTP endpoint,
bound to one interface, that turns a spoken question into a spoken summary
of a headless Claude Code reply.
"""
from __future__ import annotations

import hmac
import logging
import socket
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bridge import claude_adapter, stt, tts
from bridge.config import BEHIND_PROXY_WARNING, BridgeConfig, ConfigError, load_config
from bridge.logging_setup import configure as configure_logging
from bridge.sessions import SessionStore
from bridge.summary import extract_summary

logger = logging.getLogger("bridge.server")


MAX_AUDIO_BYTES = 10 * 1024 * 1024   # about ten minutes of 16 kHz WAV; a voice turn is seconds
NOT_HEARD = "Sorry, I didn't catch that."
_project_locks: dict[str, threading.Lock] = {}
_project_locks_guard = threading.Lock()


def _project_lock(name: str) -> threading.Lock:
    with _project_locks_guard:
        return _project_locks.setdefault(name, threading.Lock())


def is_authorized(auth_header: str | None, expected_token: str) -> bool:
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    supplied = auth_header[len("Bearer "):]
    # Bytes, so a non-ASCII header is a plain "no", not a TypeError.
    return hmac.compare_digest(supplied.encode("utf-8", "surrogateescape"),
                               expected_token.encode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    config: BridgeConfig
    sessions: SessionStore

    server_version = "jarvis-bridge/0.1"
    # A client that stalls mid-request does not hold a thread forever.
    timeout = 30

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        logger.info("%s - %s", self.address_string(), format % args)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/turn":
            self._respond_error(404, "not found")
            return

        if not is_authorized(self.headers.get("Authorization"), self.config.token):
            self._respond_error(401, "missing or invalid token")
            return

        query = urllib.parse.parse_qs(parsed.query)
        project_name = query.get("project", [None])[0]
        if not project_name:
            self._respond_error(400, "project query parameter is required")
            return

        try:
            project = self.config.project(project_name)
        except ConfigError as exc:
            self._respond_error(400, str(exc))
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._respond_error(400, "Content-Length is not a number")
            return
        if length <= 0:
            self._respond_error(400, "empty request body")
            return
        if length > MAX_AUDIO_BYTES:
            self._respond_error(413, "audio too large")
            return
        audio_bytes = self.rfile.read(length)

        lock = _project_lock(project_name)
        if not lock.acquire(blocking=False):
            # Two turns on one session would race on its id (session continuity).
            self._respond_error(409, "this project is busy with another turn")
            return
        try:
            self._handle_turn(project_name, project.working_dir, audio_bytes)
        except claude_adapter.ClaudeTurnError as exc:
            logger.error("claude turn failed for %s: %s", project_name, exc)
            self._respond_error(502, "the agent turn failed; see the log")
        except Exception:  # noqa: BLE001 - last-resort guard for a voice endpoint
            logger.exception("unexpected error handling turn for %s", project_name)
            self._respond_error(500, "internal error")
        finally:
            lock.release()

    def _handle_turn(self, project_name: str, working_dir: Path, audio_bytes: bytes) -> None:
        transcript = stt.transcribe(audio_bytes, model_size=self.config.whisper_model)
        logger.info("project=%s transcript=%r", project_name, transcript)
        if not transcript.strip():
            # Silence: no agent turn, just say so.
            self._respond_audio(tts.synthesize(NOT_HEARD, voice=self.config.kokoro_voice), NOT_HEARD, "")
            return

        session_id = self.sessions.get(project_name)
        reply_text, new_session_id = claude_adapter.run_turn(
            prompt=transcript,
            working_dir=working_dir,
            session_id=session_id,
            allowed_tools=self.config.allowed_tools,
            timeout_s=self.config.claude_timeout_s,
        )
        self.sessions.set(project_name, new_session_id)
        logger.info(
            "project=%s session=%s full_reply=%r", project_name, new_session_id, reply_text
        )

        summary = extract_summary(reply_text)
        logger.info("project=%s summary=%r", project_name, summary)

        audio_reply = tts.synthesize(summary, voice=self.config.kokoro_voice)
        self._respond_audio(audio_reply, summary, new_session_id)

    def _respond_audio(self, audio_reply: bytes, summary: str, session_id: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio_reply)))
        self.send_header("X-Jarvis-Session-Id", session_id)
        self.send_header("X-Jarvis-Summary", urllib.parse.quote(summary))
        self.end_headers()
        self.wfile.write(audio_reply)

    def _respond_error(self, status: int, message: str) -> None:
        body = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(config: BridgeConfig) -> ThreadingHTTPServer:
    Handler.config = config
    Handler.sessions = SessionStore(config.session_store_path)
    server_cls = ThreadingHTTPServer
    if ":" in config.bind_host:   # a Tailscale IPv6 address
        server_cls = type("ThreadingHTTPServerV6", (ThreadingHTTPServer,),
                          {"address_family": socket.AF_INET6})
    return server_cls((config.bind_host, config.port), Handler)


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"jarvis-bridge: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    configure_logging(config.log_path)
    if config.behind_tailscale_proxy:
        logger.warning(BEHIND_PROXY_WARNING, config.bind_host)
    # Load both speech models before the first turn: on a small CPU the first
    # Kokoro load alone took over two minutes (2026-11-14 tailnet test).
    logger.info("loading speech models (whisper %s, kokoro)", config.whisper_model)
    stt.warm_up(config.whisper_model)
    tts.warm_up()
    server = make_server(config)
    logger.info("jarvis-bridge listening on %s:%s", config.bind_host, config.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
