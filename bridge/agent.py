"""The agent adapter interface (add-voice-bridge D8, jarvis-full-agent D30).

The bridge talks to a coding agent through one small seam: run a turn for a
project, get the text to speak, a session or turn id, and (in `full` mode) a
confirmation that is now waiting for the driver's yes. Two adapters exist:

- `readonly` (bridge.readonly_adapter): headless Claude Code next to the bridge
  with the read-only tool list. The default, and the fallback.
- `full` (bridge.remote_adapter): one ssh connection per turn to the wrapper on
  the agent host.

Speech stays outside the seam: an adapter gets a transcript and returns text.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from bridge.config import ProjectConfig

ANSWER_YES = "yes"
ANSWER_NO = "no"


@dataclass(frozen=True)
class PendingConfirmation:
    """What the agent host issued for a gated call it read back (D36)."""

    id: str
    sha256: str


@dataclass(frozen=True)
class ConfirmAnswer:
    """The driver's answer to a read-back, sent with the next request (D33)."""

    id: str
    sha256: str
    answer: str   # ANSWER_YES or ANSWER_NO


@dataclass(frozen=True)
class AgentTurn:
    """One finished turn.

    `spoken` has already passed the spoken filter (no code, no paths, nothing
    shaped like a secret). `ref` is what identifies the turn afterwards: the
    Claude Code session id for a `readonly` turn, the agent host's turn id for a
    `full` turn (empty when the agent host gave none).
    """

    spoken: str
    ref: str
    confirmation: PendingConfirmation | None = None
    # With a confirmation: how long the agent host keeps the call, when it said so.
    expires_s: float | None = None


class AgentUnavailable(Exception):
    """The agent host did not start a turn, and said so before any could start.

    Exactly the four cases of D30. Only this exception lets the bridge answer the
    turn with the `readonly` adapter instead.
    """

    UNREACHABLE = "unreachable"
    NO_CHECKOUT = "no_checkout"
    DISABLED = "disabled"
    UNSAFE_SETTINGS = "unsafe_settings"
    REASONS = frozenset({UNREACHABLE, NO_CHECKOUT, DISABLED, UNSAFE_SETTINGS})

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in self.REASONS:
            raise ValueError(f"not a fallback reason: {reason!r}")
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class AgentTurnFailed(Exception):
    """A `full` turn failed, or may have started. Never answered by a second run.

    `code` is one of the agent host's error codes (`busy`, `low_memory`,
    `timeout`, `agent_failed`, `bad_request`) or one of the bridge's own:
    `host_key` (the pinned host key did not match), `lost` (the connection ended
    without a usable reply), `no_answer` (the bridge's own timeout) and
    `protocol` (a reply that is not the agreed shape).
    """

    HOST_KEY = "host_key"
    LOST = "lost"
    NO_ANSWER = "no_answer"
    PROTOCOL = "protocol"

    def __init__(self, code: str, detail: str = "", turn_id: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.turn_id = turn_id


class AgentAdapter(Protocol):
    """Run one turn of a coding agent for a project."""

    name: str

    def run_turn(self, *, project: ProjectConfig, prompt: str,
                 confirm: ConfirmAnswer | None = None) -> AgentTurn:
        ...
