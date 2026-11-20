"""Egress of `full` turns, stage 0: observe only (jarvis-credential-proxy).

The observing proxy against real sockets on loopback, its log, the summary in
the turn record, the `[egress]` configuration, and what the wrapper adds to a
turn's environment. Nothing here reaches the internet: every upstream is a
server of the test's own on 127.0.0.1.
"""
import http.server
import json
import socket
import ssl
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_host import classify, egress
from agent_host import rules as rules_mod
from agent_host.config import ConfigError
from agent_host.wrapper import handle_request
from tests.test_agent_host_wrapper import _config, _ok_payload, _popen, _raw


# --- configuration ---------------------------------------------------------------------


def test_egress_is_off_unless_switched_on():
    assert egress.parse_config(None) == egress.EgressConfig(mode="off")


def test_observe_with_the_defaults():
    cfg = egress.parse_config({"mode": "observe"})
    assert (cfg.mode, cfg.proxy_host, cfg.proxy_port) == ("observe", "127.0.0.1", 3128)
    assert cfg.log_dir == Path("/var/log/jarvis-egress")
    assert cfg.proxy_url == "http://127.0.0.1:3128"


def test_an_ipv6_loopback_proxy():
    assert egress.parse_config({"mode": "observe", "proxy": "[::1]:8899"}).proxy_url \
        == "http://[::1]:8899"


@pytest.mark.parametrize("table, words", [
    ({"mode": "enforce"}, "not built"),
    ({"mode": "on"}, "mode must be one of"),
    ({"mode": "observe", "proxy": "10.0.0.5:3128"}, "loopback only"),
    ({"mode": "observe", "proxy": "localhost:3128"}, "not an IP address"),
    ({"mode": "observe", "proxy": "127.0.0.1"}, "address:port"),
    ({"mode": "observe", "proxy": "127.0.0.1:0"}, "not a port"),
    ({"mode": "observe", "log_dir": "logs"}, "absolute"),
    ({"mode": "observe", "allow": []}, "unknown key"),
])
def test_what_the_egress_table_refuses(table, words):
    with pytest.raises(egress.EgressConfigError, match=words):
        egress.parse_config(table)


def test_the_agent_host_configuration_carries_it(tmp_path):
    assert _config(tmp_path).egress.mode == "off"
    assert _config(tmp_path, egress={"mode": "observe"}).egress.mode == "observe"
    with pytest.raises(ConfigError, match="not built"):
        _config(tmp_path, egress={"mode": "enforce"})


def test_the_template_names_it_as_dotted_keys_and_leaves_it_off():
    text = (Path(__file__).resolve().parent.parent / "agent_host" / "config.example.toml").read_text()
    assert '# egress.mode = "observe"' in text
    assert "\negress" not in text and "\n[egress]" not in text


# --- the proxy -------------------------------------------------------------------------


