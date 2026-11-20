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
import re
import socket
import sys
import threading
import time
import urllib.parse
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bridge import claude_adapter, journal, remote_adapter, singing, stt, tts
from bridge.config import (
    BEHIND_PROXY_WARNING,
    MODE_FULL,
    BridgeConfig,
    ConfigError,
    ProjectConfig,
    load_config,
)
from bridge.full_mode import FRAMING_SENTENCES, FullMode
from bridge.full_mode import NOT_HEARD as FULL_NOT_HEARD
from bridge.journal import Journal
from bridge.logging_setup import FILE_ONLY, STREAM_ONLY, RedactingFilter
from bridge.logging_setup import configure as configure_logging
from bridge.readonly_adapter import ReadonlyAdapter
from bridge.sessions import SessionStore
from bridge.timing import HEADER as TIMING_HEADER
from bridge.timing import TurnTimer, wav_seconds

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
# Held per project while its turn runs. In `full` mode any name of the agent host's
# shape can be asked for (D52), so a lock lives only while it is held: taken and
# dropped under one guard, so two turns can never hold two locks for one name.
_project_locks: dict[str, threading.Lock] = {}
_project_locks_guard = threading.Lock()


def _try_project_lock(name: str) -> bool:
    with _project_locks_guard:
        lock = _project_locks.setdefault(name, threading.Lock())
        return lock.acquire(blocking=False)


def _release_project_lock(name: str) -> None:
    with _project_locks_guard:
        lock = _project_locks.pop(name)
        lock.release()


def _running_projects() -> list[str]:
    """The projects whose turn is running now (a lock lives only while it is held)."""
    with _project_locks_guard:
        return sorted(_project_locks)


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


