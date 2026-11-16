"""The checkout settings rule (1.7d, D36 "The checkout's own settings").

A project's `.claude/settings.json` and `settings.local.json` are loaded by
Claude Code. They can carry `env` (the grant of a confirmed call lives in
the process environment, 1b2), `hooks` and `disableAllHooks` (which can
change or stop the gate itself), and a good deal more that runs a command
or opens a door: `apiKeyHelper`, `statusLine`, `awsAuthRefresh`,
`otelHeadersHelper`, `mcpServers`, `enableAllProjectMcpServers`,
`enabledMcpjsonServers`, a `permissions` block that allows things or sets a
mode. Such a file needs no `Edit` call to appear: a `git pull` or a
`gh pr checkout` brings it.

So the rule is an **allow-list**: a settings file is safe only when every
top-level key is one of a few that cannot run anything or widen anything
(`SAFE_KEYS`), and `permissions` only when it holds nothing but `deny` and
`ask`. Every other key makes the checkout unsafe, the three D36 names and
every key nobody has thought of yet.

One function answers the question, for everyone who asks it:

- the wrapper, before every turn and again after it (`unsafe_settings`);
- the gate, on every call, for the turn's checkout and for the git top
  level of the directory the call itself reports.

A file that is not there is safe. A file that is there and is not a
regular file read as a JSON object is unsafe: a symlink (in the file or in
`.claude` itself: what it points at can change without the checkout
changing), a FIFO or a device (never opened in a way that can block), a
directory, something unreadable, something too large, something that is not
JSON. None of this depends on whether or when Claude Code re-reads its
settings.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

# The three names of D36. Kept for what they are: the reason the rule
# exists. The rule itself is the allow-list below.
UNSAFE_KEYS: tuple[str, ...] = ("env", "hooks", "disableAllHooks")
SETTINGS_FILES: tuple[str, ...] = ("settings.json", "settings.local.json")
# Top-level keys that cannot run a command, set the environment, add a
# server or widen a permission. Everything else is unsafe.
SAFE_KEYS: frozenset[str] = frozenset({
    "$schema", "model", "includeCoAuthoredBy", "cleanupPeriodDays", "outputStyle",
    "permissions",
})
# `permissions` may only take away: `allow`, `defaultMode`,
# `additionalDirectories` and anything else in it widen what a turn may do.
SAFE_PERMISSION_KEYS: frozenset[str] = frozenset({"deny", "ask"})
MAX_SETTINGS_BYTES = 1024 * 1024


def _read_regular(path: Path) -> bytes:
    """The bytes of a regular file, never following a symlink and never
    blocking on a FIFO or a device. Raises OSError otherwise."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("not a regular file")
        if info.st_size > MAX_SETTINGS_BYTES:
            raise OSError("too large")
        return os.read(fd, MAX_SETTINGS_BYTES + 1)
    finally:
        os.close(fd)


def file_problem(path: Path) -> str | None:
    """Why this settings file is unsafe, or None when it is safe or absent."""
    if not os.path.lexists(path):
        return None
    if os.path.islink(path):
        return f"{path.name} is a symlink"
    try:
        data = json.loads(_read_regular(path).decode("utf-8"))
    except (OSError, ValueError):
        return f"{path.name} cannot be read"
    if not isinstance(data, dict):
        return f"{path.name} is not a settings object"
    for key in UNSAFE_KEYS:
        if key in data:
            return f"{path.name} sets {key}"
    for key in sorted(data):
        if key not in SAFE_KEYS:
            return f"{path.name} sets {key}"
    permissions = data.get("permissions")
    if permissions is not None:
        if not isinstance(permissions, dict):
            return f"{path.name} sets permissions"
        for key in sorted(permissions):
            if key not in SAFE_PERMISSION_KEYS:
                return f"{path.name} sets permissions.{key}"
    return None


def directory_problem(directory: Path) -> str | None:
    """The first problem in `<directory>/.claude/settings*.json`, or None."""
    claude_dir = directory / ".claude"
    if os.path.islink(claude_dir):
        return ".claude is a symlink"
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