def start_proxy(log_dir, **kwargs):
    log_dir.mkdir(exist_ok=True)
    server = egress.ObserveProxy(("127.0.0.1", 0), egress.EventLog(log_dir), **kwargs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def proxy(tmp_path):
    """A proxy whose upstreams may be on loopback: every upstream here is."""
    log_dir = tmp_path / "log"
    server = start_proxy(log_dir, refuse_loopback=False)
    yield server, log_dir
    server.shutdown()
    server.server_close()


@pytest.fixture
def strict_proxy(tmp_path):
    """A proxy as installed: loopback and unspecified addresses refused."""
    log_dir = tmp_path / "strict-log"
    server = start_proxy(log_dir)
    yield server, log_dir
    server.shutdown()
    server.server_close()


def events(log_dir, wait_for=1):
    deadline = time.monotonic() + 5
    while True:
        lines = []
        for path in sorted(log_dir.glob("events-*.jsonl")):
            lines += [json.loads(line) for line in path.read_text().splitlines()]
        if len(lines) >= wait_for or time.monotonic() > deadline:
            return lines
        time.sleep(0.02)


@pytest.fixture
def echo_server():
    """A TCP server on loopback that answers every chunk with b"echo:" + chunk."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    received = []

    def run():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            with conn:
                while True:
                    data = conn.recv(65536)
                    if not data:
                        break
                    received.append(data)
                    conn.sendall(b"echo:" + data)

    threading.Thread(target=run, daemon=True).start()
    yield listener.getsockname()[1], received
    listener.close()


def connect_through(proxy_port, target):
    sock = socket.create_connection(("127.0.0.1", proxy_port), timeout=5)
    sock.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(1)
        if not chunk:
            break
        head += chunk
    return sock, head.decode()


def client_hello(name):
    context = ssl.create_default_context()
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    tls = context.wrap_bio(incoming, outgoing, server_hostname=name)
    with pytest.raises(ssl.SSLWantReadError):
        tls.do_handshake()
    return outgoing.read()


def test_a_connect_is_passed_through_and_logged(proxy, echo_server):
    server, log_dir = proxy
    port, _ = echo_server
    sock, head = connect_through(server.server_address[1], f"127.0.0.1:{port}")
    with sock:
        assert head.startswith("HTTP/1.1 200")
        sock.sendall(b"hello")
        assert sock.recv(100) == b"echo:hello"
    (event,) = events(log_dir)
    assert event["method"] == "CONNECT"
    assert event["origin"] == f"https://127.0.0.1:{port}"
    assert event["decision"] == "observe" and event["status"] == 200
    assert event["private"] is True and event["address"] == "127.0.0.1"
    assert event["credential_injected"] is False
    assert event["sni"] is None and event["sni_matches"] is None
    datetime.fromisoformat(event["time"])


def test_the_tls_server_name_is_logged_and_compared_never_acted_on(proxy, echo_server):
    server, log_dir = proxy
    port, received = echo_server
    hello = client_hello("example.org")
    sock, head = connect_through(server.server_address[1], f"127.0.0.1:{port}")
    with sock:
        sock.sendall(hello)
        assert sock.recv(65536).startswith(b"echo:")
    (event,) = events(log_dir)
    assert event["sni"] == "example.org"
    assert event["sni_matches"] is False      # the CONNECT named 127.0.0.1
    assert received[0] == hello               # passed on byte for byte


def test_server_name_reads_a_real_client_hello_and_nothing_else():
    assert egress.server_name(client_hello("api.github.com")) == "api.github.com"
    assert egress.server_name(b"GET / HTTP/1.1\r\n\r\n") is None
    assert egress.server_name(client_hello("api.github.com")[:40]) is None
    assert egress.server_name(b"") is None


def test_a_plain_http_request_is_forwarded_and_logged_without_its_query(proxy):
    server, log_dir = proxy
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib name
            seen.append((self.path, self.headers.get("Connection")))
            body = b"hello"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    upstream = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    try:
        port = upstream.server_address[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": f"http://127.0.0.1:{server.server_address[1]}"}))
        with opener.open(f"http://127.0.0.1:{port}/a/b?token=secret", timeout=5) as resp:
            assert resp.read() == b"hello"
    finally:
        upstream.shutdown()
        upstream.server_close()
    assert seen == [("/a/b?token=secret", "close")]
    (event,) = events(log_dir)
    assert (event["method"], event["path"], event["status"]) == ("GET", "/a/b", 200)
    assert event["origin"] == f"http://127.0.0.1:{port}"
    assert "secret" not in json.dumps(event)


def test_an_upstream_that_does_not_answer_is_a_502_and_logged(proxy):
    server, log_dir = proxy
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    sock, head = connect_through(server.server_address[1], f"127.0.0.1:{port}")
    sock.close()
    assert head.startswith("HTTP/1.1 502")
    (event,) = events(log_dir)
    assert event["decision"] == "error" and event["status"] == 502
    assert event["reason"].startswith("connect failed")


def test_a_bad_target_is_refused_and_logged(proxy):
    server, log_dir = proxy
    sock, head = connect_through(server.server_address[1], "not a host:443")
    sock.close()
    assert head.startswith("HTTP/1.1 400")
    assert events(log_dir)[0]["decision"] == "error"


def test_a_probe_that_connects_and_leaves_is_not_logged(proxy):
    server, log_dir = proxy
    socket.create_connection(server.server_address, timeout=2).close()
    time.sleep(0.2)
    assert events(log_dir, wait_for=0) == []


def test_old_log_files_are_pruned(tmp_path):
    old = tmp_path / "events-2000-01-01.jsonl"
    old.write_text("{}\n")
    keep = tmp_path / "notes.txt"
    keep.write_text("")
    egress.EventLog(tmp_path, retention_days=30).write({"method": "CONNECT"})
    assert not old.exists() and keep.exists()
    (today,) = tmp_path.glob("events-2*.jsonl")
    assert today.stat().st_mode & 0o007 == 0      # nothing for others (design E11)


# --- the summary in the turn record ------------------------------------------------------


def _event(when, origin, **fields):
    return json.dumps({"time": datetime.fromtimestamp(when, timezone.utc).isoformat(),
                       "origin": origin, "decision": "observe", **fields})


def test_the_summary_counts_only_the_turns_window(tmp_path):
    now = time.time()
    cfg = egress.EgressConfig(mode="observe", log_dir=tmp_path)
    path = egress.log_path(tmp_path, datetime.now(timezone.utc))
    path.write_text("\n".join([
        _event(now - 100, "https://before.example"),
        _event(now - 5, "https://api.anthropic.com"),
        _event(now - 4, "https://api.anthropic.com"),
        _event(now - 3, "https://github.com", private=False, sni_matches=False),
        _event(now - 2, "https://10.0.0.1:8443", private=True, decision="error"),
        "not json",
        _event(now + 100, "https://after.example"),
    ]) + "\n")
    summary = egress.summarise(cfg, now - 10, now)
    assert summary["requests"] == 4
    assert summary["origins"] == {"https://api.anthropic.com": 2, "https://github.com": 1,
                                  "https://10.0.0.1:8443": 1}
    assert (summary["failed"], summary["private"], summary["sni_mismatch"]) == (1, 1, 1)


def test_a_missing_log_is_an_empty_summary(tmp_path):
    cfg = egress.EgressConfig(mode="observe", log_dir=tmp_path / "nowhere")
    assert egress.summarise(cfg, time.time() - 5, time.time())["requests"] == 0


# --- the wrapper -----------------------------------------------------------------------


@pytest.fixture
def listening():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    yield sock.getsockname()[1]
    sock.close()


def _records(tmp_path):
    return [json.loads(p.read_text()) for p in (tmp_path / "state" / "records").glob("*.json")]


def test_with_egress_off_a_turn_is_unchanged(tmp_path, monkeypatch):
    P = _popen(stdout=_ok_payload("SUMMARY: four"))
    monkeypatch.setattr("agent_host.wrapper.subprocess.Popen", P)
    handle_request(_raw(), _config(tmp_path))
    env = P.last.kwargs["env"]
    assert not any("proxy" in name.lower() for name in env)
    assert _records(tmp_path)[0]["egress"] is None


def test_observe_points_the_turn_at_the_proxy_and_records_it(tmp_path, monkeypatch,
                                                              strict_proxy):
    server, log_dir = strict_proxy
    listening = server.server_address[1]
    P = _popen(stdout=_ok_payload("SUMMARY: four"))
    monkeypatch.setattr("agent_host.wrapper.subprocess.Popen", P)
    config = _config(tmp_path, egress={"mode": "observe", "proxy": f"127.0.0.1:{listening}",
                                       "log_dir": str(log_dir)})
    handle_request(_raw(), config)
    env = P.last.kwargs["env"]
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        assert env[name] == f"http://127.0.0.1:{listening}"
    assert env["NO_PROXY"] == env["no_proxy"] == "localhost,127.0.0.1,::1"
    assert env["JARVIS_TURN"] == "1"
    (rec,) = _records(tmp_path)
    assert rec["egress"]["mode"] == "observe" and rec["egress"]["proxy"] == "up"
    assert rec["egress"]["requests"] == 0     # the probe is never logged


def test_a_proxy_that_is_down_costs_nothing_and_is_recorded(tmp_path, monkeypatch):
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    P = _popen(stdout=_ok_payload("SUMMARY: four"))
    monkeypatch.setattr("agent_host.wrapper.subprocess.Popen", P)
    reply = handle_request(_raw(), _config(tmp_path, egress={"mode": "observe",
                                                             "proxy": f"127.0.0.1:{port}"}))
    assert reply["status"] == "ok"
    assert not any("proxy" in name.lower() for name in P.last.kwargs["env"])
    assert _records(tmp_path)[0]["egress"] == {"mode": "observe", "proxy": "down"}


# --- the gate's class `self` -------------------------------------------------------------


def test_with_egress_on_its_unit_and_log_are_protected(tmp_path):
    rules = rules_mod.load_rules(None)
    off = classify.protected_paths(_config(tmp_path), rules, home=tmp_path / "home",
                                   lib_dir=tmp_path / "lib")
    on = classify.protected_paths(
        _config(tmp_path, egress={"mode": "observe", "log_dir": str(tmp_path / "elog")}),
        rules, home=tmp_path / "home", lib_dir=tmp_path / "lib")
    assert not off.covers(egress.UNIT_PATH)
    assert on.covers(egress.UNIT_PATH)
    assert on.covers(tmp_path / "elog" / "events-2026-11-20.jsonl")


def test_a_port_that_accepts_but_does_not_answer_is_down(tmp_path, listening):
    cfg = egress.EgressConfig(mode="observe", proxy_port=listening)
    assert egress.probe(cfg) == "down"
    assert egress.turn_environment(cfg) == ({}, {"mode": "observe", "proxy": "down"})


# --- the review of #87: bounds, failures, what never reaches the log ----------------------


def test_loopback_and_unspecified_targets_are_refused(strict_proxy, echo_server):
    server, log_dir = strict_proxy
    port, received = echo_server
    for target in (f"127.0.0.1:{port}", f"0.0.0.0:{port}", f"localhost:{port}",
                   f"[::ffff:127.0.0.1]:{port}", f"127.0.0.1:{server.server_address[1]}"):
        sock, head = connect_through(server.server_address[1], target)
        sock.close()
        assert head.startswith("HTTP/1.1 403"), target
    assert received == []
    logged = events(log_dir, wait_for=5)
    assert len(logged) == 5
    assert all(e["status"] == 403 and e["decision"] == "error" for e in logged)


def test_at_the_cap_a_connection_gets_503_and_the_log_line_is_rate_limited(tmp_path):
    log_dir = tmp_path / "log"
    server = start_proxy(log_dir, max_connections=2)
    port = server.server_address[1]
    try:
        held = [socket.create_connection(("127.0.0.1", port), timeout=5) for _ in range(2)]
        time.sleep(0.2)
        answers = []
        for _ in range(3):
            with socket.create_connection(("127.0.0.1", port), timeout=5) as extra:
                answers.append(extra.recv(100))
        assert all(a.startswith(b"HTTP/1.1 503") for a in answers)
        time.sleep(0.2)
        logged = events(log_dir, wait_for=1)
        assert len(logged) == 1 and logged[0]["reason"] == "too many connections"
        # A full proxy is not "up": the turn runs without it.
        cfg = egress.EgressConfig(mode="observe", proxy_port=port, log_dir=log_dir)
        assert egress.probe(cfg) in ("full", "down")
        assert egress.turn_environment(cfg)[0] == {}
        for sock in held:
            sock.close()
    finally:
        server.shutdown()
        server.server_close()


def test_the_probe_says_full_before_the_cap_is_reached(tmp_path):
    log_dir = tmp_path / "log"
    server = start_proxy(log_dir, max_connections=egress.PROBE_FREE_SLOTS + 2)
    port = server.server_address[1]
    cfg = egress.EgressConfig(mode="observe", proxy_port=port, log_dir=log_dir)
    try:
        assert egress.probe(cfg) == "up"
        held = [socket.create_connection(("127.0.0.1", port), timeout=5) for _ in range(3)]
        time.sleep(0.3)
        assert egress.probe(cfg) == "full"
        assert egress.turn_environment(cfg) == ({}, {"mode": "observe", "proxy": "full"})
        for sock in held:
            sock.close()
    finally:
        server.shutdown()
        server.server_close()


def test_a_slow_head_is_cut_off_at_one_total_deadline(proxy, monkeypatch):
    monkeypatch.setattr(egress, "HEAD_TIMEOUT_S", 0.6)
    server, log_dir = proxy
    started = time.monotonic()
    with socket.create_connection(server.server_address, timeout=5) as sock:
        closed = False
        for byte in b"CONNECT example.org:443 HTTP/1.1\r\nX-Slow: " + b"a" * 200:
            try:
                sock.sendall(bytes([byte]))
                sock.settimeout(0.1)
                if sock.recv(1) == b"":
                    closed = True
                    break
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                closed = True
                break
        assert closed
    assert time.monotonic() - started < 3


def test_a_log_that_cannot_be_written_never_stops_a_connection(proxy, echo_server, monkeypatch):
    server, log_dir = proxy
    port, _ = echo_server

    def full_disk(path, line):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(egress.EventLog, "_append", staticmethod(full_disk))
    sock, head = connect_through(server.server_address[1], f"127.0.0.1:{port}")
    with sock:
        assert head.startswith("HTTP/1.1 200")
        sock.sendall(b"still here")
        assert sock.recv(100) == b"echo:still here"
    assert server.log.dropped == 1
    monkeypatch.undo()
    server.log.write({"method": "CONNECT"})
    (event,) = events(log_dir)
    assert event["dropped_before"] == 1 and server.log.dropped == 0


def test_credentials_in_the_request_never_reach_the_log(proxy, echo_server):
    server, log_dir = proxy
    port, _ = echo_server
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib name
            seen.append(dict(self.headers))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    upstream = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    try:
        with socket.create_connection(server.server_address, timeout=5) as sock:
            sock.sendall(
                f"GET http://alice:userinfo-secret@127.0.0.1:{upstream.server_address[1]}/p?q=1 "
                f"HTTP/1.1\r\nHost: 127.0.0.1\r\nProxy-Authorization: Basic proxy-secret\r\n"
                f"\r\n".encode())
            assert sock.recv(100).startswith(b"HTTP/1.0 204")
        tunnel = socket.create_connection(server.server_address, timeout=5)
        tunnel.sendall(f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\n"
                       f"Proxy-Authorization: Bearer tunnel-secret\r\n\r\n".encode())
        assert tunnel.recv(100).startswith(b"HTTP/1.1 200")
        tunnel.close()
    finally:
        upstream.shutdown()
        upstream.server_close()
    assert not any("Proxy-Authorization" in headers for headers in seen)
    text = "".join(p.read_text() for p in log_dir.glob("events-*.jsonl"))
    assert len(events(log_dir, wait_for=2)) == 2
    for secret in ("userinfo-secret", "alice", "proxy-secret", "tunnel-secret", "q=1"):
        assert secret not in text
