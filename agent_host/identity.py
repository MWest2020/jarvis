"""The child process environment for one turn (1.1, 1.13, D33, D37).

Starts from nothing: not the wrapper's own `os.environ` (so nothing a
client sent over ssh - `LANG`, `LC_*` - and nothing a shell rc file could
plant - `BASH_ENV`, `ENV` - reaches the child), only the root-owned
environment file plus the turn variables this module adds. No
`GIT_AUTHOR_*`, `GIT_COMMITTER_*` or `user.email`: commits carry the host's
global git identity, exactly as the coding session's do.

The gate (agent_host/gate, 1b1) is a hook: a separate process that Claude
Code starts, with this environment. What it needs and cannot derive is put
here by the wrapper: where the configuration is (`JARVIS_AGENT_CONFIG`) and
which checkout the turn was started in (`JARVIS_TURN_CHECKOUT`). Both are
set after the environment file is read, so the file cannot override them.

The grant of a confirmed call (`JARVIS_GRANT`, `JARVIS_GRANT_BASE`, D36) is
never built here: the wrapper adds it to a confirming turn only, and this
module removes both names from whatever the environment file said, so an
ordinary turn can never start with a grant.
"""
from __future__ import annotations

from pathlib import Path


def read_environment_file(path: Path) -> dict[str, str]:
    try:
        text = path.read_text()
    except OSError:
        return {}
    env: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def build_child_env(
    environment_file: Path,
    *,
    identity_env_var: str,
    session_identity: str,
    turn_id: str,
    config_path: Path,
    checkout: Path,
) -> dict[str, str]:
    env = read_environment_file(environment_file)
    env[identity_env_var] = session_identity
    env["JARVIS_TURN"] = "1"
    env["JARVIS_TURN_ID"] = turn_id
    env["JARVIS_AGENT_CONFIG"] = str(config_path)
    env["JARVIS_TURN_CHECKOUT"] = str(checkout)
    env.pop("JARVIS_GRANT", None)
    env.pop("JARVIS_GRANT_BASE", None)
    return env
