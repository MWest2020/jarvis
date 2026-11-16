"""The weekend-slice bridge service (1.1, 1.2, 1.4): a single HTTP endpoint,
bound to one interface, that turns a spoken question into a spoken summary
of a headless Claude Code reply.
"""
from __future__ import annotations

import hmac
import io
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
# Connections served at once (BoundedHTTPServer). A voice bridge has one user and a
# turn per project; anything beyond this is refused with a 503 instead of a new thread.
MAX_CONNECTIONS = 8
BUSY_MESSAGE = b"the bridge is busy; try again in a moment"
# The 503 is written from the accept loop, so it may never wait on a slow client.
BUSY_SEND_TIMEOUT_S = 1.0
# The request line and all headers must arrive within this many seconds in total,
# counted from the moment the connection's thread starts waiting for them. Without it the 30 s socket timeout applies to each read,
# and a client sending a header byte every few seconds keeps its connection, and its
# slot of MAX_CONNECTIONS, for as long as it likes. JARVIS_HEADER_DEADLINE_S overrides.
HEAD_DEADLINE_S = 10.0
HEAD_DEADLINE_ENV = "JARVIS_HEADER_DEADLINE_S"
TIMEOUT_MESSAGE = b"the request did not arrive in time"
_project_locks: dict[str, threading.Lock] = {}
_project_locks_guard = threading.Lock()


def _project_lock(name: str) -> threading.Lock:
    with _project_locks_guard:
        return _project_locks.setdefault(name, threading.Lock())


def _parse_content_length(value: str | None) -> int | None:
    """The announced body length; 0 when absent, None when not a valid number."""
    if value is None:
        return 0
    # Digits only: int() alone also takes "+5", "5_0", padding and non-ASCII digits,
    # which another HTTP parser on the path could read differently.
    if not (value.isascii() and value.isdigit()):
        return None
    try:
        return int(value)
    except ValueError:   # more digits than int() converts
        return None


def is_authorized(auth_header: str | None, expected_token: str) -> bool:
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    supplied = auth_header[len("Bearer "):]
    # Bytes, so a non-ASCII header is a plain "no", not a TypeError.
    return hmac.compare_digest(supplied.encode("utf-8", "surrogateescape"),
                               expected_token.encode("utf-8"))


def _head_deadline_from_env(environ=os.environ) -> float:
    raw = environ.get(HEAD_DEADLINE_ENV, "").strip()
    if not raw:
        return HEAD_DEADLINE_S
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not 0 < value <= 300:
        raise ConfigError(f"{HEAD_DEADLINE_ENV} must be a number of seconds between 0 and 300, got {raw!r}")
    return value


