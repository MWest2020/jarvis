"""A repository created by voice becomes a project (jarvis-general-conversation D53).

After a confirming turn in which a confirmed `gh repo create <owner>/<name>`
ran, the wrapper (never the agent) clones that repository into the project
root, so that it is a project at the next turn. Each attempt is bounded by
`clone_timeout_s`; a failed or timed-out attempt leaves nothing under the
root and a pending-clone marker in the state directory, and the next wrapper
invocations try again, at most MAX_TRIES attempts in all.

What is under the wrapper's control here:

- *Where.* Only `<projects_root>/<name>`, only for an owner in `own_owners`,
  only for a project name that is not excluded, and only where nothing is.
  A folder that is already there counts as ready only when it is a project
  whose `origin` is exactly that repository on the forge host.
- *How.* `clone_command <owner>/<name> <staging>/<name> -- --depth 1`, in a
  fresh hidden staging directory under the root (hidden, so no list names
  it), with the turn's environment minus the grant, stdin closed, its whole
  process group killed at the bound or on any way out of this module. Moved
  into place with a rename that refuses to replace anything.
- *Markers.* The state directory is writable by the agent (D40), so a marker
  is checked again in full before it is used: the same owner, name and
  exclusion rules. The worst a planted marker can do is have one of the
  user's own repositories cloned to an empty name under the root.
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
import shutil
import signal
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent_host import classify, projects, pushaddr
from agent_host import record as record_mod
from agent_host.config import AgentHostConfig
from bridge.contract import PROJECT_REASONS

CLONE_DIR_PREFIX = record_mod.CLONE_DIR_PREFIX
MAX_TRIES = 3
# Never handed to the clone: they belong to the agent turn that ended.
_GRANT_VARIABLES = ("JARVIS_GRANT", "JARVIS_GRANT_BASE")
# A new repository has one commit or none: its history is not needed to work in it,
# and a shallow clone keeps the attempt short. A work tree either way.
DEPTH_ARGS = ("--", "--depth", "1")
_MAX_MARKER_BYTES = 4096

READY = "ready"


@dataclass(frozen=True)
class Outcome:
    """`state` is "ready" or one of bridge.contract.PROJECT_REASONS."""

    state: str
    why: str

    def __post_init__(self) -> None:
        assert self.state == READY or self.state in PROJECT_REASONS

    @property
    def ready(self) -> bool:
        return self.state == READY

    def field(self, name: str) -> dict:
        """The reply's `project` field. Only for a valid project name: the
        contract's pattern (bridge.contract.PROJECT_NAME) is part of it."""
        assert projects.project_name_ok(name), name
        return {"name": name, "ready": self.ready, "reason": None if self.ready else self.state}

    def recorded(self) -> str:
        return READY if self.ready else f"not ready ({self.state}): {self.why}"


def markers_dir(config: AgentHostConfig) -> Path:
    return config.state_dir / "clones"


def _refusal(config: AgentHostConfig, repository: str) -> str | None:
    """Why this repository is never cloned here, or None."""
    if config.projects or config.projects_root is None:
        return "the agent host has an explicit project list"
    owner, _, name = repository.partition("/")
    if owner.lower() not in {o.lower() for o in config.own_owners}:
        return "the owner is not one of own_owners"
    if not projects.project_name_ok(name) or name in config.project_exclusions:
        return "not a project name, or excluded"
    return None


def _is_that_repository(config: AgentHostConfig, name: str, repository: str,
                        env: dict[str, str]) -> bool:
    """`<root>/<name>` is a project whose origin is exactly `repository` (D53)."""
    try:
        path = projects.resolve_project(config, name)
    except (projects.ProjectError, OSError):
        return False
    found = pushaddr.checkout_repository(path, config.forge_host, classify.git_runner(env))
    return isinstance(found, pushaddr.Address) and found.repository.lower() == repository.lower()


def make_project(config: AgentHostConfig, repository: str, env: dict[str, str], *,
                 tries_before: int = 0) -> Outcome:
    """One attempt to make `repository` a project. Writes or clears its marker."""
    refused = _refusal(config, repository)
    name = repository.partition("/")[2]
    if refused is not None:
        _clear_marker(config, name)
        return Outcome("needs_desk", refused)
    assert config.projects_root is not None
    target = config.projects_root / name
    if os.path.lexists(target):
        _clear_marker(config, name)
        if _is_that_repository(config, name, repository, env):
            return Outcome(READY, "already a project")
        return Outcome("name_taken", "a different folder with that name exists")
    ok, why = _attempt(config, repository, name, env)
    if ok:
        _clear_marker(config, name)
        return Outcome(READY, why)
    tries = tries_before + 1
    if tries >= MAX_TRIES:
        _clear_marker(config, name)
        return Outcome("needs_desk", f"{why}; {tries} attempts, no more")
    _write_marker(config, name, repository, tries)
    return Outcome("retrying", why)


