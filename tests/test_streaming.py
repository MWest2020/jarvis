"""The streamed reply (jarvis-streaming-speech): the `Accept` match, headers before
the body, one chunk per sentence, a cut body on an error, read-backs rendered whole
before the headers, and the WAV unchanged for every client that does not ask.

Real HTTP server on loopback, faked speech and agent (no Kokoro is loaded here)."""
import http.client
import json
import logging
import socket
import threading
import urllib.parse

import numpy as np
import pytest

from bridge import claude_adapter, server, stt, tts
from bridge.config import BridgeConfig, ProjectConfig
from tests.test_server_full_mode import READBACK, Bridge
from tests.test_timing import timing_lines

STREAM_ACCEPT = "audio/L16;rate=24000;channels=1"
SUMMARY = "The answer to your question is four. Nothing else in the repository changed!"


# --- the Accept match (S1) -----------------------------------------------------------

@pytest.mark.parametrize("accept", [
    "audio/L16;rate=24000;channels=1",
    "audio/l16; rate=24000",
    "AUDIO/L16;RATE=24000;channels=1",
    'audio/L16;rate="24000";channels="1"',
    "audio/L16;rate=24000;channels=1, audio/wav;q=0.5",
    "audio/wav;q=0.5, audio/L16;rate=24000;channels=1;q=0.9",
])
def test_a_client_that_names_the_format_gets_the_stream(accept):
    assert server.accepts_stream(accept)


@pytest.mark.parametrize("accept", [
    None, "", "*/*", "audio/*", "audio/wav", "audio/L16", "audio/L16;rate=16000",
    "audio/L16;rate=24000;channels=2", "audio/L16;rate=24000;q=0",
    "audio/L16;rate=24000;q=zero", "audio/L16x;rate=24000",
])
def test_anything_else_keeps_the_wav(accept):
    assert not server.accepts_stream(accept)


# --- the stream's pieces (S4, S5), without a model ---------------------------------------

class FakePipeline:
    """Stands for KPipeline: a string is split like it does (re.split of the stripped
    text at its default pattern), a list is rendered element by element; one float array
    per piece, so the conversion is real."""

    def __init__(self):
        self.inputs = []

    def __call__(self, text, voice, split_pattern=r"\n+"):
        import re
        self.inputs.append(text)
        parts = re.split(split_pattern, text.strip()) if isinstance(text, str) else text
        for piece in parts:
            if piece.strip():
                yield piece, "", np.full(len(piece), 0.5, dtype=np.float32)


def test_to_l16_is_big_endian_and_clipped():
    assert tts.to_l16(np.array([0.0, 1.0, -1.0, 0.5, 2.0], dtype=np.float32)) == (
        b"\x00\x00" b"\x7f\xff" b"\x80\x01" b"\x40\x00" b"\x7f\xff")