class _DeadlineReader(io.RawIOBase):
    """The connection's read side, with an optional deadline over several reads.

    While `deadline` (a time.monotonic() value) is set, each recv gets only the time
    that is left, and none once it has passed; `expired` then says why the read
    failed. With no deadline the socket's own timeout applies, as before.
    """

    def __init__(self, sock: socket.socket, timeout: float) -> None:
        super().__init__()
        self._sock = sock
        self._timeout = timeout
        self.deadline: float | None = None
        self.expired = False

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        if self.deadline is None:
            return self._sock.recv_into(buffer)
        left = self.deadline - time.monotonic()
        if left <= 0:
            self.expired = True
            raise TimeoutError("the request head did not arrive in time")
        self._sock.settimeout(min(self._timeout, left))
        try:
            return self._sock.recv_into(buffer)
        except TimeoutError:
            self.expired = True
            raise


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

    def setup(self) -> None:
        super().setup()
        # Same socket, but reads go through a reader that can hold a total deadline.
        self.rfile.close()
        self._reader = _DeadlineReader(self.connection, self.timeout)
        self.rfile = io.BufferedReader(self._reader)
        self._requests_started = 0

    def handle_one_request(self) -> None:
        """One request, its line and headers under HEAD_DEADLINE_S in total (jarvis#15).

        The stdlib turns the timeout into a closed connection; a 408 goes out first,
        so a slow but honest client learns why. Only for the first request on a
        connection: the bridge answers HTTP/1.0 and closes after each reply, but if it
        ever kept connections alive, a 408 on an idle one would be read as the answer
        to the client's next request.
        """
        self._requests_started += 1
        self._reader.expired = False
        self._reader.deadline = time.monotonic() + HEAD_DEADLINE_S
        try:
            super().handle_one_request()
        finally:
            self._reader.deadline = None
        if self._reader.expired:
            self.close_connection = True
            if self._requests_started == 1:
                try:
                    self.connection.settimeout(BUSY_SEND_TIMEOUT_S)
                    self.connection.sendall(
                        b"HTTP/1.1 408 Request Timeout\r\n"
                        b"Content-Type: text/plain; charset=utf-8\r\n"
                        b"Content-Length: " + str(len(TIMEOUT_MESSAGE)).encode("ascii") + b"\r\n"
                        b"Connection: close\r\n\r\n" + TIMEOUT_MESSAGE
                    )
                except OSError:
                    pass

    def parse_request(self) -> bool:
        try:
            return super().parse_request()
        finally:
            # The head is in (or refused): what follows, the body and the turn, is
            # under the socket timeout and the drain deadline again.
            self._reader.deadline = None
            try:
                self.connection.settimeout(self.timeout)
            except OSError:
                pass

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
            if self.full is not None:
                # `full` mode: the agent host's list, or the bridge's own when it
                # cannot give one, and which it is (jarvis-general-conversation D52).
                names, source = self.full.project_names(self.config.project_names)
                listing = {"projects": names, "source": source}
            else:
                listing = {"projects": self.config.project_names()}
        except Exception:  # noqa: BLE001 - the list is best effort, never a traceback to the phone
            logger.exception("listing the projects failed")
            self._respond_error(500, "internal error", close=not keep_alive)
            return
        body = json.dumps(listing).encode("utf-8")
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
            # In `full` mode the agent host decides which projects exist (D52).
            project = self.config.project_for_turn(project_name)
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
        audio = audio_bytes
        if self.full is not None:
            # Decoded once: `full` mode needs the samples for the transcript and
            # again for the detected speech.
            audio = stt.decode(audio_bytes)
        transcript = stt.transcribe(audio, model_size=self.config.whisper_model)
        logger.info("project=%s transcript=%r", project_name, transcript, extra=FILE_ONLY)
        if not transcript.strip():
            # Silence: no agent turn, just say so.
            self._respond_audio(tts.synthesize(NOT_HEARD, voice=self.config.kokoro_voice), NOT_HEARD, "")
            return

        if self.full is not None:
            # The pending confirmation, the fallback and the spoken errors of
            # `full` mode. A yes is only ever read from this transcript.
            outcome = self.full.handle(project, transcript,
                                       lambda: stt.speech_seconds(audio))
            logger.info("project=%s summary=%r", project_name, outcome.spoken)
            try:
                audio_reply = tts.synthesize(outcome.spoken, voice=self.config.kokoro_voice)
                self._respond_audio(audio_reply, outcome.spoken, outcome.ref,
                                    needs_confirmation=outcome.needs_confirmation,
                                    projects_changed=outcome.projects_changed)
            except BaseException:
                # A read-back that was not spoken, or did not reach the phone, was
                # not heard: nothing stays pending, here or on the agent host.
                self.full.undelivered(project, outcome)
                raise
            self.full.delivered(project, outcome)
            return

        turn = self.readonly.run_turn(project=project, prompt=transcript)
        summary = turn.spoken
        logger.info("project=%s summary=%r", project_name, summary)

        audio_reply = tts.synthesize(summary, voice=self.config.kokoro_voice)
        self._respond_audio(audio_reply, summary, turn.ref)

    def _respond_audio(self, audio_reply: bytes, summary: str, session_id: str, *,
                       needs_confirmation: bool = False,
                       projects_changed: bool = False) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio_reply)))
        self.send_header("X-Jarvis-Session-Id", session_id)
        self.send_header("X-Jarvis-Summary", urllib.parse.quote(summary))
        if needs_confirmation:
            # D36: this reply is a read-back; the next utterance is the answer.
            self.send_header("X-Jarvis-Needs-Confirmation", "1")
        if projects_changed:
            # jarvis-general-conversation D56: a new project is ready; the phone
            # fetches GET /projects again.
            self.send_header("X-Jarvis-Projects-Changed", "1")
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


class BoundedHTTPServer(ThreadingHTTPServer):
    """A thread per connection, but at most MAX_CONNECTIONS of them (jarvis#15).

    A connection holds its slot from accept until it closes, whatever it is doing:
    sending headers (at most HEAD_DEADLINE_S), uploading, draining, or waiting for
    the agent. One over the cap gets no thread and no queue place: a 503 is written
    from the accept loop, without reading the request, and the socket is closed.
    """

    # The listen backlog. The stdlib's 5 is less than the cap: a burst of connections
    # then overflows it, and the kernel makes the extra ones wait a second or more for
    # a retransmit instead of letting the bridge accept them and say 503 at once.
    request_queue_size = 64

    def __init__(self, *args, **kwargs) -> None:
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address) -> None:
        if not self._slots.acquire(blocking=False):
            self._refuse_busy(request, client_address)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:   # the thread did not start, so nothing else frees the slot
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def _refuse_busy(self, request, client_address) -> None:
        logger.warning("refused %s: %s connections already open", client_address[0], MAX_CONNECTIONS)
        try:
            request.settimeout(BUSY_SEND_TIMEOUT_S)
            request.sendall(
                b"HTTP/1.1 503 Service Unavailable\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n"
                b"Content-Length: " + str(len(BUSY_MESSAGE)).encode("ascii") + b"\r\n"
                b"Retry-After: 5\r\n"
                b"Connection: close\r\n\r\n" + BUSY_MESSAGE
            )
        except OSError as exc:
            logger.info("could not send 503 to %s: %s", client_address[0], exc)
        finally:
            self.shutdown_request(request)


def make_server(config: BridgeConfig) -> BoundedHTTPServer:
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
    server_cls = BoundedHTTPServer
    if ":" in config.bind_host:   # a Tailscale IPv6 address
        server_cls = type("BoundedHTTPServerV6", (BoundedHTTPServer,),
                          {"address_family": socket.AF_INET6})
    return server_cls((config.bind_host, config.port), Handler)


def main() -> None:
    global HEAD_DEADLINE_S
    try:
        config = load_config()
        HEAD_DEADLINE_S = _head_deadline_from_env()
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