def _attempt(config: AgentHostConfig, repository: str, name: str,
             env: dict[str, str]) -> tuple[bool, str]:
    root = config.projects_root
    assert root is not None
    try:
        staging = Path(tempfile.mkdtemp(prefix=CLONE_DIR_PREFIX, dir=root))
    except OSError as exc:
        return False, f"no directory for the clone: {exc.strerror or exc}"
    try:
        destination = staging / name
        child_env = {key: value for key, value in env.items() if key not in _GRANT_VARIABLES}
        try:
            proc = subprocess.Popen(
                [*config.clone_command, repository, str(destination), *DEPTH_ARGS], cwd=root,
                env=child_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"the clone could not start: {exc}"
        try:
            try:
                proc.wait(timeout=config.clone_timeout_s)
            except subprocess.TimeoutExpired:
                return False, f"the clone took longer than {config.clone_timeout_s}s"
        finally:
            # Whatever way out: no clone outlives this attempt.
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
        if proc.returncode != 0:
            return False, f"the clone exited {proc.returncode}"
        if destination.is_symlink() or not destination.is_dir() \
                or not (destination / ".git").is_dir():
            return False, "the clone is not a work tree"
        try:
            rename_noreplace(destination, root / name)
        except FileExistsError:
            return False, "something appeared at that name during the clone"
        except OSError as exc:
            return False, f"the clone could not be moved into place: {exc.strerror or exc}"
        try:
            projects.resolve_project(config, name)
        except projects.ProjectError as exc:
            return False, f"not a project after the clone: {exc}"
        return True, "cloned"
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# --- a rename that never replaces ---------------------------------------------------

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1


def _renameat2():
    try:
        fn = ctypes.CDLL(None, use_errno=True).renameat2
    except (OSError, AttributeError):
        return None
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    return fn


def rename_noreplace(source: Path, target: Path) -> None:
    """Rename `source` to `target`, never replacing what is at `target`.

    `renameat2(RENAME_NOREPLACE)` where the C library and the file system have
    it (glibc 2.28 and later, ext4, xfs, btrfs, tmpfs): atomic. Elsewhere a
    check right before a plain rename; the window between the two is accepted
    (a plain rename replaces only an empty directory, never a checkout).
    FileExistsError when something is at `target`.
    """
    fn = _renameat2()
    if fn is not None:
        if fn(_AT_FDCWD, os.fsencode(source), _AT_FDCWD, os.fsencode(target),
              _RENAME_NOREPLACE) == 0:
            return
        err = ctypes.get_errno()
        if err == errno.EEXIST:
            raise FileExistsError(err, os.strerror(err), str(target))
        if err not in (errno.ENOSYS, errno.EINVAL):
            raise OSError(err, os.strerror(err), str(target))
    if os.path.lexists(target):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(target))
    os.rename(source, target)


# --- markers and retries ------------------------------------------------------------

def _marker(config: AgentHostConfig, name: str) -> Path:
    return markers_dir(config) / f"{name}.json"


def _write_marker(config: AgentHostConfig, name: str, repository: str, tries: int) -> None:
    directory = markers_dir(config)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = {"repository": repository, "tries": tries,
            "updated": datetime.now(timezone.utc).isoformat()}
    tmp = directory / f".{name}.{uuid.uuid4().hex}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, _marker(config, name))


def _clear_marker(config: AgentHostConfig, name: str) -> None:
    try:
        _marker(config, name).unlink()
    except (FileNotFoundError, NotADirectoryError):
        pass
    except OSError:
        pass


def pending(config: AgentHostConfig) -> list[tuple[str, int]]:
    """The markers that are still valid: (repository, tries so far). An invalid
    one (wrong shape, a name that does not match its file, a repository that
    would never be cloned here) is removed."""
    directory = markers_dir(config)
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return []
    found = []
    try:
        # Oldest first (by when the marker was last written), then by name.
        entries.sort(key=lambda e: (e.lstat().st_mtime_ns, e.name))
    except OSError:
        pass
    for entry in entries:
        if entry.name.startswith(".") or entry.suffix != ".json":
            continue
        try:
            raw = record_mod.read_text(entry, _MAX_MARKER_BYTES, follow=False)
            data = json.loads(raw)
            repository, tries = data["repository"], data["tries"]
            valid = (isinstance(repository, str) and isinstance(tries, int)
                     and not isinstance(tries, bool) and 1 <= tries < MAX_TRIES
                     and repository.count("/") == 1
                     and repository.partition("/")[2] == entry.stem
                     and _refusal(config, repository) is None)
        except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError):
            valid = False
        if not valid:
            try:
                entry.unlink()
            except OSError:
                pass
            continue
        found.append((repository, tries))
    return found


def retry_pending(config: AgentHostConfig, env: dict[str, str]) -> list[tuple[str, Outcome]]:
    """Try the oldest pending clone once more. The caller holds the turn lock.
    Stale staging directories go first. The retry is recorded."""
    if config.projects_root is not None:
        record_mod.remove_stale_staging(config.projects_root)
    done = []
    # One marker per call, the oldest: whoever holds the turn lock holds it for
    # at most one clone attempt. The others wait for the next request.
    for repository, tries in pending(config)[:1]:
        try:
            outcome = make_project(config, repository, env, tries_before=tries)
        except Exception as exc:  # noqa: BLE001 - recorded, and the marker is used up
            _clear_marker(config, repository.partition("/")[2])
            outcome = Outcome("needs_desk", f"failed: {type(exc).__name__}")
        done.append((repository, outcome))
        _record_retry(config, repository, tries + 1, outcome)
    return done


def _record_retry(config: AgentHostConfig, repository: str, attempt: int,
                  outcome: Outcome) -> None:
    rec = record_mod.TurnRecord(
        turn_id=str(uuid.uuid4()), time=datetime.now(timezone.utc).isoformat(),
        project=repository.partition("/")[2], session_id=None, prompt="", reply="",
        duration_s=0.0, outcome="clone",
        gated_actions=[{"outcome": "clone", "repository": repository, "attempt": attempt,
                        "project": outcome.recorded()}],
    )
    try:
        record_mod.write_record(config.records_dir, rec)
    except OSError:
        pass
