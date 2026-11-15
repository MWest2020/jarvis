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


def test_draining_a_trickled_body_stops_at_the_total_deadline(running_server, monkeypatch):
    # Slowloris-style: a byte every 100 ms keeps every single read well inside the
    # socket timeout, so only the total deadline can end the drain (jarvis#15).
    import socket
    import time
    monkeypatch.setattr(server, "DRAIN_DEADLINE_S", 1.0)
    head = (b"POST /turn?project=demo HTTP/1.1\r\nHost: x\r\n"
            b"Authorization: Bearer wrong\r\nContent-Length: 5000000\r\n\r\n")
    reply = b""
    sent = 0
    with socket.create_connection(("127.0.0.1", running_server), timeout=10) as s:
        s.sendall(head)
        started = time.monotonic()
        s.settimeout(0.1)
        while time.monotonic() - started < 10:
            try:
                s.sendall(b"x")
                sent += 1
            except OSError:
                break
            try:
                chunk = s.recv(4096)   # doubles as the 100 ms pause
            except TimeoutError:
                continue
            except OSError:
                break
            reply += chunk
            if not chunk or b"\r\n\r\n" in reply:
                break
        elapsed = time.monotonic() - started
    # Not before the deadline (the trickle was still being read), and not long after.
    assert elapsed >= 1.0
    assert elapsed < 3.0
    assert sent >= 5
    assert b" 401 " in reply.split(b"\r\n")[0]
    assert b"connection: close" in reply.lower()


@pytest.mark.parametrize("value", ["+5", "-1", "5_0", "0x10", "1.0", "\u0665", "9" * 5000])
def test_content_length_must_be_plain_digits(value):
    assert server._parse_content_length(value) is None


def test_content_length_plain_digits_and_absent():
    assert server._parse_content_length("0") == 0
    assert server._parse_content_length("0010") == 10
    assert server._parse_content_length(None) == 0


def test_rejects_a_signed_content_length(running_server):
    reply = _raw(running_server, b"POST /turn?project=demo HTTP/1.1\r\nHost: x\r\n"
                 b"Authorization: Bearer test-token\r\nContent-Length: +5\r\n\r\nhello")
    assert b" 400 " in reply.split(b"\r\n")[0]


# jarvis#15: at most MAX_CONNECTIONS connections are served at once.

@pytest.fixture
def capped_server(monkeypatch, request):
    # The cap is read when the server is made, so set it before running_server starts.
    monkeypatch.setattr(server, "MAX_CONNECTIONS", 2)
    return request.getfixturevalue("running_server")


def _wait_for_status(port, wanted, *, within=5.0):
    import time
    deadline = time.monotonic() + within
    status = None
    while time.monotonic() < deadline:
        status = _get(port)[0].status
        if status == wanted:
            return status
        time.sleep(0.05)
    return status


def test_a_connection_over_the_cap_gets_a_quick_503(capped_server):
    import socket
    import time
    # Two clients that connect and say nothing hold both slots.
    idle = [socket.create_connection(("127.0.0.1", capped_server), timeout=5) for _ in range(2)]
    try:
        assert _wait_for_status(capped_server, 503) == 503
        started = time.monotonic()
        with socket.create_connection(("127.0.0.1", capped_server), timeout=5) as s:
            reply = b""
            while chunk := s.recv(4096):   # nothing was sent: the 503 does not wait for a request
                reply += chunk
        assert time.monotonic() - started < 1.0
        head, _, body = reply.partition(b"\r\n\r\n")
        assert head.startswith(b"HTTP/1.1 503 ")
        assert b"connection: close" in head.lower()
        assert b"retry-after:" in head.lower()
        assert body == server.BUSY_MESSAGE
    finally:
        for s in idle:
            s.close()
    # The slots come back when those connections close.
    assert _wait_for_status(capped_server, 200) == 200


def test_many_connections_over_the_cap_do_not_queue(capped_server):
    import socket
    idle = [socket.create_connection(("127.0.0.1", capped_server), timeout=5) for _ in range(2)]
    try:
        assert _wait_for_status(capped_server, 503) == 503
        for _ in range(20):
            assert _get(capped_server)[0].status == 503
    finally:
        for s in idle:
            s.close()
    assert _wait_for_status(capped_server, 200) == 200
    # More requests than slots, one after another: each gives its slot back. A slot
    # is freed just after the client sees the close, hence the short wait.
    for _ in range(10):
        assert _wait_for_status(capped_server, 200) == 200


