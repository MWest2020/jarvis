"""Exercises the real HTTP server (auth, routing, session store) on loopback
with faked speech and agent calls. This is not the tailnet end-to-end test
(task 4.1) - it has no microphone, no Tailscale network and no real Claude
Code session. It only proves the server wiring itself is correct.
"""
import http.client
import threading
import urllib.parse

import pytest

from bridge import claude_adapter, server, stt, tts
from bridge.config import BridgeConfig, ProjectConfig


@pytest.fixture
def running_server(tmp_path, monkeypatch):
    config = BridgeConfig(
        bind_host="127.0.0.1",
        port=0,
        token="test-token",
        projects={"demo": ProjectConfig(name="demo", working_dir=tmp_path)},
        allowed_tools=("Read", "Glob", "Grep"),
        session_store_path=tmp_path / "sessions.json",
        log_path=tmp_path / "bridge.log",
        whisper_model="base",
        kokoro_voice="af_heart",
        claude_timeout_s=30,
    )

    monkeypatch.setattr(stt, "transcribe", lambda audio_bytes, model_size: "what is 2 plus 2")
    monkeypatch.setattr(
        claude_adapter,
        "run_turn",
        lambda **kwargs: ("4\nSPOKEN SUMMARY: The answer is four.", "session-123"),
    )
    monkeypatch.setattr(tts, "synthesize", lambda text, voice: b"FAKE-WAV-" + text.encode())

    httpd = server.make_server(config)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _post_turn(port, *, project="demo", token="test-token", body=b"fake-audio"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    path = "/turn?" + urllib.parse.urlencode({"project": project})
    headers = {"Content-Length": str(len(body))}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    conn.request("POST", path, body=body, headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response, data


def test_full_turn_returns_synthesized_audio(running_server):
    response, data = _post_turn(running_server)
    assert response.status == 200
    assert data == b"FAKE-WAV-The answer is four."
    assert response.getheader("X-Jarvis-Session-Id") == "session-123"


def test_rejects_a_missing_token(running_server):
    response, _ = _post_turn(running_server, token=None)
    assert response.status == 401


def test_rejects_a_wrong_token(running_server):
    response, _ = _post_turn(running_server, token="wrong")
    assert response.status == 401


def test_rejects_an_unknown_project(running_server):
    response, _ = _post_turn(running_server, project="does-not-exist")
    assert response.status == 400


def test_rejects_a_missing_project(running_server):
    response, data = _post_turn(running_server, project="")
    assert response.status == 400


def test_rejects_an_empty_body(running_server):
    response, _ = _post_turn(running_server, body=b"")
    assert response.status == 400


def _raw(port, payload: bytes) -> bytes:
    import socket
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(payload)
        return s.recv(4096)


def test_rejects_a_non_numeric_content_length(running_server):
    reply = _raw(running_server, b"POST /turn?project=demo HTTP/1.1\r\nHost: x\r\n"
                 b"Authorization: Bearer test-token\r\nContent-Length: abc\r\n\r\n")
    assert reply.startswith(b"HTTP/1.0 400") or reply.startswith(b"HTTP/1.1 400")


def test_rejects_a_too_large_upload_before_reading_it(running_server):
    reply = _raw(running_server, b"POST /turn?project=demo HTTP/1.1\r\nHost: x\r\n"
                 b"Authorization: Bearer test-token\r\nContent-Length: 999999999\r\n\r\n")
    assert b" 413 " in reply.split(b"\r\n")[0]


def test_a_non_ascii_token_is_a_plain_401(running_server):
    reply = _raw(running_server, "POST /turn?project=demo HTTP/1.1\r\nHost: x\r\n"
                 "Authorization: Bearer ték\r\nContent-Length: 1\r\n\r\nx".encode("latin-1"))
    assert b" 401 " in reply.split(b"\r\n")[0]


def test_silence_is_answered_without_an_agent_turn(running_server, monkeypatch):
    calls = []
    monkeypatch.setattr(stt, "transcribe", lambda audio_bytes, model_size: "  ")
    monkeypatch.setattr(claude_adapter, "run_turn", lambda **kw: calls.append(kw))
    response, data = _post_turn(running_server)
    assert response.status == 200
    assert data == b"FAKE-WAV-" + server.NOT_HEARD.encode()
    assert calls == []


def test_a_second_turn_on_a_busy_project_gets_409(running_server):
    lock = server._project_lock("demo")
    lock.acquire()
    try:
        response, _ = _post_turn(running_server)
        assert response.status == 409
    finally:
        lock.release()


# An early 4xx (bad token, unknown project) used to be sent before the body was
# read; the server then closed with the upload still arriving, the kernel reset
# the connection, and a client with a long question saw a broken pipe instead of
# the status (jarvis#12: 700 KB and up on loopback). These send the whole body
# first, the way a phone does, and only then read.
BIG_BODY = b"\0" * (4 * 1024 * 1024)


def _post_raw_then_read(port, *, project="demo", token="wrong", body=BIG_BODY) -> bytes:
    import socket
    head = (f"POST /turn?project={project} HTTP/1.1\r\nHost: x\r\n"
            f"Authorization: Bearer {token}\r\nContent-Length: {len(body)}\r\n\r\n").encode()
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(head + body)
        reply = b""
        while b"\r\n" not in reply:
            chunk = s.recv(4096)
            if not chunk:
                break
            reply += chunk
        return reply


def test_a_wrong_token_with_a_large_body_gets_the_401(running_server):
    reply = _post_raw_then_read(running_server, token="wrong")
    assert b" 401 " in reply.split(b"\r\n")[0]


def test_an_unknown_project_with_a_large_body_gets_the_400(running_server):
    reply = _post_raw_then_read(running_server, project="does-not-exist", token="test-token")
    assert b" 400 " in reply.split(b"\r\n")[0]


def test_draining_a_rejected_body_is_bounded_in_time(running_server, monkeypatch):
    # A client that announces a large body with a wrong token and then stalls
    # must not hold the thread longer than the drain deadline.
    import socket
    import time
    monkeypatch.setattr(server, "DRAIN_DEADLINE_S", 1.0)
    head = (b"POST /turn?project=demo HTTP/1.1\r\nHost: x\r\n"
            b"Authorization: Bearer wrong\r\nContent-Length: 5000000\r\n\r\n")
    with socket.create_connection(("127.0.0.1", running_server), timeout=10) as s:
        s.sendall(head + b"x" * 1000)
        started = time.monotonic()
        try:
            reply = s.recv(4096)
        except ConnectionResetError:
            reply = b""
        elapsed = time.monotonic() - started
    assert elapsed < 5
    # Best effort: the status still goes out once the deadline has passed.
    assert reply == b"" or b" 401 " in reply.split(b"\r\n")[0]
