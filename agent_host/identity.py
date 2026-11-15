"""The child process environment for one turn (1.1, 1.13, D33, D37).

Starts from nothing: not the wrapper's own `os.environ` (so nothing a
client sent over ssh - `LANG`, `LC_*` - and nothing a shell rc file could
plant - `BASH_ENV`, `ENV` - reaches the child), only the root-owned
environment file plus the turn variables this module adds. No
`GIT_AUTHOR_*`, `GIT_COMMITTER_*` or `user.email`: commits carry the host's
global git identity, exactly as the coding session's do.
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
) -> dict[str, str]:
    env = read_environment_file(environment_file)
    env[identity_env_var] = session_identity
    env["JARVIS_TURN"] = "1"
    env["JARVIS_TURN_ID"] = turn_id
    return env
