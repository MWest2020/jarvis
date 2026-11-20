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

from agent_host.boundedio import read_regular
from agent_host.command import EFFORT_LEVELS, MODEL_NAME
from agent_host.egress import EgressConfig, EgressConfigError
from agent_host.egress import parse_config as parse_egress


class ConfigError(RuntimeError):
    """Raised when the agent host is misconfigured."""


DEFAULT_CONFIG_PATH = Path("/etc/jarvis-agent/config.toml")
# The gate reads this on every call: never without a bound (host check 12).
MAX_CONFIG_BYTES = 1024 * 1024

DEFAULT_MEMORY_FLOOR_MB = 600
DEFAULT_TIMEOUT_S = 600
DEFAULT_IDENTITY_ENV_VAR = "IDENT"
DEFAULT_RULES_DIR = Path("/etc/jarvis-agent/rules.d")
# Where Claude Code looks for managed settings on Linux. A configuration key,
# so that no path of anyone's own setup is a constant (the class `self`, D36).
DEFAULT_MANAGED_SETTINGS_PATH = Path("/etc/claude-code/managed-settings.json")
# The confirmation path (1b2, D36/D38).
DEFAULT_CONFIRM_PROGRAM = Path("/usr/local/lib/jarvis-agent/confirm")
DEFAULT_PR_STATUS_COMMAND = ("gh", "pr", "view")
DEFAULT_RETENTION_DAYS = 90
# A turn after this many minutes without one starts a new session (change
# jarvis-session-lifetime): the prompt cache lives an hour. 0: never.
DEFAULT_SESSION_IDLE_MINUTES = 60
# After a confirmed `gh repo create` ran (jarvis-general-conversation D53).
DEFAULT_CLONE_COMMAND = ("gh", "repo", "clone")
DEFAULT_CLONE_TIMEOUT_S = 45
MAX_CLONE_TIMEOUT_S = 90
PENDING_SECONDS = 60
# The classes a confirmation can be switched off for. `self` is not one of
# them: it stays desk-only whatever the switch says.
CONFIRM_CLASSES = frozenset({
    "merge", "deploy", "delete", "force", "api", "account", "message",
    "create", "outward", "root", "tool",
})


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
    # Voice confirmation (1b2, D36). "off" lets gated calls through
    # unconfirmed; `confirm_skip` does that for single classes.
    confirm: str = "on"
    confirm_skip: frozenset[str] = frozenset()
    # The root-owned `confirm on|off [class]` program: the one way to change
    # the two settings above by voice.
    confirm_program: Path = DEFAULT_CONFIRM_PROGRAM
    # Run by the wrapper after a confirmed call ran, with the read-back as
    # its last argument. Empty: no notification.
    notify_command: tuple[str, ...] = ()
    # Asked for a merge's review decision and check roll-up; the wrapper adds
    # `<number> --repo <owner>/<name> --json ...`.
    pr_status_command: tuple[str, ...] = DEFAULT_PR_STATUS_COMMAND
    # How long records and action logs are kept (D38).
    retention_days: int = DEFAULT_RETENTION_DAYS
    # Run by the wrapper after a confirmed `gh repo create <owner>/<name>`
    # ran: it adds `<owner>/<name> <destination>` (D53).
    clone_command: tuple[str, ...] = DEFAULT_CLONE_COMMAND
    clone_timeout_s: int = DEFAULT_CLONE_TIMEOUT_S
    # The voice profile (voice-and-speed): `--model` and `--effort` of every
    # turn. None: not on the line, Claude Code's own choice from the user's
    # settings, as before.
    voice_model: str | None = None
    voice_effort: str | None = None
    # A project's next turn starts a new session when its last turn ended
    # more than this many minutes ago (agent_host.lifetime). 0: always resume.
    session_idle_minutes: int = DEFAULT_SESSION_IDLE_MINUTES
    # Egress of turns (jarvis-credential-proxy): `mode = "off"` by default;
    # "observe" points a turn's environment at the observing proxy.
    egress: EgressConfig = field(default_factory=EgressConfig)

    @property
    def voice_profile(self) -> dict:
        """The configured parts of the voice profile, for the turn record."""
        profile = {"model": self.voice_model, "effort": self.voice_effort}
        return {key: value for key, value in profile.items() if value is not None}

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "turn.lock"

    @property
    def sessions_dir(self) -> Path:
        return self.state_dir / "sessions"

    @property
    def records_dir(self) -> Path:
        return self.state_dir / "records"

    @property
    def gate_dir(self) -> Path:
        """What the gate noted per turn (agent_host.pending)."""
        return self.state_dir / "gate"

    @property
    def pending_dir(self) -> Path:
        """The one pending call per project, between read-back and answer."""
        return self.state_dir / "pending"

    @property
    def actions_dir(self) -> Path:
        """The action log: one file of lines per turn (agent_host/action-log)."""
        return self.state_dir / "actions"


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