def test_an_ordinary_reply_is_rendered_one_sentence_at_a_time(monkeypatch):
    fake = FakePipeline()
    monkeypatch.setattr(tts, "_pipeline", lambda code: fake)
    text = "Done. The branch is pushed and the build started. Yes."
    pieces = list(tts.stream_pcm(text, voice="bm_george"))
    # "Done." alone would leave a gap while the next sentence renders: it goes with it.
    assert fake.inputs == [["Done. The branch is pushed and the build started.", "Yes."]]
    assert [len(p) // 2 for p in pieces] == [49, 4]


def test_a_read_back_is_rendered_with_kokoros_own_split(monkeypatch):
    fake = FakePipeline()
    monkeypatch.setattr(tts, "_pipeline", lambda code: fake)
    pieces = list(tts.stream_pcm(READBACK, voice="bm_george", whole=True))
    assert fake.inputs == [READBACK]                 # the string, as synthesize() passes it
    assert len(pieces) == 1 and len(pieces[0]) == 2 * len(READBACK)


def test_pieces_join_short_sentences_and_keep_the_words():
    assert tts.pieces("The answer is four. Nothing else changed!") == [
        "The answer is four. Nothing else changed!"]
    long_text = ("I can't search the web from here. I can read the repository and run "
                 "commands at the desk, so ask me about the code.")
    assert tts.pieces(long_text) == [
        "I can't search the web from here.",
        "I can read the repository and run commands at the desk, so ask me about the code."]
    text = "Done. Merged the change into main today. It is live. OK."
    assert tts.pieces(text) == ["Done. Merged the change into main today.", "It is live. OK."]
    assert " ".join(tts.pieces(text)) == text


def test_the_sentence_split_never_changes_the_words():
    text = "Merged 3.5 PRs, e.g. the docs. Is that all? Yes… it is!\nNext line."
    parts = tts.sentences(text)
    assert parts == ["Merged 3.5 PRs, e.g. the docs.", "Is that all?", "Yes… it is!", "Next line."]
    assert " ".join(parts) == text.replace("\n", " ")


# --- the server, readonly mode -----------------------------------------------------------

class Speech:
    """A fake tts.stream_pcm: one piece per sentence, the bytes say which. Can hold
    back the second piece until the test lets it go, or fail at a given piece."""

    def __init__(self):
        self.calls = []
        self.rendered = []
        self.release = threading.Event()
        self.release.set()
        self.fail_at = None

    def __call__(self, text, *, voice, whole=False):
        self.calls.append((text, whole))
        parts = [text] if whole else tts.pieces(text)
        for index, part in enumerate(parts):
            if index == 1:
                self.release.wait(5)
            if self.fail_at == index:
                raise RuntimeError("no voice today")
            self.rendered.append(part)
            yield part.encode()


@pytest.fixture
def stream_server(tmp_path, monkeypatch):
    config = BridgeConfig(
        bind_host="127.0.0.1", port=0, token="test-token",
        projects={"demo": ProjectConfig(name="demo", working_dir=tmp_path)},
        allowed_tools=("Read", "Glob", "Grep"),
        session_store_path=tmp_path / "sessions.json", log_path=tmp_path / "bridge.log",
        whisper_model="base", kokoro_voice="bm_george", claude_timeout_s=30,
    )
    monkeypatch.setattr(stt, "transcribe", lambda audio_bytes, model_size: "what is 2 plus 2")
    monkeypatch.setattr(claude_adapter, "run_turn",
                        lambda **kwargs: (f"4\nSPOKEN SUMMARY: {SUMMARY}", "session-123"))
    monkeypatch.setattr(tts, "synthesize", lambda text, voice: b"FAKE-WAV-" + text.encode())
    speech = Speech()
    monkeypatch.setattr(tts, "stream_pcm", speech)

    class Running:
        pass
    running = Running()
    running.speech = speech
    running.config = config

    def start(cfg):
        running.httpd = server.make_server(cfg)
        running.port = running.httpd.server_address[1]
        running.thread = threading.Thread(target=running.httpd.serve_forever, daemon=True)
        running.thread.start()
    running.start = start
    start(config)
    try:
        yield running
    finally:
        running.httpd.shutdown()
        running.httpd.server_close()
        running.thread.join(timeout=5)


def _request(accept=STREAM_ACCEPT, version="HTTP/1.1", body=b"fake-audio") -> bytes:
    head = (f"POST /turn?project=demo {version}\r\nHost: x\r\n"
            f"Authorization: Bearer test-token\r\nContent-Length: {len(body)}\r\n")
    if accept is not None:
        head += f"Accept: {accept}\r\n"
    return head.encode() + b"\r\n" + body


class Raw:
    """A plain socket, so the test sees the bytes as sent: status, headers, chunks."""

    def __init__(self, port, payload):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sock.sendall(payload)
        self.buffer = b""

    def _fill(self):
        data = self.sock.recv(65536)
        if not data:
            raise EOFError
        self.buffer += data

    def until(self, marker: bytes) -> bytes:
        while marker not in self.buffer:
            self._fill()
        head, self.buffer = self.buffer.split(marker, 1)
        return head

    def exactly(self, n: int) -> bytes:
        while len(self.buffer) < n:
            self._fill()
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def head(self) -> tuple[str, dict[str, str]]:
        lines = self.until(b"\r\n\r\n").decode("latin-1").split("\r\n")
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        return lines[0], headers

    def chunk(self) -> bytes:
        size = int(self.until(b"\r\n"), 16)
        data = self.exactly(size)
        assert self.exactly(2) == b"\r\n"
        return data

    def rest(self) -> bytes:
        try:
            while True:
                self._fill()
        except EOFError:
            pass
        return self.buffer

    def close(self):
        self.sock.close()


def test_headers_come_first_and_each_sentence_is_one_chunk(stream_server):
    raw = Raw(stream_server.port, _request())
    try:
        status, headers = raw.head()
        assert status == "HTTP/1.1 200 OK"
        assert headers["content-type"] == "audio/L16;rate=24000;channels=1"
        assert headers["transfer-encoding"] == "chunked"
        assert headers["connection"] == "close"
        assert "content-length" not in headers
        assert urllib.parse.unquote(headers["x-jarvis-summary"]) == SUMMARY
        assert headers["x-jarvis-session-id"] == "session-123"
        # The timing so far: the agent has answered, nothing is rendered yet (S3).
        names = [part.split("=")[0] for part in headers["x-jarvis-timing"].split(";")]
        assert names == ["read", "stt", "agent", "total"]
        assert raw.chunk() == b"The answer to your question is four."
        assert raw.chunk() == b"Nothing else in the repository changed!"
        assert raw.chunk() == b""                      # the terminating chunk
        assert raw.rest() == b""                       # and the connection closes
    finally:
        raw.close()
    assert stream_server.speech.calls == [(SUMMARY, False)]


def test_the_first_sentence_is_sent_before_the_second_is_rendered(stream_server):
    speech = stream_server.speech
    speech.release.clear()
    raw = Raw(stream_server.port, _request())
    try:
        raw.head()
        assert raw.chunk() == b"The answer to your question is four."
        assert speech.rendered == ["The answer to your question is four."]   # the second waits
        speech.release.set()
        assert raw.chunk() == b"Nothing else in the repository changed!"
        assert raw.chunk() == b""
    finally:
        speech.release.set()
        raw.close()


def test_a_rendering_error_mid_stream_cuts_the_body(stream_server, caplog):
    caplog.set_level(logging.INFO, logger="bridge")
    stream_server.speech.fail_at = 1
    raw = Raw(stream_server.port, _request())
    try:
        status, _ = raw.head()
        assert status == "HTTP/1.1 200 OK"
        assert raw.chunk() == b"The answer to your question is four."
        # No terminating chunk and no error status after the headers: closed.
        assert raw.rest() == b""
    finally:
        raw.close()
    lines = timing_lines(caplog, 1)
    assert len(lines) == 1 and "reply=stream_cut" in lines[0]
    assert "first_audio_ms=" in lines[0]


def test_a_rendering_error_before_the_first_sentence_sends_headers_and_no_audio(stream_server):
    stream_server.speech.fail_at = 0
    raw = Raw(stream_server.port, _request())
    try:
        status, headers = raw.head()
        assert status == "HTTP/1.1 200 OK"
        assert raw.rest() == b""
    finally:
        raw.close()


def test_http_client_sees_a_cut_body_as_incomplete(stream_server):
    stream_server.speech.fail_at = 1
    conn = http.client.HTTPConnection("127.0.0.1", stream_server.port, timeout=5)
    conn.request("POST", "/turn?project=demo", body=b"fake-audio",
                 headers={"Authorization": "Bearer test-token", "Accept": STREAM_ACCEPT})
    response = conn.getresponse()
    assert response.status == 200
    with pytest.raises(http.client.IncompleteRead):
        response.read()
    conn.close()


def test_http_client_reads_a_whole_stream(stream_server):
    conn = http.client.HTTPConnection("127.0.0.1", stream_server.port, timeout=5)
    conn.request("POST", "/turn?project=demo", body=b"fake-audio",
                 headers={"Authorization": "Bearer test-token", "Accept": STREAM_ACCEPT})
    response = conn.getresponse()
    assert response.read() == b"The answer to your question is four.Nothing else in the repository changed!"
    conn.close()


@pytest.mark.parametrize("accept", [None, "*/*", "audio/wav"])
def test_a_client_that_does_not_ask_gets_the_wav_unchanged(stream_server, accept):
    raw = Raw(stream_server.port, _request(accept=accept))
    try:
        status, headers = raw.head()
        body = raw.rest()
    finally:
        raw.close()
    assert status == "HTTP/1.0 200 OK"
    assert headers["content-type"] == "audio/wav"
    assert headers["content-length"] == str(len(body))
    assert "transfer-encoding" not in headers
    assert body == b"FAKE-WAV-" + SUMMARY.encode()
    assert stream_server.speech.calls == []


def test_an_http_1_0_client_gets_the_wav_even_when_it_asks(stream_server):
    raw = Raw(stream_server.port, _request(version="HTTP/1.0"))
    try:
        status, headers = raw.head()
        body = raw.rest()
    finally:
        raw.close()
    assert headers["content-type"] == "audio/wav"
    assert body == b"FAKE-WAV-" + SUMMARY.encode()


def test_the_switch_gives_every_client_the_wav(stream_server):
    import dataclasses
    stream_server.httpd.shutdown()
    stream_server.httpd.server_close()
    stream_server.thread.join(timeout=5)
    stream_server.start(dataclasses.replace(stream_server.config, stream_replies=False))
    raw = Raw(stream_server.port, _request())
    try:
        _, headers = raw.head()
        body = raw.rest()
    finally:
        raw.close()
    assert headers["content-type"] == "audio/wav"
    assert body == b"FAKE-WAV-" + SUMMARY.encode()


def test_silence_is_streamed_too(stream_server, monkeypatch):
    monkeypatch.setattr(stt, "transcribe", lambda audio_bytes, model_size: "  ")
    raw = Raw(stream_server.port, _request())
    try:
        _, headers = raw.head()
        assert headers["content-type"] == "audio/L16;rate=24000;channels=1"
        assert raw.chunk() == server.NOT_HEARD.encode()
        assert raw.chunk() == b""
    finally:
        raw.close()


def test_the_log_line_has_the_time_to_the_first_audio_byte(stream_server, caplog):
    caplog.set_level(logging.INFO, logger="bridge")
    conn = http.client.HTTPConnection("127.0.0.1", stream_server.port, timeout=5)
    conn.request("POST", "/turn?project=demo", body=b"fake-audio",
                 headers={"Authorization": "Bearer test-token", "Accept": STREAM_ACCEPT})
    conn.getresponse().read()
    conn.close()
    timing_lines(caplog, 1)                       # in this order: streamed, then WAV
    conn = http.client.HTTPConnection("127.0.0.1", stream_server.port, timeout=5)
    conn.request("POST", "/turn?project=demo", body=b"fake-audio",
                 headers={"Authorization": "Bearer test-token"})
    conn.getresponse().read()
    conn.close()
    lines = timing_lines(caplog, 2)
    assert len(lines) == 2
    streamed, wav = lines
    for field in ("tts_ms=", "write_ms=", "first_audio_ms=", "total_ms=",
                  f"spoken_chars={len(SUMMARY)}"):
        assert field in streamed and field in wav
    assert "reply=stream " in streamed and "reply=wav " in wav
    # A few bytes of fake audio: well under a tenth of a second at 24 kHz.
    assert "audio_out_s=0.0" in streamed
    assert SUMMARY not in streamed


def test_the_switch_reads_0_1_or_unset(monkeypatch):
    from bridge import config
    from bridge.config import ConfigError
    monkeypatch.setenv("JARVIS_BIND_HOST", "100.64.0.1")
    monkeypatch.setenv("JARVIS_TOKEN", "secret")
    monkeypatch.setenv("JARVIS_PROJECTS", "demo=/tmp")
    monkeypatch.delenv("JARVIS_STREAM_REPLIES", raising=False)
    assert config.load_config().stream_replies is True
    monkeypatch.setenv("JARVIS_STREAM_REPLIES", "0")
    assert config.load_config().stream_replies is False
    monkeypatch.setenv("JARVIS_STREAM_REPLIES", "1")
    assert config.load_config().stream_replies is True
    monkeypatch.setenv("JARVIS_STREAM_REPLIES", "off")
    with pytest.raises(ConfigError):
        config.load_config()


def test_only_the_first_audio_byte_counts():
    from bridge.timing import TurnTimer
    now = [10.0]
    timer = TurnTimer(lambda: now[0])
    now[0] = 12.5
    timer.mark_first_audio()
    now[0] = 14.0
    timer.mark_first_audio()
    timer.reply = "stream"
    assert timer.first_audio_ms == 2500
    assert timer.log_fields() == "first_audio_ms=2500 total_ms=4000 reply=stream"


# --- full mode: read-backs (S5, S6) ---------------------------------------------------

def _say(bridge, heard, *, accept=STREAM_ACCEPT):
    bridge.heard = heard
    conn = http.client.HTTPConnection("127.0.0.1", bridge.httpd.server_address[1], timeout=30)
    headers = {"Authorization": "Bearer test-token"}
    if accept:
        headers["Accept"] = accept
    conn.request("POST", "/turn?project=demo", body=b"audio", headers=headers)
    response = conn.getresponse()
    try:
        data = response.read()
    except http.client.IncompleteRead as exc:
        data = exc.partial
    conn.close()
    return response, data


@pytest.fixture
def full(tmp_path, monkeypatch):
    b = Bridge(tmp_path, monkeypatch)
    b.voice = Speech()
    monkeypatch.setattr(tts, "stream_pcm", b.voice)
    yield b
    b.stop()


def test_a_read_back_is_streamed_whole_and_exactly_as_built(full):
    full.host.scenario(mode="wrapper", gate=True)
    response, data = _say(full, "merge the jarvis pull request")
    assert response.getheader("Content-Type") == STREAM_ACCEPT
    assert response.getheader("X-Jarvis-Needs-Confirmation") == "1"
    assert data == READBACK.encode()
    assert full.voice.calls == [(READBACK, True)]   # one call, Kokoro's own split
    # Rendered before the headers: the header's timing already has the speech.
    assert "tts=" in response.getheader("X-Jarvis-Timing")

    full.host.scenario(mode="wrapper")
    response, data = _say(full, "Yes.")
    assert data == b"Merged."
    assert full.kinds() == ["agent", "confirmed"]


def test_a_streamed_read_back_whose_rendering_fails_is_a_500_and_cancelled(full):
    full.host.scenario(mode="wrapper", gate=True)
    full.voice.fail_at = 0
    response, _ = _say(full, "merge it")
    assert response.status == 500
    assert full.kinds() == ["agent", "cancel"]
    full.voice.fail_at = None
    full.host.scenario(mode="wrapper")
    _say(full, "yes")
    assert full.host.requests()[-1]["confirm"] is None


def test_a_streamed_read_back_that_breaks_off_is_cancelled(full, monkeypatch):
    full.host.scenario(mode="wrapper", gate=True)
    monkeypatch.setattr(server.Handler, "_respond_stream", lambda self, *a, **k: False)
    try:
        _say(full, "merge it")
    except Exception:  # noqa: BLE001 - no reply at all reaches this client
        pass
    assert full.kinds() == ["agent", "cancel"]


def test_an_ordinary_full_reply_is_split_and_a_cut_leaves_nothing_pending(full):
    full.voice.fail_at = 0
    response, data = _say(full, "what is open?")
    assert response.status == 200 and data == b""
    assert full.voice.calls == [("Done on the agent host.", False)]   # not whole
    assert full.kinds() == ["agent"]                                   # no cancel needed


# --- cancelling a read-back the phone did not play to the end (S6) ----------------------

def _cancel(bridge, confirmation_id, *, project="demo", token="test-token"):
    conn = http.client.HTTPConnection("127.0.0.1", bridge.httpd.server_address[1], timeout=30)
    query = urllib.parse.urlencode({k: v for k, v in
                                    {"project": project, "id": confirmation_id}.items() if v})
    headers = {"Content-Length": "0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn.request("POST", "/turn/cancel?" + query, headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response, data


def _read_back(full):
    full.host.scenario(mode="wrapper", gate=True)
    response, _ = _say(full, "merge the jarvis pull request")
    confirmation_id = response.getheader("X-Jarvis-Confirmation-Id")
    assert response.getheader("X-Jarvis-Needs-Confirmation") == "1" and confirmation_id
    full.host.scenario(mode="wrapper")
    _wait_pending(confirmation_id)
    return confirmation_id


def _wait_pending(confirmation_id):
    """delivered() keeps the read-back after its last byte is written, on the handler's
    thread: a client can have read it all just before. Wait until it is kept."""
    import time
    pending = server.Handler.full._pending._pending
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        entry = pending.get("demo")
        if entry is not None and entry[0].id == confirmation_id:
            return
        time.sleep(0.01)
    raise AssertionError("the read-back was never kept")


def test_a_read_back_the_phone_stopped_is_cancelled_and_a_yes_confirms_nothing(full):
    confirmation_id = _read_back(full)
    response, data = _cancel(full, confirmation_id)
    assert response.status == 200 and json.loads(data) == {"cancelled": True}
    assert full.kinds() == ["agent", "cancel"]
    assert full.host.requests()[-1]["confirm"]["answer"] == "no"
    _say(full, "yes")
    assert full.host.requests()[-1]["confirm"] is None     # an ordinary utterance
    assert "confirmed" not in full.kinds()


def test_cancelling_twice_changes_nothing_the_second_time(full):
    confirmation_id = _read_back(full)
    _cancel(full, confirmation_id)
    response, data = _cancel(full, confirmation_id)
    assert response.status == 200 and json.loads(data) == {"cancelled": False}
    assert full.kinds() == ["agent", "cancel"]


def test_a_cancel_with_another_id_leaves_the_read_back_pending(full):
    _read_back(full)
    response, data = _cancel(full, "not-this-one")
    assert response.status == 200 and json.loads(data) == {"cancelled": False}
    _say(full, "yes")
    assert full.kinds() == ["agent", "confirmed"]


def test_a_cancel_after_the_yes_changes_nothing(full):
    confirmation_id = _read_back(full)
    _say(full, "yes")
    response, data = _cancel(full, confirmation_id)
    assert json.loads(data) == {"cancelled": False}
    assert full.kinds() == ["agent", "confirmed"]


def test_a_cancel_that_comes_while_the_read_back_is_written_still_cancels(full, monkeypatch):
    real = server.Handler._respond_stream

    def cancel_first(self, *args, **kwargs):
        # The phone stopped before the bridge finished writing: its cancel arrives
        # before delivered() could keep the read-back.
        assert self.full.cancel_read_back(
            self.config.project_for_turn("demo"), kwargs["confirmation_id"]) is False
        return real(self, *args, **kwargs)
    monkeypatch.setattr(server.Handler, "_respond_stream", cancel_first)
    full.host.scenario(mode="wrapper", gate=True)
    _say(full, "merge the jarvis pull request")
    full.host.scenario(mode="wrapper")
    # delivered() runs after the last byte is written, on the handler's thread.
    import time
    deadline = time.monotonic() + 5
    while full.kinds() != ["agent", "cancel"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert full.kinds() == ["agent", "cancel"]
    _say(full, "yes")
    assert "confirmed" not in full.kinds()


def test_the_cancel_route_needs_the_token(full):
    confirmation_id = _read_back(full)
    response, _ = _cancel(full, confirmation_id, token=None)
    assert response.status == 401
    response, _ = _cancel(full, confirmation_id, token="wrong")
    assert response.status == 401
    assert full.kinds() == ["agent"]


@pytest.mark.parametrize("project, confirmation_id", [
    ("demo", None), (None, "abc"), ("demo", "has space"), ("demo", "x" * 65), ("../etc", "abc")])
def test_the_cancel_route_refuses_a_bad_request(full, project, confirmation_id):
    response, _ = _cancel(full, confirmation_id, project=project)
    assert response.status == 400


def test_the_wav_reply_of_a_read_back_carries_its_id_too(full):
    full.host.scenario(mode="wrapper", gate=True)
    response, _ = _say(full, "merge it", accept=None)
    assert response.getheader("Content-Type") == "audio/wav"
    confirmation_id = response.getheader("X-Jarvis-Confirmation-Id")
    assert confirmation_id
    full.host.scenario(mode="wrapper")
    _wait_pending(confirmation_id)
    _, data = _cancel(full, confirmation_id)
    assert json.loads(data) == {"cancelled": True}


def test_an_ordinary_reply_has_no_confirmation_id(full):
    response, _ = _say(full, "what is open?")
    assert response.getheader("X-Jarvis-Confirmation-Id") is None


def test_readonly_mode_has_nothing_to_cancel(stream_server):
    conn = http.client.HTTPConnection("127.0.0.1", stream_server.port, timeout=5)
    conn.request("POST", "/turn/cancel?project=demo&id=abc",
                 headers={"Authorization": "Bearer test-token", "Content-Length": "0"})
    response = conn.getresponse()
    assert response.status == 200 and json.loads(response.read()) == {"cancelled": False}
    conn.close()
