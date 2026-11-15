"""The weekend-slice bridge service (1.1, 1.2, 1.4): a single HTTP endpoint,
bound to one interface, that turns a spoken question into a spoken summary
of a headless Claude Code reply.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bridge import claude_adapter, remote_adapter, stt, tts
from bridge.config import (
    BEHIND_PROXY_WARNING,
    MODE_FULL,
    BridgeConfig,
    ConfigError,
    ProjectConfig,
    load_config,
)
from bridge.full_mode import FullMode
from bridge.logging_setup import FILE_ONLY, STREAM_ONLY
from bridge.logging_setup import configure as configure_logging
from bridge.readonly_adapter import ReadonlyAdapter
from bridge.sessions import SessionStore

logger = logging.getLogger("bridge.server")


MAX_AUDIO_BYTES = 10 * 1024 * 1024   # about ten minutes of 16 kHz WAV; a voice turn is seconds
NOT_HEARD = "Sorry, I didn't catch that."
# An early 4xx is sent only after the announced body has been read and thrown
# away (see Handler._reject). That read stops after this many seconds in total,
# however slowly the client trickles bytes in.
DRAIN_DEADLINE_S = 30.0
DRAIN_CHUNK_BYTES = 64 * 1024
_project_locks: dict[str, threading.Lock] = {}
_project_locks_guard = threading.Lock()


def _project_lock(name: str) -> threading.Lock:
    with _project_locks_guard:
        return _project_locks.setdefault(name, threading.Lock())


def _parse_content_length(value: str | None) -> int | None:
    """The announced body length; 0 when absent, None when not a valid number."""
    if value is None:
        return 0
    try:
        length = int(value)
    except ValueError:
        return None
    return length if length >= 0 else None


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
    readonly: ReadonlyAdapter
    # Set in `full` mode only (jarvis-full-agent D30); None means `readonly`.
    full: FullMode | None = None

    server_version = "jarvis-bridge/0.1"
    # A client that stalls mid-request does not hold a thread forever.
    timeout = 30

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        logger.info("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
        """`GET /projects`: the project names, sorted, as JSON (add-project-list).

        The same gate as /turn: the route, then the token in constant time, and a
        refusal drains an announced body first (_reject). Names only, never paths.
        """
        self._body_length = _parse_content_length(self.headers.get("Content-Length"))

        if urllib.parse.urlparse(self.path).path != "/projects":
            self._reject(404, "not found")
            return

        if not is_authorized(self.headers.get("Authorization"), self.config.token):
            self._reject(401, "missing or invalid token")
            return

        # A GET has no body; one that announces one anyway is read and thrown away
        # like a refused upload, so the connection stays in step.
        length = self._body_length
        keep_alive = length == 0 or (length is not None and length <= MAX_AUDIO_BYTES
                                     and self._drain(length))
        try:
            names = self.config.project_names()
        except Exception:  # noqa: BLE001 - the list is best effort, never a traceback to the phone
            logger.exception("listing the projects failed")
            self._respond_error(500, "internal error", close=not keep_alive)
            return
        body = json.dumps({"projects": names}).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if not keep_alive:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except OSError as exc:
            self.close_connection = True
            logger.info("could not send the project list to %s: %s", self.address_string(), exc)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
        # Read the announced length before any check, but not the body: a
        # rejection needs it to drain the upload first (_reject).
        self._body_length = _parse_content_length(self.headers.get("Content-Length"))

        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/turn":
            self._reject(404, "not found")
            return

        if not is_authorized(self.headers.get("Authorization"), self.config.token):
            self._reject(401, "missing or invalid token")
            return

        query = urllib.parse.parse_qs(parsed.query)
        project_name = query.get("project", [None])[0]
        if not project_name:
            self._reject(400, "project query parameter is required")
            return

        try:
            project = self.config.project(project_name)
        except ConfigError as exc:
            self._reject(400, str(exc))
            return

        length = self._body_length
        if length is None:
            self._reject(400, "Content-Length is not a valid number")
            return
        if length == 0:
            self._reject(400, "empty request body")
            return
        if length > MAX_AUDIO_BYTES:
            self._reject(413, "audio too large")
            return
        audio_bytes = self.rfile.read(length)

        lock = _project_lock(project_name)
        if not lock.acquire(blocking=False):
            # Two turns on one session would race on its id (session continuity).
            self._respond_error(409, "this project is busy with another turn")
            return
        try:
            self._handle_turn(project, audio_bytes)
        except claude_adapter.ClaudeTurnError as exc:
            # The error can quote Claude Code's stderr or its whole JSON reply:
            # details to the file only, a short line to stdout.
            logger.error("claude turn failed for %s: %s", project_name, exc, extra=FILE_ONLY)
            logger.error("claude turn failed for %s (details in bridge.log)", project_name,
                         extra=STREAM_ONLY)
            self._respond_error(502, "the agent turn failed; see the log")
        except Exception:  # noqa: BLE001 - last-resort guard for a voice endpoint
            logger.exception("unexpected error handling turn for %s", project_name)
            self._respond_error(500, "internal error")
        finally:
            lock.release()

    def _handle_turn(self, project: ProjectConfig, audio_bytes: bytes) -> None:
        project_name = project.name
        transcript = stt.transcribe(audio_bytes, model_size=self.config.whisper_model)
        logger.info("project=%s transcript=%r", project_name, transcript, extra=FILE_ONLY)
        if not transcript.strip():
            # Silence: no agent turn, just say so.
            self._respond_audio(tts.synthesize(NOT_HEARD, voice=self.config.kokoro_voice), NOT_HEARD, "")
            return

        if self.full is not None:
            # The pending confirmation, the fallback and the spoken errors of
            # `full` mode. A yes is only ever read from this transcript.
            outcome = self.full.handle(project, transcript,
                                       lambda: stt.speech_seconds(audio_bytes))
            logger.info("project=%s summary=%r", project_name, outcome.spoken)
            audio_reply = tts.synthesize(outcome.spoken, voice=self.config.kokoro_voice)
            self._respond_audio(audio_reply, outcome.spoken, outcome.ref,
                                needs_confirmation=outcome.needs_confirmation)
            return

        turn = self.readonly.run_turn(project=project, prompt=transcript)
        summary = turn.spoken
        logger.info("project=%s summary=%r", project_name, summary)

        audio_reply = tts.synthesize(summary, voice=self.config.kokoro_voice)
        self._respond_audio(audio_reply, summary, turn.ref)

    def _respond_audio(self, audio_reply: bytes, summary: str, session_id: str, *,
                       needs_confirmation: bool = False) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio_reply)))
        self.send_header("X-Jarvis-Session-Id", session_id)
        self.send_header("X-Jarvis-Summary", urllib.parse.quote(summary))
        if needs_confirmation:
            # D36: this reply is a read-back; the next utterance is the answer.
            self.send_header("X-Jarvis-Needs-Confirmation", "1")
        self.end_headers()
        self.wfile.write(audio_reply)

    def _reject(self, status: int, message: str) -> None:
        """Answer a request that is refused before its body was read.

        Closing with the upload still arriving makes the kernel reset the
        connection, and the client sees a broken pipe instead of the status
        (jarvis#12: from about 700 KB on loopback, some 22 s of speech). So a
        body whose announced length is valid and within MAX_AUDIO_BYTES is read
        and discarded first, in chunks, never kept.

        Trade-off: the token is checked first, but a refused request is drained
        too, so an unauthenticated client can make the bridge read and discard
        up to MAX_AUDIO_BYTES (10 MB) per request. It cannot hold the thread long:
        the drain stops at DRAIN_DEADLINE_S in total (each read also within the
        30 s socket timeout), and the connection is then closed. A body over the
        cap (413) or without a valid length is not drained at all: the reply
        goes out at once with "Connection: close", as before.
        """
        length = self._body_length
        drained = False
        if length is not None and 0 < length <= MAX_AUDIO_BYTES:
            drained = self._drain(length)
        self._respond_error(status, message, close=not drained)

    def _drain(self, length: int) -> bool:
        """Read and discard `length` body bytes; True if all of them arrived in time."""
        deadline = time.monotonic() + DRAIN_DEADLINE_S
        remaining = length
        try:
            while remaining > 0:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self.connection.settimeout(min(self.timeout, left))
                chunk = self.rfile.read1(min(remaining, DRAIN_CHUNK_BYTES))
                if not chunk:
                    return False
                remaining -= len(chunk)
            return True
        except OSError:   # includes the socket timeout
            return False
        finally:
            self.connection.settimeout(self.timeout)

    def _respond_error(self, status: int, message: str, *, close: bool = False) -> None:
        body = message.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if close:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except OSError as exc:
            # The client went away (or stalled past the drain); nothing to tell it.
            self.close_connection = True
            logger.info("could not send %s to %s: %s", status, self.address_string(), exc)


def make_server(config: BridgeConfig) -> ThreadingHTTPServer:
    Handler.config = config
    Handler.sessions = SessionStore(config.session_store_path)
    Handler.readonly = ReadonlyAdapter(
        sessions=Handler.sessions,
        allowed_tools=config.allowed_tools,
        timeout_s=config.claude_timeout_s,
    )
    Handler.full = None
    if config.agent_mode == MODE_FULL:
        if config.agent_host is None:
            raise ConfigError("JARVIS_AGENT_MODE=full without an agent host")
        Handler.full = FullMode(
            remote=remote_adapter.RemoteAdapter(config.agent_host),
            readonly=Handler.readonly,
            confirm_yes=config.confirm_yes,
            confirm_no=config.confirm_no,
            min_speech_s=config.confirm_min_speech_s,
        )
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

    configure_logging(
        config.log_path,
        secrets=(config.token, os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")),
    )
    if config.behind_tailscale_proxy:
        logger.warning(BEHIND_PROXY_WARNING, config.bind_host)
    try:
        claude_adapter.check_cli()
    except claude_adapter.ClaudeTurnError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc
    if config.agent_mode == MODE_FULL:
        try:
            remote_adapter.check_ssh()
        except RuntimeError as exc:
            logger.error("%s", exc)
            raise SystemExit(1) from exc
        agent = config.agent_host
        logger.info("agent mode full: turns run on %s@%s port %s; read-only is the fallback",
                    agent.user, agent.host, agent.port)
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
