"""Egress of `full` turns, stage 0: observe only (change jarvis-credential-proxy).

Stage 0 of the rollout (proposal, "Rollout"; design E3, E5, E11): a small
proxy on loopback, under its own system user, that passes every connection
through by name and writes one JSON line per request. The wrapper points a
turn's environment at it (`HTTPS_PROXY` and friends) when
`egress.mode = "observe"`. **Nothing is enforced and nothing is denied**:
there is no allow-list yet, no firewall, no TLS interception and no
credential. A week of voice turns gives the real list for stage 1.

Three parts, one module, Python standard library only:

- `EgressConfig` and `parse_config`: the `[egress]` table of `config.toml`.
  `mode = "off"` (the default) changes nothing; `"observe"` is stage 0;
  `"enforce"` is refused until stage 1 is built.
- `turn_environment`: what the wrapper adds to the environment of the
  `claude` process of one turn, and what goes into the turn record. When the
  proxy does not answer at the start of the turn, nothing is added (the turn
  runs exactly as with `"off"`) and the record says so: observing must never
  cost a turn.
- `serve`: the proxy itself. `CONNECT host:port` is answered, the address
  resolved by the proxy, and bytes relayed; the TLS server name is read from
  the client's first bytes and logged, never acted on. A plain-HTTP request
  in absolute form is forwarded with `Connection: close`. Every connection
  is one line in a log file per day (`events-YYYY-MM-DD.jsonl`), in the
  `Event` shape of the external-sandbox protocol (time, method, origin, path
  without query, status, decision, reason, duration, credential injected),
  plus what stage 1 needs to know: the resolved address, whether it is a
  private one, and the server name. Never a body, never a header.

The wrapper reads the log after a turn and adds a summary to the turn
record (`summarise`). Turns are one at a time (`turn.lock`) and only a turn's
environment points at the proxy, so the requests between the turn's start
and end are the turn's.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import selectors
import socket
import socketserver
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

MODES = ("off", "observe", "enforce")
DEFAULT_MODE = "off"
DEFAULT_PROXY = "127.0.0.1:3128"
DEFAULT_LOG_DIR = Path("/var/log/jarvis-egress")
# Where the installer puts the unit (agent_host/jarvis-egress.service). A
# write to it is in the gate's class `self` (design E12).
UNIT_PATH = Path("/etc/systemd/system/jarvis-egress.service")
# Loopback names a turn reaches directly, never through the proxy.
NO_PROXY = "localhost,127.0.0.1,::1"
# The variables a turn gets with `"observe"`, always all six, and pinned in the
# turn's settings so that a checkout's `env` cannot change them (task 0.13).
PROXY_NAMES = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy")
# The wait for the proxy at the start of a turn: it is on loopback.
PROBE_TIMEOUT_S = 0.3

# The proxy's own bounds.
MAX_HEAD_BYTES = 64 * 1024
# One deadline for the whole request head, not per read: a client that sends
# a byte now and then cannot hold a slot.
HEAD_TIMEOUT_S = 10.0
CONNECT_TIMEOUT_S = 10.0
# How long the proxy waits for a client's first bytes after `CONNECT`, to
# read the TLS server name. A TLS client sends at once; a protocol where the
# server speaks first waits this long once.
SNI_WAIT_S = 2.0
# A line with no byte either way for this long is closed: the turn's own
# timeout (timeout_s, 600 by default).
IDLE_TIMEOUT_S = 600.0
MAX_CONNECTIONS = 256
# The wrapper's probe gets "200" only while at least this many slots are free
# besides its own; otherwise the proxy counts as full and the turn runs
# without it.
PROBE_FREE_SLOTS = 16
PROBE_PATH = "/.jarvis-egress/health"
# At most one log line per this many seconds for connections refused at the
# cap; the next one says how many were not logged.
OVER_CAP_LOG_INTERVAL_S = 60.0
# The summary in a turn record reads at most this much of a day's log, and
# names at most this many origins.
MAX_SUMMARY_READ_BYTES = 4 * 1024 * 1024
MAX_SUMMARY_ORIGINS = 20

_HOST = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})(?:\.[A-Za-z0-9-]{1,63})*\.?\Z")
_HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-connection", "proxy-authorization", "te",
    "trailer", "upgrade",
})


class EgressConfigError(ValueError):
    """The `[egress]` table is not valid."""


@dataclass(frozen=True)
class EgressConfig:
    mode: str = DEFAULT_MODE
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 3128
    log_dir: Path = DEFAULT_LOG_DIR

    @property
    def proxy_url(self) -> str:
        host = f"[{self.proxy_host}]" if ":" in self.proxy_host else self.proxy_host
        return f"http://{host}:{self.proxy_port}"


def _split_address(text: str) -> tuple[str, int]:
    """`127.0.0.1:3128` or `[::1]:3128`: a loopback address and a port."""
    if text.startswith("["):
        host, sep, rest = text[1:].partition("]")
        if not sep or not rest.startswith(":"):
            raise EgressConfigError(f"egress.proxy = {text!r}: write [address]:port")
        port_text = rest[1:]
    else:
        host, sep, port_text = text.rpartition(":")
        if not sep:
            raise EgressConfigError(f"egress.proxy = {text!r}: write address:port")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise EgressConfigError(f"egress.proxy = {text!r}: not an IP address") from exc
    if not address.is_loopback:
        raise EgressConfigError(f"egress.proxy = {text!r}: the proxy listens on loopback only")
    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise EgressConfigError(f"egress.proxy = {text!r}: not a port")
    return address.compressed, int(port_text)


def parse_config(data: object) -> EgressConfig:
    """The `[egress]` table (or nothing, which is `mode = "off"`)."""
    if data is None:
        return EgressConfig()
    if not isinstance(data, dict):
        raise EgressConfigError("egress must be a table")
    unknown = set(data) - {"mode", "proxy", "log_dir"}
    if unknown:
        raise EgressConfigError(f"egress: unknown key(s) {sorted(unknown)}")
    mode = data.get("mode", DEFAULT_MODE)
    if mode not in MODES:
        raise EgressConfigError(f"egress.mode must be one of {', '.join(MODES)}")
    if mode == "enforce":
        raise EgressConfigError(
            'egress.mode = "enforce" is stage 1 of jarvis-credential-proxy and is not built yet; '
            'use "observe" or "off"')
    proxy = data.get("proxy", DEFAULT_PROXY)
    if not isinstance(proxy, str):
        raise EgressConfigError("egress.proxy must be a string, address:port")
    host, port = _split_address(proxy)
    log_dir = data.get("log_dir", str(DEFAULT_LOG_DIR))
    if not isinstance(log_dir, str) or not os.path.isabs(log_dir):
        raise EgressConfigError("egress.log_dir must be an absolute path (no ~)")
    return EgressConfig(mode=mode, proxy_host=host, proxy_port=port, log_dir=Path(log_dir))


# --- the wrapper's side ---------------------------------------------------------------


def probe(config: EgressConfig, timeout: float = PROBE_TIMEOUT_S) -> str:
    """"up", "full" or "down": a real request to the proxy and its answer,
    within one deadline. "up" only when the proxy says it has free slots."""
    deadline = time.monotonic() + timeout
    try:
        with socket.create_connection((config.proxy_host, config.proxy_port),
                                      timeout=timeout) as sock:
            sock.sendall(f"GET {PROBE_PATH} HTTP/1.1\r\nHost: jarvis-egress\r\n\r\n".encode())
            answer = b""
            while b"\r\n" not in answer and len(answer) < 1024:
                left = deadline - time.monotonic()
                if left <= 0:
                    return "down"
                sock.settimeout(left)
                chunk = sock.recv(1024)
                if not chunk:
                    break
                answer += chunk
    except OSError:
        return "down"
    if answer.startswith(b"HTTP/1.1 200"):
        return "up"
    if answer.startswith(b"HTTP/1.1 503"):
        return "full"
    return "down"


def turn_environment(config: EgressConfig) -> tuple[dict[str, str], dict | None]:
    """(what to add to the turn's environment, the record's `egress` field).

    `"off"`: nothing, and no field. `"observe"`: always the six
    `PROXY_NAMES`, which the wrapper also pins in the turn's settings
    (`pinned_settings`). With a proxy that answers its probe with free slots
    they name the proxy, in both spellings (curl reads `http_proxy` only in
    lower case, Node and Go read either). With a proxy that does not answer,
    or is full, they are empty, which every client reads as "no proxy": the
    turn runs as with `"off"`, and the record says `"proxy": "down"` or
    `"full"`. This is decided once, at the start: a proxy that dies in the
    middle of a turn fails that turn's connections."""
    if config.mode != "observe":
        return {}, None
    state = probe(config)
    if state != "up":
        return dict.fromkeys(PROXY_NAMES, ""), {"mode": "observe", "proxy": state}
    url = config.proxy_url
    env = {}
    for name in ("HTTPS_PROXY", "HTTP_PROXY"):
        env[name] = env[name.lower()] = url
    env["NO_PROXY"] = env["no_proxy"] = NO_PROXY
    return env, {"mode": "observe", "proxy": "up"}