# add-project-list: GET /projects, behind the same gate as /turn.

def _get(port, path="/projects", *, token="test-token"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    conn.request("GET", path, headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response, data


def test_lists_the_projects_as_sorted_names(tmp_path):
    import json
    config = BridgeConfig(
        bind_host="127.0.0.1", port=0, token="test-token",
        projects={
            "zeta": ProjectConfig(name="zeta", working_dir=tmp_path / "z"),
            "alpha": ProjectConfig(name="alpha", working_dir=tmp_path / "a"),
        },
        allowed_tools=("Read",), session_store_path=tmp_path / "s.json",
        log_path=tmp_path / "b.log", whisper_model="base", kokoro_voice="af_heart",
        claude_timeout_s=30,
    )
    httpd = server.make_server(config)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        response, data = _get(httpd.server_address[1])
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert response.status == 200
    assert response.getheader("Content-Type") == "application/json"
    assert json.loads(data) == {"projects": ["alpha", "zeta"]}
    assert str(tmp_path).encode() not in data   # names only, never paths


def test_the_project_list_needs_the_token(running_server):
    response, data = _get(running_server, token=None)
    assert response.status == 401
    assert b"demo" not in data


def test_the_project_list_refuses_a_wrong_token(running_server):
    response, data = _get(running_server, token="wrong")
    assert response.status == 401
    assert b"demo" not in data


def test_the_project_list_with_a_non_ascii_token_is_a_plain_401(running_server):
    reply = _raw(running_server, "GET /projects HTTP/1.1\r\nHost: x\r\n"
                 "Authorization: Bearer ték\r\n\r\n".encode("latin-1"))
    assert b" 401 " in reply.split(b"\r\n")[0]


def test_a_get_on_another_path_is_404(running_server):
    response, _ = _get(running_server, "/turn")
    assert response.status == 404
    response, _ = _get(running_server, "/projects/../etc")
    assert response.status == 404


def test_post_to_the_project_list_is_404(running_server):
    conn = http.client.HTTPConnection("127.0.0.1", running_server, timeout=5)
    conn.request("POST", "/projects", body=b"x",
                 headers={"Authorization": "Bearer test-token", "Content-Length": "1"})
    assert conn.getresponse().status == 404
    conn.close()


def test_a_refused_project_list_with_a_large_body_gets_the_401(running_server):
    import socket
    head = (f"GET /projects HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer wrong\r\n"
            f"Content-Length: {len(BIG_BODY)}\r\n\r\n").encode()
    with socket.create_connection(("127.0.0.1", running_server), timeout=10) as s:
        s.sendall(head + BIG_BODY)
        reply = s.recv(4096)
    assert b" 401 " in reply.split(b"\r\n")[0]


def test_an_authorized_project_list_with_a_body_still_answers(running_server):
    import json
    import socket
    head = (b"GET /projects HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer test-token\r\n"
            b"Content-Length: 5\r\n\r\nhello")
    with socket.create_connection(("127.0.0.1", running_server), timeout=10) as s:
        s.sendall(head)
        reply = b""
        while chunk := s.recv(4096):
            reply += chunk
    status, _, body = reply.partition(b"\r\n\r\n")
    assert b" 200 " in status.split(b"\r\n")[0]
    assert json.loads(body) == {"projects": ["demo"]}


def test_the_project_list_follows_the_root_with_a_wildcard(tmp_path):
    import json
    from bridge.config import ProjectScanner
    root = tmp_path / "work"
    (root / "one" / ".git").mkdir(parents=True)
    clock = [0.0]
    config = BridgeConfig(
        bind_host="127.0.0.1", port=0, token="test-token", projects={},
        allowed_tools=("Read",), session_store_path=tmp_path / "s.json",
        log_path=tmp_path / "b.log", whisper_model="base", kokoro_voice="af_heart",
        claude_timeout_s=30,
        project_scanner=ProjectScanner(root, ttl_s=10, clock=lambda: clock[0]),
    )
    httpd = server.make_server(config)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        assert json.loads(_get(port)[1]) == {"projects": ["one"]}
        (root / "two" / ".git").mkdir(parents=True)
        assert json.loads(_get(port)[1]) == {"projects": ["one"]}      # cached
        clock[0] = 11.0
        assert json.loads(_get(port)[1]) == {"projects": ["one", "two"]}
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
