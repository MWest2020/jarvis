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
