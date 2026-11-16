"""The turn's trusted values, which the gate never takes from its own
environment (D36, reopened after host check 6).

Host check 6 (Claude Code 2.1.322) showed that Claude Code gives a
checkout's `env` settings to every hook process, and reloads them when the
file appears in the middle of a turn. So the gate's own environment is not
the environment the wrapper started `claude` with: a project file can plant
`JARVIS_GRANT` there, or point `JARVIS_TURN_CHECKOUT` somewhere clean.

What the gate relies on comes from two places that project settings cannot
reach, and both must agree:

- **the record**: the wrapper writes the turn's `JARVIS_*` values to
  `<gate dir>/trusted-<pid>-<start>-<boot>.json`, keyed by the process it
  starts (its pid, its start time in `/proc/<pid>/stat`, and the boot id, so
  a reused pid never matches an old record). It is written from inside the
  new process just before it becomes `claude` (`preexec_fn`), so it exists
  before the first hook can run. The gate's state directory is in the class
  `self`: the agent's tools cannot write it through the gate;
- **the exec-time environment** of that same process, `/proc/<pid>/environ`:
  the block the kernel received at `execve`. Settings applied later by
  Claude Code change the process's own copy, not this block (seen on the
  host: the hook got the planted grant, `/proc/<claude>/environ` kept the
  wrapper's).

The gate walks up from its parent (Claude Code runs a hook as `sh -c`, so
the chain is gate, sh, claude, wrapper) and takes the **topmost** process
that has a record: a process the agent started inside the turn (a nested
`claude` with an environment of its own making) is always below the turn's
`claude`, so it cannot stand in for it. The record's `JARVIS_*` values must
equal that process's exec-time `JARVIS_*` values exactly; if they do not,
or there is no record, there are no trusted values and every gated call is
denied.

**The whole environment is compared**, not only `JARVIS_*`: a project's
`env` can set `LD_PRELOAD`, `PATH`, `HOME`, `GIT_CONFIG_*` and more, which
change what the gate and every command after it do. `env_difference` lists
every name whose value in the gate's own environment is not the one
expected: the exec-time environment of `claude`, with the values the turn
settings pin (`env` in `--settings`, which outranks a project's `env`, at
the start and after a mid-turn reload: seen on the host), and minus the
names Claude Code itself adds to every hook process (`CLAUDE_ADDED`,
measured on the host for 2.1.322). Any name in that list denies every gated
call (`settings_changed`).

Every read here is bounded (agent_host.boundedio): the walk has a depth,
each file a cap.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from agent_host.boundedio import MAX_SETTINGS_BYTES, read_regular

PREFIX = "JARVIS_"
RECORD_PREFIX = "trusted-"
MAX_DEPTH = 64
MAX_RECORD_BYTES = 64 * 1024
MAX_ENVIRON_BYTES = 4 * 1024 * 1024
MAX_STAT_BYTES = 4096
PROC = Path("/proc")

# The names Claude Code 2.1.322 adds to the environment of every hook
# process, by itself, measured on the agent host (a hook that dumped its
# environment, compared with /proc/<claude>/environ, in a clean checkout).
# Their values are Claude Code's, and they are not compared. On a new Claude
# Code version a name missing here shows up as a denial of every gated call
# with `settings_changed`: the update runbook's check 4 catches it.
CLAUDE_ADDED: frozenset[str] = frozenset({
    "AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT", "CLAUDE_PID",
    "CLAUDE_PROJECT_DIR", "COREPACK_ENABLE_AUTO_PIN", "DISABLE_AUTOUPDATER", "LC_CTYPE",
    "NoDefaultCurrentDirectoryInExePath", "PWD",
})


@dataclass(frozen=True)
class Trusted:
    """The values the wrapper started the turn's `claude` with: its whole
    exec-time environment, and the `JARVIS_*` part of it (`env`)."""
    pid: int
    env: dict[str, str]
    exec_env: dict[str, str] | None = None

    @property
    def turn_id(self) -> str | None:
        return self.env.get("JARVIS_TURN_ID")

    @property
    def checkout(self) -> Path | None:
        value = self.env.get("JARVIS_TURN_CHECKOUT")
        if not value or not os.path.isabs(value):
            return None
        return Path(value)

    @property
    def grant(self) -> str | None:
        return self.env.get("JARVIS_GRANT") or None

    @property
    def grant_base(self) -> str | None:
        return self.env.get("JARVIS_GRANT_BASE") or None


def jarvis_env(environ) -> dict[str, str]:
    """The `JARVIS_*` part of an environment."""
    return {key: value for key, value in environ.items() if key.startswith(PREFIX)}


def env_difference(own_environ, trusted: Trusted,
                   pins: dict[str, str] | None = None) -> list[str]:
    """The names whose value in this environment is not the expected one: a
    value changed, a name added, a name gone. Expected is the exec-time
    environment of `claude` with the turn settings' pins on top; the names
    Claude Code adds itself are not compared. Project `env` settings are the
    only way a difference comes about in a turn the wrapper started."""
    base = trusted.exec_env if trusted.exec_env is not None else trusted.env
    expected = {**base, **(pins or {})}
    found = []
    for name in sorted(set(own_environ) | set(expected)):
        if name in CLAUDE_ADDED:
            continue
        if own_environ.get(name) != expected.get(name):
            found.append(name)
    return found


def changed(own_environ, trusted: Trusted, pins: dict[str, str] | None = None) -> bool:
    return bool(env_difference(own_environ, trusted, pins))


def settings_pins(turn_settings: Path) -> dict[str, str]:
    """The `env` the turn settings pin: names and values. Raises when the
    file cannot be read: then nobody knows what to expect, and the gate
    denies."""
    data = json.loads(read_regular(turn_settings, MAX_SETTINGS_BYTES))
    env = data.get("env", {}) if isinstance(data, dict) else None
    if not isinstance(env, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in env.items()):
        raise ValueError("the turn settings' env is not a table of strings")
    return dict(env)


def boot_id(proc: Path = PROC) -> str:
    raw = read_regular(proc / "sys" / "kernel" / "random" / "boot_id", 64)
    value = raw.decode("ascii").strip()
    if not value or any(ch not in "0123456789abcdef-" for ch in value):
        raise OSError("unexpected boot id")
    return value


def proc_stat(pid: int, proc: Path = PROC) -> tuple[int, int] | None:
    """(parent pid, start time in clock ticks since boot) of a process, or
    None when it is gone or cannot be read."""
    try:
        raw = read_regular(proc / str(pid) / "stat", MAX_STAT_BYTES).decode("ascii", "replace")
        # The name in parentheses may hold spaces and parentheses itself.
        fields = raw[raw.rindex(")") + 1:].split()
        return int(fields[1]), int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


def record_name(pid: int, start: int, boot: str) -> str:
    return f"{RECORD_PREFIX}{pid}-{start}-{boot}.json"


def write_for_self(gate_dir: Path, env: dict[str, str], proc: Path = PROC) -> Path:
    """Write the record for the calling process. The wrapper runs this in
    the child it starts, between fork and exec (`preexec_fn`): the pid and
    start time do not change at exec, so the record names the `claude` that
    process becomes. Raises when it cannot; the wrapper then does not start
    the turn."""
    pid = os.getpid()
    found = proc_stat(pid, proc)
    if found is None:
        raise OSError("cannot read this process's start time")
    boot = boot_id(proc)
    gate_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = gate_dir / record_name(pid, found[1], boot)
    body = json.dumps({"v": 1, "pid": pid, "start": found[1], "boot": boot,
                       "env": jarvis_env(env)}).encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, body)
    finally:
        os.close(fd)
    return path


def remove_for(gate_dir: Path, pid: int) -> None:
    """Remove the record of a process the wrapper started, once it is gone."""
    try:
        entries = list(gate_dir.glob(f"{RECORD_PREFIX}{pid}-*.json"))
    except OSError:
        return
    for path in entries:
        try:
            path.unlink()
        except OSError:
            pass


def remove_stale(gate_dir: Path, proc: Path = PROC) -> None:
    """Remove every record whose process is gone (or is another process now):
    what a `claude` that failed to start leaves behind."""
    try:
        entries = list(gate_dir.glob(f"{RECORD_PREFIX}*.json"))
    except OSError:
        return
    for path in entries:
        try:
            pid_text, start_text = path.name[len(RECORD_PREFIX):].split("-")[:2]
            found = proc_stat(int(pid_text), proc)
            if found is not None and found[1] == int(start_text):
                continue
            path.unlink()
        except (OSError, ValueError):
            continue


def exec_environ(pid: int, proc: Path = PROC, *, whole: bool = False) -> dict[str, str] | None:
    """The `JARVIS_*` part (or with `whole`, all) of a process's exec-time
    environment, or None when it cannot be read, is not text, or names a key
    twice."""
    try:
        raw = read_regular(proc / str(pid) / "environ", MAX_ENVIRON_BYTES)
    except OSError:
        return None
    found: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        if not entry or b"=" not in entry:
            continue
        if not whole and not entry.startswith(PREFIX.encode()):
            continue
        key, _, value = entry.partition(b"=")
        try:
            name, text = key.decode("utf-8"), value.decode("utf-8")
        except UnicodeDecodeError:
            return None
        if name in found:
            return None
        found[name] = text
    return found


def _load_record(path: Path, pid: int, start: int, boot: str) -> dict[str, str] | None:
    try:
        data = json.loads(read_regular(path, MAX_RECORD_BYTES, follow=False, owner=os.getuid()))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("v") != 1 or data.get("pid") != pid \
            or data.get("start") != start or data.get("boot") != boot:
        return None
    env = data.get("env")
    if not isinstance(env, dict) or not all(
        isinstance(key, str) and key.startswith(PREFIX) and isinstance(value, str)
        for key, value in env.items()
    ):
        return None
    return env


def find(gate_dir: Path, *, start_pid: int | None = None, proc: Path = PROC) -> Trusted | None:
    """The trusted values of the turn this process runs in, or None.

    Walks up from `start_pid` (the parent by default) and takes the topmost
    process with a record; the record must equal that process's exec-time
    `JARVIS_*` environment."""
    try:
        boot = boot_id(proc)
    except OSError:
        return None
    pid = start_pid if start_pid is not None else os.getppid()
    topmost: tuple[int, int] | None = None
    for _ in range(MAX_DEPTH):
        found = proc_stat(pid, proc)
        if found is None:
            break
        parent, start = found
        if os.path.lexists(gate_dir / record_name(pid, start, boot)):
            topmost = (pid, start)
        if parent <= 0 or pid == 1:
            break
        pid = parent
    if topmost is None:
        return None
    pid, start = topmost
    env = _load_record(gate_dir / record_name(pid, start, boot), pid, start, boot)
    whole = exec_environ(pid, proc, whole=True)
    if env is None or whole is None or jarvis_env(whole) != env:
        return None
    # The same process still: not gone and replaced under the same pid
    # while the files were read.
    again = proc_stat(pid, proc)
    if again is None or again[1] != start:
        return None
    return Trusted(pid, dict(env), whole)
