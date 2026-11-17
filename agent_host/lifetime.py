"""How long a project's session lives (change jarvis-session-lifetime).

Two things end a session, besides the D34 rotation fallback and a stored id
that is not one:

- **Idle.** When the project's last turn ended more than
  `session_idle_minutes` ago (default 60, the prompt cache's lifetime; 0
  switches it off), the next turn starts a new session instead of resuming.
  Resuming a long session after the cache expired sends the whole
  conversation to the cache again and costs about 3 s per turn
  (docs/how-to/agent-host.md, "The voice profile").
- **Closed.** The driver asked to close the conversation and the agent ended
  its reply with the line `SESSION: close` (agent_host/prompt.md). The wrapper
  drops the turn's own project's session id; the next turn starts fresh.

`TurnClock` keeps when each project's last turn ended, or that it was closed,
in `sessions/last_turn.json` next to the session ids.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agent_host.sessions import KeyValueStore
from bridge.summary import last_marker_line

# The value TurnClock keeps for a project whose conversation the agent closed.
CLOSED = "closed"


class TurnClock:
    """When each project's last turn ended, or that its conversation was
    closed.

    Like the session ids, the agent can write this file (D40): a value that
    is not a time with a zone is read as unknown, and so is a time in the
    future; the session is then resumed as before. The worst the agent can do
    with it is what it can already do with the session ids: make a project's
    next turn resume or start fresh.
    """

    def __init__(self, path: Path):
        self._store = KeyValueStore(path)

    def ended(self, project: str, when: datetime) -> None:
        self._store.set(project, when.isoformat())

    def closed(self, project: str) -> None:
        self._store.set(project, CLOSED)

    def was_closed(self, project: str) -> bool:
        return self._store.get(project) == CLOSED

    def idle_seconds(self, project: str, now: datetime) -> float | None:
        """Seconds since the project's last turn ended, or None when unknown."""
        raw = self._store.get(project)
        if not isinstance(raw, str) or raw == CLOSED:
            return None
        try:
            ended = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if ended.tzinfo is None:
            return None
        seconds = (now - ended).total_seconds()
        return seconds if seconds >= 0 else None


# How a turn's session started, in the record's `session.start`.
RESUMED = "resumed"     # the stored session, continued
NEW = "new"             # nothing stored: the project's first turn
AFTER_CLOSE = "closed"  # nothing stored because the agent closed the conversation
IDLE = "idle"           # the last turn ended more than session_idle_minutes ago
DISCARDED = "discarded"  # the stored value was no session id (D33)
ROTATED = "rotated"     # the first turn after 04:00 (D34's rotation fallback)

# The starts the driver is told about with the notice `new_session`: the
# conversation he had is gone and he did not ask for that. Not NEW (there was
# none) and not AFTER_CLOSE (he asked to close it, and heard the goodbye).
ANNOUNCED = frozenset({IDLE, DISCARDED, ROTATED})


@dataclass(frozen=True)
class Start:
    session_id: str | None   # what to resume, or None for a new session
    reason: str              # one of the constants above
    idle_minutes: int | None = None

    def recorded(self) -> dict:
        out: dict = {"start": self.reason}
        if self.idle_minutes is not None:
            out["idle_minutes"] = self.idle_minutes
        return out

    @property
    def announced(self) -> bool:
        return self.reason in ANNOUNCED


def choose(*, stored_id: str | None, discarded: bool, rotated_id: str | None,
           idle_seconds: float | None, idle_limit_minutes: int, was_closed: bool) -> Start:
    """Whether the turn resumes `stored_id`, and why not when it does not.

    `rotated_id` is what the rotation fallback left of `stored_id` (None on
    the first turn after 04:00, or when the fallback is on and nothing was
    stored)."""
    idle_minutes = int(idle_seconds // 60) if idle_seconds is not None else None
    if discarded:
        return Start(None, DISCARDED, idle_minutes)
    if stored_id is None:
        return Start(None, AFTER_CLOSE if was_closed else NEW, idle_minutes)
    if idle_limit_minutes > 0 and idle_seconds is not None \
            and idle_seconds > idle_limit_minutes * 60:
        return Start(None, IDLE, idle_minutes)
    if rotated_id is None:
        return Start(None, ROTATED, idle_minutes)
    return Start(stored_id, RESUMED, idle_minutes)


# The agent's last line when the driver asked to close the conversation
# (agent_host/prompt.md). Exactly these words, nothing after them: the marker
# carries no argument, so it cannot name another project, a session or
# anything else; the wrapper applies it to the turn's own project only.
CLOSE_MARKER = "SESSION: close"
_CLOSE_LINE = re.compile(r"[\s*_`>-]*SESSION:[*_`]*\s*close[\s*_`.!]*", re.IGNORECASE)
# The same words at the end of the SPOKEN SUMMARY line, when the agent put
# them there instead of on a line of their own.
_CLOSE_TAIL = re.compile(r"\s*[*_`]*SESSION:[*_`]*\s*close[\s*_`.!]*\Z", re.IGNORECASE)


def split_close_marker(reply_text: str) -> tuple[str, bool]:
    """(the reply without the close marker, True when the agent closed).

    The marker counts only as the reply's last non-empty line, or at the end
    of a last line that is the SPOKEN SUMMARY line. Anywhere else (quoted from
    a file, in the middle of the reply, with words after it) it is text, and
    the reply is returned as it is.
    """
    lines = reply_text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return reply_text, False
    last = lines[-1]
    if _CLOSE_LINE.fullmatch(last):
        return "\n".join(lines[:-1]), True
    if last_marker_line(last) == last:
        tail = _CLOSE_TAIL.search(last)
        if tail is not None:
            return "\n".join([*lines[:-1], last[:tail.start()].rstrip()]), True
    return reply_text, False
