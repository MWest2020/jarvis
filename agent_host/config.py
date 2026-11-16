"""Configuration for the agent host wrapper and gate (1a, 1b1, D33/D34/D36/D37).

Read from a root-owned TOML file (stdlib `tomllib`, no third-party
dependency). Nothing of Mark's own setup lives here as a constant: every
path, name and identity is a configuration value (AGENTS.md).
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised when the agent host is misconfigured."""


DEFAULT_CONFIG_PATH = Path("/etc/jarvis-agent/config.toml")

DEFAULT_MEMORY_FLOOR_MB = 600
DEFAULT_TIMEOUT_S = 600
DEFAULT_IDENTITY_ENV_VAR = "IDENT"
DEFAULT_RULES_DIR = Path("/etc/jarvis-agent/rules.d")
# Where Claude Code looks for managed settings on Linux. A configuration key,
# so that no path of anyone's own setup is a constant (the class `self`, D36).
DEFAULT_MANAGED_SETTINGS_PATH = Path("/etc/claude-code/managed-settings.json")


@dataclass(frozen=True)
class AgentHostConfig:
    enabled: bool
    # Either an explicit name -> path mapping, or a root with exclusions.
    # Exactly one of (projects, projects_root) is set (load_config enforces it).
    projects: dict[str, Path]
    projects_root: Path | None
    project_exclusions: frozenset[str]
    state_dir: Path
    environment_file: Path
    turn_settings_path: Path
    prompt_path: Path
    append_system_prompt_fallback: bool
    session_rotation_fallback: bool
    session_identity: str
    identity_env_var: str
    memory_floor_mb: int
    timeout_s: int
    user_settings_path: Path
    forge_host: str = "github.com"
    second_prompt_path: Path | None = field(default=None)
    # The gate (1b1, D36). `config_path` is the file this configuration was
    # read from: the gate, a separate process, is told it through the
    # turn's environment (agent_host.identity).
    config_path: Path = DEFAULT_CONFIG_PATH
    rules_dir: Path = DEFAULT_RULES_DIR
    managed_settings_path: Path = DEFAULT_MANAGED_SETTINGS_PATH
    # Whose repositories are "own" (D36): owner names on the forge host.
    own_owners: tuple[str, ...] = ()

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "turn.lock"

    @property
    def sessions_dir(self) -> Path:
        return self.state_dir / "sessions"

    @property
    def records_dir(self) -> Path:
        return self.state_dir / "records"


def hook_config_path(environ, package_dir: Path) -> Path:
    """Where the gate and the action log read their configuration.

    Installed, that is one fixed path, `DEFAULT_CONFIG_PATH`: a constant of
    the installation, never something the turn's environment can say. A
    hook is started by Claude Code with an environment that project
    settings could have touched, so `JARVIS_AGENT_CONFIG` is **ignored**.

    The one exception is a source checkout, where the tests run: there the
    variable is honoured, and "a source checkout" means that the test kit
    (`tests/gatekit.py`) lies next to the package. The installer copies the
    package and never the tests, into a root-owned directory, so an
    installed hook cannot be switched to this by anything a turn can do.
    """
    if (package_dir.parent / "tests" / "gatekit.py").is_file():
        given = environ.get("JARVIS_AGENT_CONFIG")
        if given:
            if not os.path.isabs(given):
                raise ConfigError("JARVIS_AGENT_CONFIG is not an absolute path")
            return Path(given)
    return DEFAULT_CONFIG_PATH


def _require(data: dict, key: str) -> object:
    if key not in data:
        raise ConfigError(f"{key} is required in the agent host configuration")
    return data[key]


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AgentHostConfig:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"cannot read agent host configuration {path}: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"agent host configuration {path} is not valid TOML: {exc}") from exc
    return config_from_dict(data, config_path=path)


def config_from_dict(data: dict, *, config_path: Path = DEFAULT_CONFIG_PATH) -> AgentHostConfig:
    projects_raw = data.get("projects")
    projects_root_raw = data.get("projects_root")
    if bool(projects_raw) == bool(projects_root_raw):
        raise ConfigError("exactly one of [projects] or projects_root must be set")

    projects: dict[str, Path] = {}
    projects_root: Path | None = None
    if projects_raw:
        if not isinstance(projects_raw, dict):
            raise ConfigError("projects must be a table of name = \"path\"")
        projects = {name: Path(str(p)).expanduser() for name, p in projects_raw.items()}
    else:
        projects_root = Path(str(projects_root_raw)).expanduser()
        if not projects_root.is_absolute():
            raise ConfigError(f"projects_root={projects_root_raw!r} must be an absolute path")

    exclusions = frozenset(str(x) for x in data.get("project_exclusions", []))

    state_dir = Path(str(_require(data, "state_dir"))).expanduser()
    environment_file = Path(str(_require(data, "environment_file"))).expanduser()
    turn_settings_path = Path(str(_require(data, "turn_settings"))).expanduser()
    prompt_path = Path(str(_require(data, "prompt_file"))).expanduser()
    session_identity = str(_require(data, "session_identity"))
    user_settings_path = Path(str(_require(data, "user_settings_path"))).expanduser()

    second_prompt_raw = data.get("second_prompt_file")
    second_prompt_path = Path(str(second_prompt_raw)).expanduser() if second_prompt_raw else None

    own_owners_raw = data.get("own_owners", [])
    if not isinstance(own_owners_raw, list) or not all(
        isinstance(owner, str) and owner for owner in own_owners_raw
    ):
        raise ConfigError("own_owners must be a list of owner names")

    return AgentHostConfig(
        enabled=bool(data.get("enabled", True)),
        projects=projects,
        projects_root=projects_root,
        project_exclusions=exclusions,
        state_dir=state_dir,
        environment_file=environment_file,
        turn_settings_path=turn_settings_path,
        prompt_path=prompt_path,
        append_system_prompt_fallback=bool(data.get("append_system_prompt_fallback", False)),
        session_rotation_fallback=bool(data.get("session_rotation_fallback", False)),
        session_identity=session_identity,
        identity_env_var=str(data.get("identity_env_var", DEFAULT_IDENTITY_ENV_VAR)),
        memory_floor_mb=int(data.get("memory_floor_mb", DEFAULT_MEMORY_FLOOR_MB)),
        timeout_s=int(data.get("timeout_s", DEFAULT_TIMEOUT_S)),
        user_settings_path=user_settings_path,
        forge_host=str(data.get("forge_host", "github.com")),
        second_prompt_path=second_prompt_path,
        config_path=config_path,
        rules_dir=Path(str(data.get("rules_dir", DEFAULT_RULES_DIR))).expanduser(),
        managed_settings_path=Path(
            str(data.get("managed_settings_path", DEFAULT_MANAGED_SETTINGS_PATH))
        ).expanduser(),
        own_owners=tuple(own_owners_raw),
    )