# jarvis-streaming-speech S6: the phone cancels a read-back it did not play to the end.
CANCEL_PATH = "/turn/cancel"
CONFIRMATION_HEADER = "X-Jarvis-Confirmation-Id"
# The shape the remote adapter accepts for a confirmation id (remote_adapter._CONFIRM_ID).
_CONFIRMATION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def accepts_stream(accept: str | None) -> bool:
    """True when an `Accept` header names the streamed reply's format
    (jarvis-streaming-speech S1): `audio/L16` with `rate=24000`, `channels` absent or
    1, and a quality above 0. `*/*`, `audio/*` or `audio/wav` do not count: a client
    that never asked keeps getting the WAV."""
    if not accept:
        return False
    for entry in accept.split(","):
        kind, *params = (part.strip() for part in entry.split(";"))
        if kind.lower() != "audio/l16":
            continue
        values = {}
        for param in params:
            name, _, value = param.partition("=")
            values[name.strip().lower()] = value.strip().strip('"')
        if values.get("rate") != str(tts.SAMPLE_RATE) or values.get("channels", "1") != "1":
            continue
        try:
            quality = float(values.get("q", "1"))
        except ValueError:
            continue
        if quality > 0:
            return True
    return False


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
    # add-drive-mode phase 1: the drive journal (D10).
    journal: Journal | None = None

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
        # Per-stage timing of the turn (bridge.timing), from the head on.
        timer = TurnTimer()
        # jarvis-streaming-speech S1, S2: a streamed reply only for a client that asks
        # and speaks HTTP/1.1 (a chunked body needs it), and only while the switch is on.
        self._stream_reply = (self.config.stream_replies
                              and self.request_version == "HTTP/1.1"
                              and accepts_stream(self.headers.get("Accept")))
        self._headers_out = False
        # Read the announced length before any check, but not the body: a
        # rejection needs it to drain the upload first (_reject).
        self._body_length = _parse_content_length(self.headers.get("Content-Length"))

        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in ("/turn", CANCEL_PATH):
            self._reject(404, "not found")
            return

        if not is_authorized(self.headers.get("Authorization"), self.config.token):
            self._reject(401, "missing or invalid token")
            return

        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == CANCEL_PATH:
            self._cancel_read_back(query)
            return
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
        with timer.stage("read"):
            audio_bytes = self.rfile.read(length)

        # Two turns on one session would race on its id (session continuity). A busy
        # project still hears its control words (add-drive-mode D18): the recording is
        # transcribed, and anything else gets the 409 in _handle_turn.
        locked = _try_project_lock(project_name)
        try:
            self._handle_turn(project, audio_bytes, timer, busy=not locked)
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
            if locked:
                _release_project_lock(project_name)

    def _cancel_read_back(self, query: dict) -> None:
        """`POST /turn/cancel?project=<name>&id=<confirmation id>` (S6): the phone did not
        play a read-back to its end, so it is not heard and the agent host gets a `no`.
        Idempotent: `{"cancelled": false}` when nothing with that id is pending (answered,
        expired, cancelled before, another read-back, or `readonly` mode). No project
        lock: the cancel can come while the read-back's turn is still writing."""
        # No body is expected; without a Content-Length it is empty (_parse_content_length).
        length = self._body_length
        if length is None:
            self._reject(400, "Content-Length is not a valid number")
            return
        if length > MAX_AUDIO_BYTES:
            self._reject(413, "body too large")
            return
        if length and not self._drain(length):
            self._respond_error(400, "the body did not arrive", close=True)
            return
        project_name = query.get("project", [None])[0]
        confirmation_id = query.get("id", [None])[0]
        if not project_name:
            self._respond_error(400, "project query parameter is required")
            return
        if not confirmation_id or not _CONFIRMATION_ID.fullmatch(confirmation_id):
            self._respond_error(400, "id query parameter is missing or not a confirmation id")
            return
        try:
            project = self.config.project_for_turn(project_name)
        except ConfigError as exc:
            self._respond_error(400, str(exc))
            return
        try:
            cancelled = (self.full is not None
                         and self.full.cancel_read_back(project, confirmation_id))
        except Exception:  # noqa: BLE001 - never a traceback to the phone
            logger.exception("cancelling a read-back for %s failed", project_name)
            self._respond_error(500, "internal error")
            return
        logger.info("project=%s read-back cancel from the phone: %s", project_name,
                    "cancelled" if cancelled else "nothing pending with that id")
        body = json.dumps({"cancelled": bool(cancelled)}).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except OSError as exc:
            self.close_connection = True
            logger.info("could not send the cancel answer to %s: %s", self.address_string(), exc)

    def _handle_turn(self, project: ProjectConfig, audio_bytes: bytes,
                     timer: TurnTimer | None = None, *, busy: bool = False) -> None:
        """`busy`: another turn of this project is running. Only a control word is
        answered then; anything else gets the 409 the busy project always got."""
        timer = timer if timer is not None else TurnTimer()
        project_name = project.name
        audio = audio_bytes
        timer.audio_in_s = wav_seconds(audio_bytes)
        with timer.stage("stt"):
            if self.full is not None:
                # Decoded once: `full` mode needs the samples for the transcript and
                # again for the detected speech.
                audio = stt.decode(audio_bytes)
            transcript = stt.transcribe(audio, model_size=self.config.whisper_model,
                                        **self.config.whisper_options)
        logger.info("project=%s transcript=%r", project_name, transcript, extra=FILE_ONLY)
        if transcript.strip() and self._drive_word(project, transcript, timer, busy=busy):
            return
        if busy:
            self._respond_error(409, "this project is busy with another turn")
            return
        if not transcript.strip():
            # Silence: no agent turn, just say so.
            self._speak(NOT_HEARD, "", timer=timer, project=project_name)
            return

        if self.full is not None:
            # The pending confirmation, the fallback and the spoken errors of
            # `full` mode. A yes is only ever read from this transcript.
            measure = timer.wrap("vad", lambda: stt.speech_seconds(audio))
            handle_started = time.monotonic()
            outcome = self.full.handle(
                project, transcript, measure,
                typed_list=self.headers.get("X-Jarvis-Projects", "") == "typed")
            # The agent's part: the handling minus the speech measurement in it.
            timer.add("agent", time.monotonic() - handle_started - timer.ms.get("vad", 0) / 1000)
            # The bridge's notice, fallback and news sentences may stand around the
            # agent's own line: the tag is looked for on that line.
            spoken, song = self._song(outcome.spoken, transcript,
                                      read_back=outcome.needs_confirmation,
                                      framing=FRAMING_SENTENCES)
            logger.info("project=%s summary=%r", project_name, spoken)
            if outcome.ref or outcome.spoken != FULL_NOT_HEARD:
                # A noise that was not taken as an utterance is not a turn.
                self._journal_turn(project_name, transcript, spoken, turn_id=outcome.ref,
                                   read_back=outcome.needs_confirmation,
                                   answer=self.full.is_answer(transcript))
            try:
                complete = self._speak(spoken, outcome.ref, song=song,
                                       needs_confirmation=outcome.needs_confirmation,
                                       projects_changed=outcome.projects_changed,
                                       confirmation_id=outcome.confirmation_id,
                                       timer=timer, project=project_name)
            except BaseException:
                # A read-back that was not spoken, or did not reach the phone, was
                # not heard: nothing stays pending, here or on the agent host.
                self.full.undelivered(project, outcome)
                raise
            if complete:
                self.full.delivered(project, outcome)
            else:
                # A streamed reply that broke off after its headers (S6).
                self.full.undelivered(project, outcome)
            return

        with timer.stage("agent"):
            turn = self.readonly.run_turn(project=project, prompt=transcript)
        summary, song = self._song(turn.spoken, transcript, read_back=False)
        logger.info("project=%s summary=%r", project_name, summary)
        self._journal_turn(project_name, transcript, summary, session_id=turn.ref)
        self._speak(summary, turn.ref, song=song, timer=timer, project=project_name)

    def _journal_turn(self, project: str, transcript: str, spoken: str, **fields) -> None:
        """The turn into the drive journal (D10), before it is spoken: a reply that
        does not reach the phone still ran. The journal never costs the turn."""
        if self.journal is None:
            return
        try:
            self.journal.record_turn(project=project, transcript=transcript,
                                     spoken=spoken, **fields)
        except Exception:  # noqa: BLE001 - a full disk is not the driver's problem now
            logger.exception("project=%s: the turn could not be written to the journal",
                             project)

    def _drive_word(self, project: ProjectConfig, transcript: str, timer: TurnTimer, *,
                    busy: bool) -> bool:
        """add-drive-mode D18, D19: "where was I" and "note ..." from the driver's own
        transcript, answered by the bridge without an agent turn, also while the agent is
        busy or down. True when the utterance was one of them and has been answered.

        Like every utterance that is not a yes, a control word uses up a pending
        read-back (D36), and the driver is told it was not done.
        """
        if self.journal is None:
            return False
        word = journal.match(transcript, recap_words=self.config.recap_words,
                             note_words=self.config.note_words)
        if word is None:
            return False
        name = project.name
        dropped = False
        if self.full is not None:
            try:
                dropped = self.full.drop_pending(project)
            except Exception:  # noqa: BLE001 - the bridge's side has dropped it already
                logger.exception("project=%s: cancelling the read-back failed", name)
                dropped = True
        if word.kind == journal.RECAP:
            running = [p for p in _running_projects() if busy or p != name]
            try:
                spoken = self.journal.recap(running=running)
                self.journal.record_recap(project=name)
            except Exception:  # noqa: BLE001 - never a traceback to the phone
                logger.exception("project=%s: the recap could not be built", name)
                spoken = journal.RECAP_FAILED
        elif not word.text:
            spoken = journal.NOTE_EMPTY
        else:
            try:
                self.journal.add_note(project=name, text=word.text)
                spoken = journal.NOTED
            except Exception:  # noqa: BLE001 - the note is lost, and the driver hears so
                logger.exception("project=%s: the note could not be written", name)
                spoken = journal.NOTE_FAILED
        if dropped:
            spoken = f"{spoken} {journal.READ_BACK_DROPPED}"
        logger.info("project=%s control word=%s", name, word.kind)
        self._speak(spoken, "", timer=timer, project=name)
        return True

    def _song(self, spoken: str, transcript: str, *, read_back: bool,
              framing: tuple[str, ...] = ()) -> tuple[str, singing.SongRequest | None]:
        """jarvis-sing: the agent's reply, and the song when it is to be sung. Only
        for an agent's reply the driver asked to be sung, never for a read-back."""
        text, song = singing.choose(spoken, transcript=transcript,
                                    enabled=self.config.sing, read_back=read_back,
                                    framing=framing, parody=self.config.sing_parody)
        if song is not None:
            logger.info("sung: %s", song.tune.id)
        return text, song

    def _speak(self, text: str, session_id: str, *, needs_confirmation: bool = False,
               projects_changed: bool = False, confirmation_id: str = "",
               song: singing.SongRequest | None = None,
               timer: TurnTimer, project: str) -> bool:
        """Render `text` and send it: as a WAV, or as a stream to a client that asked
        for one (jarvis-streaming-speech).

        Raises when the failure came before anything was sent (do_POST then answers
        500, as before). Returns False when a streamed reply broke off after its
        headers (S6), True when the whole reply was written.

        `song`: sing it instead (jarvis-sing), rendered whole before the status line.
        When that fails, `text` is spoken as usual.
        """
        voice = self.config.kokoro_voice
        if song is not None and not needs_confirmation:
            try:
                with timer.stage("tts"):
                    samples = tts.sing(song, voice=voice)
            except Exception:  # noqa: BLE001 - an easter egg never costs the reply
                logger.exception("project=%s: the song could not be rendered; spoken instead",
                                 project)
            else:
                if not getattr(self, "_stream_reply", False):
                    timer.reply = "wav"
                    self._respond_audio(tts.wav_bytes(samples), text, session_id,
                                        projects_changed=projects_changed,
                                        timer=timer, project=project)
                    return True
                timer.reply = "stream"
                return self._respond_stream(iter(tts.pcm_pieces(samples)), text, session_id,
                                            projects_changed=projects_changed,
                                            timer=timer, project=project, rendered=True)
        if not getattr(self, "_stream_reply", False):
            timer.reply = "wav"
            with timer.stage("tts"):
                audio_reply = tts.synthesize(text, voice=voice)
            self._respond_audio(audio_reply, text, session_id,
                                needs_confirmation=needs_confirmation,
                                projects_changed=projects_changed,
                                confirmation_id=confirmation_id, timer=timer, project=project)
            return True
        timer.reply = "stream"
        if needs_confirmation:
            # S5: a read-back is rendered whole, with Kokoro's own split (the WAV's
            # call), and before the status line: a failure here is still a 500 with
            # nothing pending, and its words go out exactly as built.
            with timer.stage("tts"):
                pieces = list(tts.stream_pcm(text, voice=voice, whole=True))
            return self._respond_stream(iter(pieces), text, session_id,
                                        needs_confirmation=True,
                                        projects_changed=projects_changed,
                                        confirmation_id=confirmation_id,
                                        timer=timer, project=project, rendered=True)
        return self._respond_stream(tts.stream_pcm(text, voice=voice), text, session_id,
                                    projects_changed=projects_changed,
                                    timer=timer, project=project, rendered=False)

    def _reply_headers(self, summary: str, session_id: str, *, needs_confirmation: bool,
                       projects_changed: bool, timer: TurnTimer | None,
                       confirmation_id: str = "") -> None:
        """The X-Jarvis-* headers of a reply, the same for a WAV and a stream."""
        self.send_header("X-Jarvis-Session-Id", session_id)
        self.send_header("X-Jarvis-Summary", urllib.parse.quote(summary))
        if needs_confirmation:
            # D36: this reply is a read-back; the next utterance is the answer.
            self.send_header("X-Jarvis-Needs-Confirmation", "1")
            if confirmation_id:
                # S6: what the phone sends to POST /turn/cancel when it did not play
                # the read-back to its end.
                self.send_header(CONFIRMATION_HEADER, confirmation_id)
        if projects_changed:
            # jarvis-general-conversation D56: a new project is ready; the phone
            # fetches GET /projects again.
            self.send_header("X-Jarvis-Projects-Changed", "1")
        if timer is not None:
            # Milliseconds per stage so far, for a client that wants to show them.
            self.send_header(TIMING_HEADER, timer.header_value())

    def _respond_audio(self, audio_reply: bytes, summary: str, session_id: str, *,
                       needs_confirmation: bool = False,
                       projects_changed: bool = False, confirmation_id: str = "",
                       timer: TurnTimer | None = None, project: str = "") -> None:
        if timer is not None:
            timer.audio_out_s = wav_seconds(audio_reply)
            timer.spoken_chars = len(summary)
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio_reply)))
        self._reply_headers(summary, session_id, needs_confirmation=needs_confirmation,
                            projects_changed=projects_changed, timer=timer,
                            confirmation_id=confirmation_id)
        self.end_headers()
        if timer is None:
            self.wfile.write(audio_reply)
            return
        timer.mark_first_audio()
        with timer.stage("write"):
            self.wfile.write(audio_reply)
        # One line per turn, in the file and on stdout: numbers only, no text.
        logger.info("project=%s timing %s", project, timer.log_fields())

    def _respond_stream(self, pieces: Iterator[bytes], summary: str, session_id: str, *,
                        needs_confirmation: bool = False, projects_changed: bool = False,
                        confirmation_id: str = "",
                        timer: TurnTimer, project: str, rendered: bool) -> bool:
        """The headers at once, then each piece of audio as one chunk of a chunked
        HTTP/1.1 body (S2, S3). `rendered`: the pieces are ready (a read-back, S5);
        otherwise taking the next one renders it, and that time counts as `tts`.

        On a rendering error or a broken connection the body ends without its
        terminating chunk and the connection is closed, so the client can tell a cut
        reply from a complete one (S6). True when the whole body was written.
        """
        timer.spoken_chars = len(summary)
        audio_bytes = 0
        # Only this reply's status line says HTTP/1.1; the connection still closes.
        self.protocol_version = "HTTP/1.1"
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", tts.STREAM_CONTENT_TYPE)
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self._reply_headers(summary, session_id, needs_confirmation=needs_confirmation,
                                projects_changed=projects_changed, timer=timer,
                                confirmation_id=confirmation_id)
            self._headers_out = True
            self.end_headers()
            while True:
                try:
                    if rendered:
                        piece = next(pieces, None)
                    else:
                        with timer.stage("tts"):
                            piece = next(pieces, None)
                except Exception as exc:  # noqa: BLE001 - Kokoro can fail in many ways
                    logger.error("project=%s: the streamed reply could not be rendered "
                                 "after %d bytes; the stream is cut", project, audio_bytes)
                    logger.error("project=%s streamed rendering failed: %r", project, exc,
                                 extra=FILE_ONLY)
                    return self._stream_cut(timer, project, audio_bytes)
                if piece is None:
                    break
                if not piece:
                    continue
                timer.mark_first_audio()
                with timer.stage("write"):
                    self.wfile.write(b"%X\r\n" % len(piece) + piece + b"\r\n")
                audio_bytes += len(piece)
            with timer.stage("write"):
                self.wfile.write(b"0\r\n\r\n")
        except OSError as exc:
            logger.info("project=%s: the streamed reply to %s broke off after %d bytes: %s",
                        project, self.address_string(), audio_bytes, exc)
            return self._stream_cut(timer, project, audio_bytes)
        timer.audio_out_s = audio_bytes / 2 / tts.SAMPLE_RATE
        logger.info("project=%s timing %s", project, timer.log_fields())
        return True

    def _stream_cut(self, timer: TurnTimer, project: str, audio_bytes: int) -> bool:
        self.close_connection = True
        timer.reply = "stream_cut"
        timer.audio_out_s = audio_bytes / 2 / tts.SAMPLE_RATE
        logger.info("project=%s timing %s", project, timer.log_fields())
        return False

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
        if getattr(self, "_headers_out", False):
            # A streamed reply's headers are out: a status cannot follow them. The
            # connection closes, which the client reads as a cut reply (S6).
            self.close_connection = True
            return
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


def _make_journal(config: BridgeConfig) -> Journal | None:
    """The drive journal (add-drive-mode D10), with the log's redaction. A journal that
    cannot be opened never stops the bridge: the drive's words are then not matched and
    every utterance goes to the agent as before."""
    redact = RedactingFilter((config.token, os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))).redact
    try:
        return Journal(config.journal_path, gap_s=config.drive_gap_minutes * 60,
                       retention_s=config.journal_days * 86400, redact=redact)
    except (OSError, ValueError) as exc:
        logger.error("the drive journal in %s cannot be used (%s): \"where was I\" and "
                     "notes are off until it can", config.journal_path, exc)
        return None


def make_server(config: BridgeConfig) -> BoundedHTTPServer:
    Handler.config = config
    Handler.sessions = SessionStore(config.session_store_path)
    Handler.readonly = ReadonlyAdapter(
        sessions=Handler.sessions,
        allowed_tools=config.allowed_tools,
        timeout_s=config.claude_timeout_s,
    )
    Handler.journal = _make_journal(config)
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
    logger.info("loading speech models (whisper %s, beam %s, language %s; kokoro)",
                config.whisper_model, config.whisper_beam_size,
                config.whisper_language or "detected")
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
