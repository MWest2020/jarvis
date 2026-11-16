"""Configuration for the jarvis bridge service, read from the environment.

Secrets (the token) are never read from a file committed to git - only from
the environment, which operators fill from a local, gitignored `.env`.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import stat
import threading
import time
import unicodedata
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


# jarvis-full-agent. D30: two modes, `readonly` is the default and the fallback.
MODE_READONLY = "readonly"
MODE_FULL = "full"

# D36: the closed lists a whole, normalised transcript is compared with, and the
# least detected speech a recording must hold before a yes counts.
DEFAULT_CONFIRM_YES: tuple[str, ...] = ("yes", "ja")
DEFAULT_CONFIRM_NO: tuple[str, ...] = ("no", "nee")
DEFAULT_CONFIRM_MIN_SPEECH_S = 0.3

# How long the bridge waits for the agent host before it answers the phone that
# the turn is still running. The phone gives up after 180 s (the Android app's read
# timeout), and the bridge must have spoken before that, so the wait is 170 s and
# can be set no higher than 175. The turn itself may take longer on the agent host
# (its own timeout is 600 s by default, D34); the bridge only stops waiting.
DEFAULT_AGENT_TIMEOUT_S = 170
MAX_AGENT_TIMEOUT_S = 175

# What may stand in the ssh argv (D32). A host is a DNS name or an address, a user
# a Unix login; neither can start with "-" and so neither can be read as an option.
_AGENT_HOST = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|[0-9A-Fa-f:]{2,39}")
_AGENT_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}")


@dataclass(frozen=True)
class AgentHostConfig:
    """Where `full` turns run and how the bridge gets there (D32)."""

    host: str
    port: int
    user: str
    key_file: Path
    known_hosts_file: Path
    timeout_s: int


def normalise_utterance(text: str) -> str:
    """Lowered, without punctuation or symbols, single spaces (D36, step 5).

    In one Unicode form (NFKC), before and after lowering: Whisper may write "é" as
    one character and a configuration file as two, and they must compare equal.
    """
    folded = unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", text).casefold())
    kept = "".join(" " if unicodedata.category(ch)[0] in "PSZC" else ch for ch in folded)
    return " ".join(kept.split())


def _agent_mode() -> str:
    raw = os.environ.get("JARVIS_AGENT_MODE", "")
    if raw in ("", MODE_READONLY):
        return MODE_READONLY
    if raw == MODE_FULL:
        return MODE_FULL
    raise ConfigError(f"JARVIS_AGENT_MODE must be readonly or full, not {raw!r}")


def _positive_int(name: str, default: int, *, maximum: int) -> int:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if not 1 <= value <= maximum:
        raise ConfigError(f"{name}={raw!r} must be a whole number from 1 to {maximum}")
    return value


# ssh reads more than a path in an option value: it splits on spaces, expands "~",
# "%" tokens and "${VAR}", and takes quotes and backslashes as its own. None of
# these may stand in the path of the key or of the known-hosts file.
_UNSAFE_PATH_CHARS = frozenset("%$~\"'\\")


def check_private_file(path: Path, name: str) -> None:
    """The key file or the pinned host key, as it is right now.

    ConfigError when it is missing, not a regular file, gives any permission to
    group or others, or cannot be read by the bridge's own user. Checked at start
    and again before every turn (bridge.remote_adapter): a file that went away or
    was opened up since start is not used.
    """
    try:
        real = path.resolve(strict=True)
        info = real.stat()
    except OSError as exc:
        raise ConfigError(f"{name}={str(path)!r} cannot be read: {exc.strerror or exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ConfigError(f"{name}={str(path)!r} is not a regular file")
    if info.st_mode & 0o077:
        raise ConfigError(
            f"{name}={str(path)!r} has mode {stat.S_IMODE(info.st_mode):04o}: it must give "
            f"nothing to group or others (0400 or 0600)"
        )
    if not os.access(real, os.R_OK):
        raise ConfigError(f"{name}={str(path)!r} is not readable by the bridge's own user")


def _private_file(name: str, project_dirs: list[Path]) -> Path:
    """A file the ssh client is pointed at: the key, or the pinned host key.

    Refused when its path holds a character ssh would read as something else, when
    check_private_file refuses it, or when it lies inside a project directory (the
    read-only agent may read everything there, and nothing else).
    """
    raw = _require(name)
    if any(ch.isspace() or ch in _UNSAFE_PATH_CHARS for ch in raw):
        raise ConfigError(
            f"{name} must not contain whitespace or any of % $ ~ \" ' \\ "
            f"(ssh would expand or split them)"
        )
    path = Path(raw)
    if not path.is_absolute():
        raise ConfigError(f"{name}={raw!r} must be an absolute path")
    check_private_file(path, name)
    real = path.resolve()
    for directory in project_dirs:
        try:
            inside = real.is_relative_to(directory.resolve())
        except OSError:
            inside = False
        if inside or path.is_relative_to(directory):
            raise ConfigError(
                f"{name}={raw!r} lies inside the project directory {directory}: the "
                f"read-only agent could read it. Keep it outside every project."
            )
    return path


def _agent_host_from_env(projects: dict[str, ProjectConfig],
                         scanner: ProjectScanner | None) -> AgentHostConfig:
    host = _require("JARVIS_AGENT_HOST")
    if not _AGENT_HOST.fullmatch(host):
        raise ConfigError(f"JARVIS_AGENT_HOST={host!r} is not a host name or an address")
    user = _require("JARVIS_AGENT_USER")
    if not _AGENT_USER.fullmatch(user):
        raise ConfigError(f"JARVIS_AGENT_USER={user!r} is not a login name")
    project_dirs = [p.working_dir for p in projects.values()]
    if scanner is not None:
        project_dirs.append(scanner.root)
    return AgentHostConfig(
        host=host,
        port=_positive_int("JARVIS_AGENT_PORT", 22, maximum=65535),
        user=user,
        key_file=_private_file("JARVIS_AGENT_KEY_FILE", project_dirs),
        known_hosts_file=_private_file("JARVIS_AGENT_KNOWN_HOSTS_FILE", project_dirs),
        timeout_s=_positive_int("JARVIS_AGENT_TIMEOUT_S", DEFAULT_AGENT_TIMEOUT_S,
                                maximum=MAX_AGENT_TIMEOUT_S),
    )


def _word_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    # Kept in the form the transcript is compared in, however the file spelled it.
    words = tuple(unicodedata.normalize("NFKC", w.strip()) for w in raw.split(",")
                  if w.strip())
    if not words:
        raise ConfigError(f"{name} must list at least one word")
    for word in words:
        if normalise_utterance(word) != word:
            raise ConfigError(
                f"{name}: {word!r} must be written as it is compared: lower case, "
                f"no punctuation, single spaces"
            )
    return words


def _confirm_words() -> tuple[tuple[str, ...], tuple[str, ...]]:
    yes = _word_list("JARVIS_CONFIRM_YES", DEFAULT_CONFIRM_YES)
    no = _word_list("JARVIS_CONFIRM_NO", DEFAULT_CONFIRM_NO)
    both = sorted(set(yes) & set(no))
    if both:
        raise ConfigError(f"{both[0]!r} is in JARVIS_CONFIRM_YES and in JARVIS_CONFIRM_NO")
    return yes, no


def _confirm_min_speech_s() -> float:
    raw = os.environ.get("JARVIS_CONFIRM_MIN_SPEECH_S", "")
    if not raw:
        return DEFAULT_CONFIRM_MIN_SPEECH_S
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not 0.0 < value <= 10.0:   # also refuses nan
        raise ConfigError(
            f"JARVIS_CONFIRM_MIN_SPEECH_S={raw!r} must be a number of seconds above 0, "
            f"at most 10"
        )
    return value


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
    # jarvis-full-agent (D30): `readonly` unless JARVIS_AGENT_MODE=full, and then
    # `agent_host` says where the turns run.
    agent_mode: str = MODE_READONLY
    agent_host: AgentHostConfig | None = None
    confirm_yes: tuple[str, ...] = DEFAULT_CONFIRM_YES
    confirm_no: tuple[str, ...] = DEFAULT_CONFIRM_NO
    confirm_min_speech_s: float = DEFAULT_CONFIRM_MIN_SPEECH_S

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
    # jarvis-full-agent: nothing below is read unless JARVIS_AGENT_MODE=full.
    full: dict = {}
    if _agent_mode() == MODE_FULL:
        confirm_yes, confirm_no = _confirm_words()
        full = {
            "agent_mode": MODE_FULL,
            "agent_host": _agent_host_from_env(projects, scanner),
            "confirm_yes": confirm_yes,
            "confirm_no": confirm_no,
            "confirm_min_speech_s": _confirm_min_speech_s(),
        }

    return BridgeConfig(
        bind_host=bind_host,
        port=port,
        token=token,
        projects=projects,
        allowed_tools=READ_ONLY_ALLOWED_TOOLS,
        session_store_path=state_dir / "sessions.json",
        log_path=state_dir / "bridge.log",
        whisper_model=os.environ.get("JARVIS_WHISPER_MODEL", "base"),
        kokoro_voice=os.environ.get("JARVIS_KOKORO_VOICE", "af_heart"),
        claude_timeout_s=int(os.environ.get("JARVIS_CLAUDE_TIMEOUT_S", "120")),
        behind_tailscale_proxy=behind_proxy,
        project_scanner=scanner,
        **full,
    )
