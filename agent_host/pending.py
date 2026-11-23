"""What is kept between a gated call and its answer (1.9, D36 "The flow").

Two small stores under the wrapper's state directory (0700 / 0600):

- **the turn's gate state** (`gate/<turn id>.json`), written by the gate
  during one turn and read by the wrapper when `claude` has exited: the
  pending call, the refused call that may be repaired once, whether the
  grant was used, and what the gate answered (for the record and the
  notice). At most one call is pending per turn; once one is, or once a
  repair went wrong, every further gated call is denied without becoming
  pending.
- **the project's pending call** (`pending/<project>.json`), written by the
  wrapper when a read-back goes out: the call and the moment it was issued.
  It lives for 60 seconds, is single use, and is cleared by the next
  request for the project, whatever that request is.

A pending call is the tool name, its input and the real-path `cwd`, and
**nothing derived**: no class, no target, no hash, no sentence. The wrapper
derives all of those again from the call itself (agent_host.naming), so if
the stored call was altered, the read-back describes the altered call and
the hash releases the altered call: what is heard and what a yes releases
are always the same call.

**This store is not a trust boundary.** The state directory belongs to
the agent's own user (D40). It is in the class `self`, so a write to it
through `Edit`, `Write` or a shell command in a shape the rules know is
refused; that keeps a mistake out, not an agent that attacks its own host.
The grant itself travels in the environment of the `claude` process, which
its tools cannot change, and the wrapper decides whether a confirmed call
ran from its own grant and the action log, not from this state.

Next to the call the project's pending file keeps the two hashes that were
read back (`sha256`, `base_hash`). They release nothing: the grant is the
hash the bridge sends, checked against the call as derived at that moment.
They only tell an answer that came too late for a changed push address
(`target_changed`) from one that does not belong to this call (`expired`).
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from agent_host.boundedio import read_text
from agent_host.config import PENDING_SECONDS
from agent_host.sessions import is_canonical_uuid  # noqa: F401 - also used by the gate

# The gate's state for one turn, and a pending call: small files. A larger
# one, or one that is not a regular file, cannot be read (the gate denies).
MAX_STATE_BYTES = 1024 * 1024
_PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class StateError(RuntimeError):
    """The gate's state cannot be used. The gate denies."""


def _write_private(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    # O_NONBLOCK and O_NOFOLLOW: a FIFO or a symlink planted in its place
    # fails at once instead of blocking or writing elsewhere.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NONBLOCK | os.O_NOFOLLOW,
                 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(data))
    tmp.replace(path)


def make_call(tool_name: str, tool_input: dict, cwd: str) -> dict:
    return {"tool": tool_name, "input": tool_input, "cwd": cwd}


def valid_call(call: object) -> bool:
    return (
        isinstance(call, dict) and set(call) == {"tool", "input", "cwd"}
        and isinstance(call["tool"], str) and bool(call["tool"])
        and isinstance(call["input"], dict)
        and isinstance(call["cwd"], str) and os.path.isabs(call["cwd"])
    )


# --- the turn's gate state -------------------------------------------------------------

_EMPTY_STATE = {
    "pending": None,       # the one call that waits for a yes
    "refused": None,       # {"call", "options", "words"}: may be repaired once
    "repair_used": False,
    "desk": False,         # a repair went wrong: every gated call needs the desk
    "grant_used": False,
    "events": [],          # what the gate answered, in order
    "builders": 0,         # builder subagents started in this turn (D59)
}
# Keys a state written before they existed may lack: they take the default.
_OPTIONAL_KEYS = frozenset({"builders"})


class TurnState:
    def __init__(self, path: Path, data: dict):
        self._path = path
        self.data = data

    @classmethod
    def load(cls, gate_dir: Path, turn_id: str) -> "TurnState":
        if not is_canonical_uuid(turn_id):
            raise StateError("the turn id is not one the wrapper issues")
        path = gate_dir / f"{turn_id}.json"
        if not os.path.lexists(path):
            return cls(path, json.loads(json.dumps(_EMPTY_STATE)))
        try:
            data = json.loads(read_text(path, MAX_STATE_BYTES, follow=False))
        except (OSError, ValueError) as exc:
            raise StateError(f"the gate's state for this turn cannot be read: {exc}") from exc
        if not isinstance(data, dict) or not (set(_EMPTY_STATE) - _OPTIONAL_KEYS
                                              <= set(data) <= set(_EMPTY_STATE)) \
                or not isinstance(data["events"], list):
            raise StateError("the gate's state for this turn has another shape")
        data.setdefault("builders", 0)
        if not isinstance(data["builders"], int) or isinstance(data["builders"], bool):
            raise StateError("the gate's state for this turn has another shape")
        return cls(path, data)

    def save(self) -> None:
        _write_private(self._path, self.data)

    def note(self, outcome: str, *, code: str | None, verdict=None, sentence: str | None = None,
             why: str | None = None) -> None:
        """One line for the record: `outcome` is pending, refused, released
        or not_confirmed; `code` is the notice it leads to, if any; `why`, when
        given, a short reason made of fixed words and names only (never a
        value from the environment or the call)."""
        event = {
            "outcome": outcome,
            "code": code,
            "classes": list(verdict.classes) if verdict is not None else [],
            "rule": verdict.rule if verdict is not None else "",
            "readback": sentence,
        }
        if why is not None:
            event["why"] = why
        self.data["events"].append(event)


def read_turn_state(gate_dir: Path, turn_id: str) -> dict:
    """The wrapper's view after the turn. A state it cannot read is an empty
    one: nothing is pending, which is the safe side."""
    try:
        return TurnState.load(gate_dir, turn_id).data
    except StateError:
        return json.loads(json.dumps(_EMPTY_STATE))


# --- the project's pending call ---------------------------------------------------------

@dataclass(frozen=True)
class PendingCall:
    id: str
    call: dict
    issued_at: float
    sha256: str = ""
    base_hash: str = ""

    def expired(self, now: float | None = None) -> bool:
        age = (time.time() if now is None else now) - self.issued_at
        return not 0 <= age <= PENDING_SECONDS


def _pending_path(pending_dir: Path, project: str) -> Path:
    if not _PROJECT_NAME.fullmatch(project):
        raise ValueError("not a project name")
    return pending_dir / f"{project}.json"


def store_pending(pending_dir: Path, project: str, pending_id: str, call: dict,
                  now: float | None = None, *, sha256: str = "", base_hash: str = "") -> PendingCall:
    """Called by the wrapper when the read-back goes out: the expiry clock
    starts here, on the agent host."""
    issued = time.time() if now is None else now
    _write_private(_pending_path(pending_dir, project),
                   {"id": pending_id, "call": call, "issued_at": issued,
                    "sha256": sha256, "base_hash": base_hash})
    return PendingCall(pending_id, call, issued, sha256, base_hash)


def take_pending(pending_dir: Path, project: str) -> PendingCall | None:
    """Read the project's pending call and remove it. Every request for the
    project does this first, so a pending call never survives the next
    request, and can never be used twice."""
    path = _pending_path(pending_dir, project)
    try:
        raw = path.read_text()
    except OSError:
        raw = None
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        issued = data["issued_at"]
        if not isinstance(data["id"], str) or not valid_call(data["call"]) \
                or type(issued) not in (int, float):
            return None
        sha256, base_hash = data.get("sha256", ""), data.get("base_hash", "")
        if not isinstance(sha256, str) or not isinstance(base_hash, str):
            return None
        return PendingCall(data["id"], data["call"], float(issued), sha256, base_hash)
    except (ValueError, KeyError, TypeError):
        return None
