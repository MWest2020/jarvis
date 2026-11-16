"""The checkout settings rule (1.7d, D36 "The checkout's own settings").

A project's `.claude/settings.json` and `settings.local.json` are loaded by
Claude Code, and they can carry `env`, `hooks` and `disableAllHooks`. The
grant of a confirmed call lives in the process environment (1b2), and an
`env` entry in project settings is added to that same environment; `hooks`
and `disableAllHooks` can change or stop the gate itself. Such a file needs
no `Edit` call to appear: a `git pull` or a `gh pr checkout` brings it.

One function answers the question, for everyone who asks it:

- the wrapper, before every turn and again after it (`unsafe_settings`);
- the gate, on every call, for the turn's checkout and for the git top
  level of the directory the call itself reports.

A file that exists and cannot be read as a JSON object is unsafe: nobody
can say what Claude Code would make of it. A file that is not there is
safe. None of this depends on whether or when Claude Code re-reads its
settings.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

UNSAFE_KEYS: tuple[str, ...] = ("env", "hooks", "disableAllHooks")
SETTINGS_FILES: tuple[str, ...] = ("settings.json", "settings.local.json")


def file_problem(path: Path) -> str | None:
    """Why this settings file is unsafe, or None when it is safe or absent."""
    if not os.path.lexists(path):
        return None
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return f"{path.name} cannot be read"
    if not isinstance(data, dict):
        return f"{path.name} is not a settings object"
    for key in UNSAFE_KEYS:
        if key in data:
            return f"{path.name} sets {key}"
    return None


def directory_problem(directory: Path) -> str | None:
    """The first problem in `<directory>/.claude/settings*.json`, or None."""
    claude_dir = directory / ".claude"
    for name in SETTINGS_FILES:
        problem = file_problem(claude_dir / name)
        if problem is not None:
            return problem
    return None


def git_top_level(cwd: Path) -> Path | None:
    """The nearest directory at or above `cwd` with a `.git` entry (a
    directory in a clone, a file in a worktree). No `git` is run: the answer
    must not depend on a configuration the agent can write."""
    current = Path(os.path.realpath(cwd))
    for candidate in (current, *current.parents):
        if os.path.lexists(candidate / ".git"):
            return candidate
    return None


def problem(
    turn_checkout: Path, cwd: Path | None = None, *, user_settings_path: Path | None = None,
) -> str | None:
    """Why a gated call must not run now, or None.

    `turn_checkout` is the directory the turn was started in; `cwd` is the
    working directory a call reports (the agent's own worktree, often not
    the turn checkout). `user_settings_path` is the user's own Claude Code
    settings: a home directory that is itself a git checkout must not make
    the user's settings count as a project's.
    """
    directories = [Path(os.path.realpath(turn_checkout))]
    if cwd is not None:
        top = git_top_level(cwd)
        if top is not None and top not in directories:
            directories.append(top)
    user_dir = None
    if user_settings_path is not None:
        user_dir = os.path.realpath(user_settings_path.parent)
    for directory in directories:
        if user_dir is not None and os.path.realpath(directory / ".claude") == user_dir:
            continue
        found = directory_problem(directory)
        if found is not None:
            return found
    return None