def pinned_settings(turn_settings: dict, pins: dict[str, str]) -> str:
    """The turn settings with `pins` added to their `env`, as the one JSON
    string `--settings` takes in place of a path (task 0.13).

    `env` in `--settings` outranks a checkout's `env` (host-checked for
    `PATH` and `HOME`), so a checkout cannot point the turn's programs at
    another proxy, or past it with `NO_PROXY=*`, in the middle of a turn.
    The string travels in argv, which the turn cannot change; it holds no
    secret. The pinned values are also in the `claude` process's own
    environment, so the gate's comparison (exec-time environment plus the
    file's pins) expects exactly what the hooks get."""
    if not isinstance(turn_settings, dict):
        raise ValueError("the turn settings are not a JSON object")
    env = turn_settings.get("env", {})
    if not isinstance(env, dict):
        raise ValueError("the turn settings' env is not a table")
    merged = {**turn_settings, "env": {**env, **pins}}
    return json.dumps(merged, sort_keys=True, separators=(",", ":"))


def log_path(log_dir: Path, day: datetime) -> Path:
    return log_dir / f"events-{day.strftime('%Y-%m-%d')}.jsonl"


def _read_tail(path: Path, cap: int) -> list[str]:
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not (info.st_mode & 0o170000 == 0o100000):
            raise OSError(f"{path} is not a regular file")
        start = max(0, info.st_size - cap)
        os.lseek(fd, start, os.SEEK_SET)
        chunks, size = [], 0
        while size < cap:
            chunk = os.read(fd, min(65536, cap - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    finally:
        os.close(fd)
    lines = b"".join(chunks).decode("utf-8", "replace").splitlines()
    return lines[1:] if start > 0 else lines


def summarise(config: EgressConfig, started: float, ended: float) -> dict:
    """The requests the proxy logged between `started` and `ended` (epoch
    seconds): counts and the origins, for the turn record. Never raises: a
    log that cannot be read is said, not fatal."""
    begin = datetime.fromtimestamp(started, timezone.utc)
    end = datetime.fromtimestamp(ended, timezone.utc)
    days, day = [], begin.date()
    while day <= end.date():
        days.append(day)
        day += timedelta(days=1)
    origins: Counter[str] = Counter()
    statuses = {"requests": 0, "failed": 0, "private": 0, "sni_mismatch": 0}
    try:
        for each in days:
            path = log_path(config.log_dir, datetime(each.year, each.month, each.day))
            try:
                lines = _read_tail(path, MAX_SUMMARY_READ_BYTES)
            except FileNotFoundError:
                continue
            for line in lines:
                try:
                    event = json.loads(line)
                    when = datetime.fromisoformat(event["time"]).timestamp()
                except (ValueError, KeyError, TypeError):
                    continue
                if not started <= when <= ended:
                    continue
                statuses["requests"] += 1
                origins[str(event.get("origin"))] += 1
                if event.get("decision") == "error":
                    statuses["failed"] += 1
                if event.get("private") is True:
                    statuses["private"] += 1
                if event.get("sni_matches") is False:
                    statuses["sni_mismatch"] += 1
    except OSError as exc:
        return {"log": f"unreadable: {type(exc).__name__}"}
    summary: dict = dict(statuses)
    summary["origins"] = dict(origins.most_common(MAX_SUMMARY_ORIGINS))
    if len(origins) > MAX_SUMMARY_ORIGINS:
        summary["more_origins"] = len(origins) - MAX_SUMMARY_ORIGINS
    return summary


# --- the TLS server name ---------------------------------------------------------------


def server_name(data: bytes) -> str | None:
    """The server name of a TLS ClientHello at the start of `data`, or None.
    Reads only; a malformed or partial hello is None."""
    try:
        if len(data) < 5 or data[0] != 0x16:
            return None
        record_end = 5 + int.from_bytes(data[3:5], "big")
        body = data[5:record_end]
        if not body or body[0] != 0x01:
            return None
        pos = 4 + 2 + 32                       # handshake header, version, random
        pos += 1 + body[pos]                   # session id
        pos += 2 + int.from_bytes(body[pos:pos + 2], "big")   # cipher suites
        pos += 1 + body[pos]                   # compression methods
        ext_end = pos + 2 + int.from_bytes(body[pos:pos + 2], "big")
        pos += 2
        while pos + 4 <= min(ext_end, len(body)):
            kind = int.from_bytes(body[pos:pos + 2], "big")
            size = int.from_bytes(body[pos + 2:pos + 4], "big")
            ext = body[pos + 4:pos + 4 + size]
            pos += 4 + size
            if kind != 0:
                continue
            inner = 2
            while inner + 3 <= len(ext):
                name_type = ext[inner]
                name_len = int.from_bytes(ext[inner + 1:inner + 3], "big")
                name = ext[inner + 3:inner + 3 + name_len]
                if name_type == 0 and len(name) == name_len:
                    return name.decode("ascii").lower().rstrip(".")
                inner += 3 + name_len
            return None
    except (IndexError, UnicodeDecodeError):
        return None
    return None


# --- the proxy ------------------------------------------------------------------------


def valid_host(host: str) -> bool:
    if not host or len(host) > 253:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(_HOST.match(host))


def canonical_origin(scheme: str, host: str, port: int) -> str:
    shown = f"[{host}]" if ":" in host else host.lower().rstrip(".")
    default = {"https": 443, "http": 80}.get(scheme)
    return f"{scheme}://{shown}" if port == default else f"{scheme}://{shown}:{port}"


def is_private(address: str) -> bool:
    try:
        return not ipaddress.ip_address(address.split("%", 1)[0]).is_global
    except ValueError:
        return True


class EventLog:
    """One JSON line per request, a file per day (UTC), kept `retention_days`.
    Written by the proxy's own user; mode 0640 so the turn's user can read it
    through the log group (design E11)."""

    def __init__(self, log_dir: Path, retention_days: int = 90) -> None:
        self.log_dir = log_dir
        self.retention_days = retention_days
        self._lock = threading.Lock()
        self._pruned_day: str | None = None
        # Lines that could not be written (a full disk, a directory gone).
        # Never an error for the connection: observing must not cost one.
        self.dropped = 0

    def write(self, event: dict) -> None:
        """Never raises: a line that cannot be written is counted in
        `dropped`, said in the next line that can, and the connection goes
        on."""
        now = datetime.now(timezone.utc)
        with self._lock:
            event = {"time": now.isoformat(timespec="milliseconds"), **event}
            if self.dropped:
                event["dropped_before"] = self.dropped
            line = (json.dumps(event, sort_keys=True) + "\n").encode()
            try:
                self._append(log_path(self.log_dir, now), line)
            except OSError:
                self.dropped += 1
                return
            self.dropped = 0
            day = now.strftime("%Y-%m-%d")
            if day != self._pruned_day:
                self._pruned_day = day
                self._prune(now)

    @staticmethod
    def _append(path: Path, line: bytes) -> None:
        flags = (os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
                 | getattr(os, "O_CLOEXEC", 0))
        fd = os.open(path, flags, 0o640)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)

    def _prune(self, now: datetime) -> None:
        cutoff = (now - timedelta(days=self.retention_days)).strftime("%Y-%m-%d")
        try:
            names = os.listdir(self.log_dir)
        except OSError:
            return
        for name in names:
            match = re.fullmatch(r"events-(\d{4}-\d{2}-\d{2})\.jsonl", name)
            if match and match.group(1) < cutoff:
                try:
                    os.unlink(self.log_dir / name)
                except OSError:
                    pass


def _read_head(sock: socket.socket) -> tuple[bytes, bytes] | None:
    """(the request head without its blank line, bytes read past it), or
    None when it is not complete within one deadline of HEAD_TIMEOUT_S."""
    deadline = time.monotonic() + HEAD_TIMEOUT_S
    data = b""
    while b"\r\n\r\n" not in data:
        if len(data) > MAX_HEAD_BYTES:
            return None
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        sock.settimeout(left)
        try:
            chunk = sock.recv(65536)
        except (socket.timeout, TimeoutError):
            return None
        if not chunk:
            return None
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    return head, rest


class RefusedAddress(OSError):
    """Every address of the host is loopback or unspecified."""


def refused_address(address: str) -> bool:
    """Loopback and unspecified addresses are never connected to, as the
    proxy's own user: not the proxy itself, not another local service
    (`CONNECT 0.0.0.0:<port>` reaches a 127.0.0.1 listener). Turns reach
    loopback directly (`NO_PROXY`)."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_loopback or ip.is_unspecified


def _connect(host: str, port: int, refuse_loopback: bool = True) -> tuple[socket.socket, str]:
    """A connection to `host`, resolved here, and the address it went to."""
    last: OSError = OSError("no address")
    for family, kind, proto, _, address in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        if refuse_loopback and refused_address(address[0]):
            last = RefusedAddress("loopback or unspecified address")
            continue
        sock = socket.socket(family, kind, proto)
        sock.settimeout(CONNECT_TIMEOUT_S)
        try:
            sock.connect(address)
        except OSError as exc:
            sock.close()
            last = exc
            continue
        return sock, address[0]
    raise last


def _relay(a: socket.socket, b: socket.socket, first_from_b=None, *,
           done_when_b_ends: bool = False) -> None:
    """Copy bytes both ways until both sides are done or the line is idle.
    `first_from_b(chunk)` sees the first chunk from `b` before it is sent on.
    With `done_when_b_ends`, the end of `b` ends the relay (a forwarded HTTP
    request: the upstream was told `Connection: close`)."""
    a.setblocking(False)
    b.setblocking(False)
    sel = selectors.DefaultSelector()
    sel.register(a, selectors.EVENT_READ, b)
    sel.register(b, selectors.EVENT_READ, a)
    open_ends = 2
    try:
        while open_ends:
            ready = sel.select(IDLE_TIMEOUT_S)
            if not ready:
                return
            for key, _ in ready:
                src, dst = key.fileobj, key.data
                try:
                    chunk = src.recv(65536)
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError:
                    return
                if not chunk:
                    if src is b and done_when_b_ends:
                        return
                    sel.unregister(src)
                    open_ends -= 1
                    try:
                        dst.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    continue
                if src is b and first_from_b is not None:
                    first_from_b(chunk)
                    first_from_b = None
                dst.settimeout(IDLE_TIMEOUT_S)
                try:
                    dst.sendall(chunk)
                except OSError:
                    return
                finally:
                    dst.setblocking(False)
    finally:
        sel.close()


class _Handler(socketserver.BaseRequestHandler):
    server: "ObserveProxy"

    def handle(self) -> None:
        if not self.server.take_slot():
            try:
                self.request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            suppressed = self.server.over_cap_due()
            if suppressed is not None:
                self.server.log.write({
                    "method": None, "origin": None, "path": None, "status": 503,
                    "decision": "error", "reason": "too many connections",
                    "duration_ms": 0, "credential_injected": False,
                    "not_logged_before": suppressed})
            return
        try:
            self._handle()
        except OSError:
            pass
        finally:
            self.server.give_slot()

    def _refuse(self, status: int, reason: str, **fields) -> None:
        text = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway"}.get(status, "Error")
        try:
            self.request.sendall(
                f"HTTP/1.1 {status} {text}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode())
        except OSError:
            pass
        self.server.log.write({"status": status, "decision": "error", "reason": reason,
                               "credential_injected": False, **fields})

    def _handle(self) -> None:
        started = time.monotonic()
        try:
            got = _read_head(self.request)
        except OSError:
            return
        if got is None:
            return   # a probe that connected and left, or nothing that is HTTP
        head, rest = got
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
            self._refuse(400, "not an HTTP/1 request line", method=None, origin=None, path=None,
                         duration_ms=0)
            return
        method, target, _ = parts
        if method == "GET" and target == PROBE_PATH:
            # The wrapper's probe: answered, never logged, never forwarded.
            ok = self.server.free_slots() >= PROBE_FREE_SLOTS
            status = b"200 OK" if ok else b"503 Service Unavailable"
            try:
                self.request.sendall(b"HTTP/1.1 " + status +
                                     b"\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass
            return
        if method == "CONNECT":
            self._tunnel(target, rest, started)
        else:
            self._forward(method, target, lines[1:], rest, started)

    def _tunnel(self, target: str, rest: bytes, started: float) -> None:
        host, sep, port_text = target.rpartition(":")
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        if not sep or not port_text.isdigit() or not 1 <= int(port_text) <= 65535 \
                or not valid_host(host):
            self._refuse(400, "CONNECT target is not host:port", method="CONNECT", origin=None,
                         path=None, duration_ms=0)
            return
        port = int(port_text)
        origin = canonical_origin("https", host, port)
        try:
            upstream, address = _connect(host, port, self.server.refuse_loopback)
        except RefusedAddress as exc:
            self._refuse(403, str(exc), method="CONNECT", origin=origin, path=None,
                         duration_ms=round((time.monotonic() - started) * 1000))
            return
        except OSError as exc:
            self._refuse(502, f"connect failed: {type(exc).__name__}", method="CONNECT",
                         origin=origin, path=None,
                         duration_ms=round((time.monotonic() - started) * 1000))
            return
        try:
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            first = rest
            if not first:
                self.request.settimeout(SNI_WAIT_S)
                try:
                    first = self.request.recv(65536)
                except (socket.timeout, TimeoutError):
                    first = b""
            name = server_name(first) if first else None
            wanted = host.lower().rstrip(".")
            self.server.log.write({
                "method": "CONNECT", "origin": origin, "path": None, "status": 200,
                "decision": "observe", "reason": "observe mode: passed through by name",
                "duration_ms": round((time.monotonic() - started) * 1000),
                "credential_injected": False, "address": address,
                "private": is_private(address), "sni": name,
                "sni_matches": None if name is None else name == wanted,
            })
            if first:
                upstream.sendall(first)
            _relay(self.request, upstream)
        finally:
            upstream.close()

    def _forward(self, method: str, target: str, header_lines: list[str], rest: bytes,
                 started: float) -> None:
        parts = urlsplit(target)
        if parts.scheme != "http" or not parts.hostname or not valid_host(parts.hostname):
            self._refuse(400, "only absolute http:// requests are forwarded", method=method,
                         origin=None, path=None, duration_ms=0)
            return
        try:
            port = parts.port or 80
        except ValueError:
            self._refuse(400, "bad port", method=method, origin=None, path=None, duration_ms=0)
            return
        host = parts.hostname
        origin = canonical_origin("http", host, port)
        path = parts.path or "/"
        try:
            upstream, address = _connect(host, port, self.server.refuse_loopback)
        except RefusedAddress as exc:
            self._refuse(403, str(exc), method=method, origin=origin, path=path,
                         duration_ms=round((time.monotonic() - started) * 1000))
            return
        except OSError as exc:
            self._refuse(502, f"connect failed: {type(exc).__name__}", method=method,
                         origin=origin, path=path,
                         duration_ms=round((time.monotonic() - started) * 1000))
            return
        kept = [line for line in header_lines
                if line and line.split(":", 1)[0].strip().lower() not in _HOP_HEADERS]
        request_target = path + (f"?{parts.query}" if parts.query else "")
        head = "\r\n".join([f"{method} {request_target} HTTP/1.1", *kept, "Connection: close"])
        logged = threading.Event()

        def log_status(chunk: bytes) -> None:
            status = None
            match = re.match(rb"HTTP/1\.[01] (\d{3})", chunk)
            if match:
                status = int(match.group(1))
            self.server.log.write({
                "method": method, "origin": origin, "path": path, "status": status,
                "decision": "observe", "reason": "observe mode: forwarded",
                "duration_ms": round((time.monotonic() - started) * 1000),
                "credential_injected": False, "address": address,
                "private": is_private(address),
            })
            logged.set()

        try:
            upstream.sendall(head.encode("latin-1") + b"\r\n\r\n" + rest)
            _relay(self.request, upstream, first_from_b=log_status, done_when_b_ends=True)
            if not logged.is_set():
                log_status(b"")
        finally:
            upstream.close()


class ObserveProxy(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], log: EventLog, *,
                 max_connections: int = MAX_CONNECTIONS, refuse_loopback: bool = True) -> None:
        self.log = log
        self.max_connections = max_connections
        # Only the tests switch this off, to have an upstream on loopback.
        self.refuse_loopback = refuse_loopback
        self._active = 0
        self._slot_lock = threading.Lock()
        self._over_cap_logged = float("-inf")
        self._over_cap_suppressed = 0
        family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        self.address_family = family
        super().__init__(address, _Handler)

    def take_slot(self) -> bool:
        with self._slot_lock:
            if self._active >= self.max_connections:
                return False
            self._active += 1
            return True

    def give_slot(self) -> None:
        with self._slot_lock:
            self._active -= 1

    def free_slots(self) -> int:
        """Free slots besides the caller's own."""
        with self._slot_lock:
            return self.max_connections - self._active

    def over_cap_due(self) -> int | None:
        """How many over-cap refusals went unlogged, when a line is due now;
        None while the last line is recent (at most one a minute)."""
        with self._slot_lock:
            now = time.monotonic()
            if now - self._over_cap_logged < OVER_CAP_LOG_INTERVAL_S:
                self._over_cap_suppressed += 1
                return None
            self._over_cap_logged = now
            suppressed, self._over_cap_suppressed = self._over_cap_suppressed, 0
            return suppressed


def serve(config: EgressConfig, retention_days: int) -> None:
    config.log_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
    server = ObserveProxy((config.proxy_host, config.proxy_port),
                          EventLog(config.log_dir, retention_days))
    with server:
        server.serve_forever()
