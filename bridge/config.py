"""Configuration for the jarvis bridge service, read from the environment.

Secrets (the token) are never read from a file committed to git - only from
the environment, which operators fill from a local, gitignored `.env`.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from dataclasses import dataclass
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

    def project(self, name: str) -> ProjectConfig:
        try:
            return self.projects[name]
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


def load_config() -> BridgeConfig:
    behind_proxy = _behind_tailscale_proxy()
    bind_host = _check_private_host(_require("JARVIS_BIND_HOST"), behind_proxy=behind_proxy)
    if behind_proxy:
        # Logging is not configured yet here, so this lands on stderr (Python's
        # last-resort handler); server.main logs it again into the bridge log.
        logger.warning(BEHIND_PROXY_WARNING, bind_host)
    token = _require("JARVIS_TOKEN")
    port = int(os.environ.get("JARVIS_PORT", "8787"))
    projects = _parse_projects(_require("JARVIS_PROJECTS"))
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
        kokoro_voice=os.environ.get("JARVIS_KOKORO_VOICE", "af_heart"),
        claude_timeout_s=int(os.environ.get("JARVIS_CLAUDE_TIMEOUT_S", "120")),
        behind_tailscale_proxy=behind_proxy,
    )
