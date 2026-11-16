"""Configuration for the jarvis bridge service, read from the environment.

Secrets (the token) are never read from a file committed to git - only from
the environment, which operators fill from a local, gitignored `.env`.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# 1.3 (weekend slice): a fixed, read-only allow-list. No approval flow yet,
# so nothing that writes or executes is ever on this list (D6).
READ_ONLY_ALLOWED_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep")

logger = logging.getLogger("bridge.config")


class ConfigError(RuntimeError):
    """Raised when the bridge is misconfigured in a way that would be unsafe to run."""


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{name} is required (set it in the environment, never in git)")
    return value


# Tailscale's address ranges (CGNAT IPv4 and its IPv6 ULA prefix), plus loopback
# for local tests. Anything else is refused: Network exposure says Tailscale only.
_ALLOWED_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)


# Behind a Tailscale proxy in a cluster (run-bridge-in-cluster, D1): the bridge
# listens on its pod IP, which is private, and the Tailscale operator's proxy is the
# only thing allowed to reach it. Only these ranges, and only with the explicit opt-in.
_PROXY_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
)

BEHIND_PROXY_WARNING = (
    "JARVIS_BEHIND_TAILSCALE_PROXY=1: listening on private address %s, NOT on a "
    "Tailscale interface. This is only safe when a Tailscale proxy is the sole way in "
    "(a tailnet-only Service plus a NetworkPolicy that admits only the proxy). "
    "The token is still required on every request."
)


def _behind_tailscale_proxy() -> bool:
    raw = os.environ.get("JARVIS_BEHIND_TAILSCALE_PROXY", "")
    if raw in ("", "0"):
        return False
    if raw == "1":
        return True
    raise ConfigError(f"JARVIS_BEHIND_TAILSCALE_PROXY must be 1 or unset, not {raw!r}")


def _check_private_host(host: str, *, behind_proxy: bool = False) -> str:
    # AGENTS.md: reachable only over a private network, never public.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ConfigError(f"JARVIS_BIND_HOST={host!r} is not a valid IP address") from exc
    if ip.is_unspecified:
        raise ConfigError(
            f"JARVIS_BIND_HOST must be a specific interface address (the Tailscale IP), "
            f"not a wildcard like {host!r}. Never public, per AGENTS.md."
        )
    if any(ip in net for net in _ALLOWED_NETWORKS):
        return host
    if behind_proxy and any(ip in net for net in _PROXY_NETWORKS):
        return host
    if behind_proxy:
        raise ConfigError(
            f"JARVIS_BIND_HOST={host!r} is not a private address. Behind a Tailscale "
            f"proxy only RFC 1918 (10/8, 172.16/12, 192.168/16) and CGNAT (100.64/10) "
            f"addresses are allowed. The bridge is never served elsewhere."
        )
    raise ConfigError(
        f"JARVIS_BIND_HOST={host!r} is not a Tailscale address (100.64.0.0/10 or "
        f"fd7a:115c:a1e0::/48) or loopback. The bridge is never served elsewhere."
    )


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    working_dir: Path


# JARVIS_PROJECTS=* (add-project-list): every git checkout directly under
# JARVIS_PROJECTS_ROOT is a project, named after its directory. Hidden directories,
# symlinks and names outside this pattern are skipped, so a project name can never
# be a path ("..", "a/b") and never leads out of the root.
ALL_PROJECTS = "*"
SCAN_TTL_S = 10.0
_PROJECT_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")


class ProjectScanner:
    """The projects under a root directory, re-scanned at most every `ttl_s` seconds.

    Thread-safe: the bridge serves requests on several threads.
    """

    def __init__(self, root: Path, *, ttl_s: float = SCAN_TTL_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.root = root
        self._ttl_s = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._scanned_at: float | None = None
        self._projects: dict[str, ProjectConfig] = {}

    def projects(self) -> dict[str, ProjectConfig]:
        with self._lock:
            now = self._clock()
            if self._scanned_at is None or now - self._scanned_at >= self._ttl_s:
                self._projects = scan_projects(self.root)
                self._scanned_at = now
            return self._projects


def scan_projects(root: Path) -> dict[str, ProjectConfig]:
    """Every directory directly under `root` that is a git checkout, by directory name."""
    found: dict[str, ProjectConfig] = {}
    try:
        entries = sorted(root.iterdir())
    except OSError as exc:
        logger.warning("cannot list projects under %s: %s", root, exc)
        return found
    for entry in entries:
        name = entry.name
        if not _PROJECT_NAME.fullmatch(name):
            continue
        try:
            if entry.is_symlink() or not entry.is_dir() or not (entry / ".git").exists():
                continue
        except OSError:
            continue
        found[name] = ProjectConfig(name=name, working_dir=entry)
    return found


@dataclass(frozen=True)
class BridgeConfig:
    bind_host: str
    port: int
    token: str
    projects: dict[str, ProjectConfig]
    allowed_tools: tuple[str, ...]
    session_store_path: Path
    log_path: Path
    whisper_model: str
    kokoro_voice: str
    claude_timeout_s: int
    behind_tailscale_proxy: bool = False
    # Set with JARVIS_PROJECTS=*: the projects come from this scanner, not `projects`.
    project_scanner: ProjectScanner | None = field(default=None, compare=False)

    def current_projects(self) -> dict[str, ProjectConfig]:
        if self.project_scanner is not None:
            return self.project_scanner.projects()
        return self.projects

    def project_names(self) -> list[str]:
        return sorted(self.current_projects())

    def project(self, name: str) -> ProjectConfig:
        try:
            return self.current_projects()[name]
        except KeyError:
            raise ConfigError(f"unknown project {name!r}") from None


def _parse_projects(raw: str) -> dict[str, ProjectConfig]:
    projects: dict[str, ProjectConfig] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        name, _, path = item.partition("=")
        name, path = name.strip(), path.strip()
        if not name or not path:
            raise ConfigError(f"JARVIS_PROJECTS entry {item!r} is not name=path")
        projects[name] = ProjectConfig(name=name, working_dir=Path(path).expanduser())
    if not projects:
        raise ConfigError("JARVIS_PROJECTS must list at least one project as name=path")
    return projects


def _projects_from_env() -> tuple[dict[str, ProjectConfig], ProjectScanner | None]:
    raw = _require("JARVIS_PROJECTS")
    root_raw = os.environ.get("JARVIS_PROJECTS_ROOT", "")
    if raw.strip() != ALL_PROJECTS:
        if root_raw:
            raise ConfigError("JARVIS_PROJECTS_ROOT is only used with JARVIS_PROJECTS=*")
        return _parse_projects(raw), None
    if not root_raw:
        raise ConfigError(
            "JARVIS_PROJECTS=* needs JARVIS_PROJECTS_ROOT, the directory that holds the checkouts"
        )
    root = Path(root_raw).expanduser()
    if not root.is_absolute():
        raise ConfigError(f"JARVIS_PROJECTS_ROOT={root_raw!r} must be an absolute path")
    if not root.is_dir():
        raise ConfigError(f"JARVIS_PROJECTS_ROOT={root_raw!r} is not a directory")
    scanner = ProjectScanner(root)
    if not scanner.projects():
        logger.warning("JARVIS_PROJECTS=*: no git checkouts under %s yet", root)
    return {}, scanner


def load_config() -> BridgeConfig:
    behind_proxy = _behind_tailscale_proxy()
    bind_host = _check_private_host(_require("JARVIS_BIND_HOST"), behind_proxy=behind_proxy)
    if behind_proxy:
        # Logging is not configured yet here, so this lands on stderr (Python's
        # last-resort handler); server.main logs it again into the bridge log.
        logger.warning(BEHIND_PROXY_WARNING, bind_host)
    token = _require("JARVIS_TOKEN")
    port = int(os.environ.get("JARVIS_PORT", "8787"))
    projects, scanner = _projects_from_env()
    state_dir = Path(os.environ.get("JARVIS_STATE_DIR", "~/.jarvis")).expanduser()

    return BridgeConfig(
        bind_host=bind_host,
        port=port,
        token=token,
        projects=projects,
        allowed_tools=READ_ONLY_ALLOWED_TOOLS,
        session_store_path=state_dir / "sessions.json",
        log_path=state_dir / "bridge.log",
        whisper_model=os.environ.get("JARVIS_WHISPER_MODEL", "base"),
        kokoro_voice=os.environ.get("JARVIS_KOKORO_VOICE", "bm_george"),
        claude_timeout_s=int(os.environ.get("JARVIS_CLAUDE_TIMEOUT_S", "120")),
        behind_tailscale_proxy=behind_proxy,
        project_scanner=scanner,
    )