def _path(value: object, key: str) -> Path:
    """A path from the configuration: absolute, as written. No `~` and no
    environment is expanded: the gate reads this file in a process whose
    `HOME` a project's settings could have set, so `~` would name a
    directory of the project's choosing."""
    text = str(value)
    if not os.path.isabs(text):
        raise ConfigError(f"{key} = {text!r} must be an absolute path (no ~)")
    return Path(text)


def _require(data: dict, key: str) -> object:
    if key not in data:
        raise ConfigError(f"{key} is required in the agent host configuration")
    return data[key]


def _string_list(data: dict, key: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    value = data.get(key, list(default))
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ConfigError(f"{key} must be a list of non-empty strings")
    return tuple(value)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AgentHostConfig:
    try:
        raw = read_regular(path, MAX_CONFIG_BYTES)
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
        projects = {name: _path(p, f"projects.{name}") for name, p in projects_raw.items()}
    else:
        projects_root = _path(projects_root_raw, "projects_root")

    exclusions = frozenset(str(x) for x in data.get("project_exclusions", []))

    state_dir = _path(_require(data, "state_dir"), "state_dir")
    environment_file = _path(_require(data, "environment_file"), "environment_file")
    turn_settings_path = _path(_require(data, "turn_settings"), "turn_settings")
    prompt_path = _path(_require(data, "prompt_file"), "prompt_file")
    session_identity = str(_require(data, "session_identity"))
    user_settings_path = _path(_require(data, "user_settings_path"), "user_settings_path")

    second_prompt_raw = data.get("second_prompt_file")
    second_prompt_path = _path(second_prompt_raw, "second_prompt_file") if second_prompt_raw else None

    own_owners_raw = data.get("own_owners", [])
    if not isinstance(own_owners_raw, list) or not all(
        isinstance(owner, str) and owner for owner in own_owners_raw
    ):
        raise ConfigError("own_owners must be a list of owner names")

    confirm = data.get("confirm", "on")
    if confirm not in ("on", "off"):
        raise ConfigError('confirm must be "on" or "off"')
    confirm_skip = _string_list(data, "confirm_skip")
    unknown = set(confirm_skip) - CONFIRM_CLASSES
    if unknown:
        raise ConfigError(f"confirm_skip: unknown class(es) {sorted(unknown)}")
    pr_status_command = _string_list(data, "pr_status_command", DEFAULT_PR_STATUS_COMMAND)
    if not pr_status_command:
        raise ConfigError("pr_status_command must not be empty")
    retention_days = data.get("retention_days", DEFAULT_RETENTION_DAYS)
    if not isinstance(retention_days, int) or isinstance(retention_days, bool) or retention_days < 1:
        raise ConfigError("retention_days must be a whole number of days, at least 1")
    clone_command = _string_list(data, "clone_command", DEFAULT_CLONE_COMMAND)
    if not clone_command:
        raise ConfigError("clone_command must not be empty")
    clone_timeout_s = data.get("clone_timeout_s", DEFAULT_CLONE_TIMEOUT_S)
    if not isinstance(clone_timeout_s, int) or isinstance(clone_timeout_s, bool) \
            or not 1 <= clone_timeout_s <= MAX_CLONE_TIMEOUT_S:
        raise ConfigError(f"clone_timeout_s must be a whole number of seconds, 1 to "
                          f"{MAX_CLONE_TIMEOUT_S}")
    voice_model = data.get("voice_model")
    if voice_model is not None and (not isinstance(voice_model, str)
                                    or not MODEL_NAME.fullmatch(voice_model)):
        raise ConfigError("voice_model must be a model alias or name, e.g. \"sonnet\"")
    voice_effort = data.get("voice_effort")
    if voice_effort is not None and voice_effort not in EFFORT_LEVELS:
        raise ConfigError(f"voice_effort must be one of {', '.join(EFFORT_LEVELS)}")
    session_idle_minutes = data.get("session_idle_minutes", DEFAULT_SESSION_IDLE_MINUTES)
    if not isinstance(session_idle_minutes, int) or isinstance(session_idle_minutes, bool) \
            or session_idle_minutes < 0:
        raise ConfigError("session_idle_minutes must be a whole number of minutes, 0 or more "
                          "(0: never start a new session for being idle)")
    try:
        egress = parse_egress(data.get("egress"))
    except EgressConfigError as exc:
        raise ConfigError(str(exc)) from exc

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
        rules_dir=_path(data.get("rules_dir", DEFAULT_RULES_DIR), "rules_dir"),
        managed_settings_path=_path(
            data.get("managed_settings_path", DEFAULT_MANAGED_SETTINGS_PATH),
            "managed_settings_path"),
        own_owners=tuple(own_owners_raw),
        confirm=confirm,
        confirm_skip=frozenset(confirm_skip),
        confirm_program=_path(data.get("confirm_program", DEFAULT_CONFIRM_PROGRAM),
                              "confirm_program"),
        notify_command=_string_list(data, "notify_command"),
        pr_status_command=pr_status_command,
        retention_days=retention_days,
        clone_command=clone_command,
        clone_timeout_s=clone_timeout_s,
        voice_model=voice_model,
        voice_effort=voice_effort,
        session_idle_minutes=session_idle_minutes,
        egress=egress,
    )
