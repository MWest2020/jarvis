"""A turn in `full` mode: the pending confirmation, the fallback, the spoken errors
(jarvis-full-agent D30, D36).

Everything here works on the transcript of the driver's own audio. Nothing an
agent wrote is ever compared with the affirmation list: a "yes" in a reply, a
file or an issue cannot make the bridge send a confirmation (D39).
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from bridge.agent import (
    ANSWER_NO,
    ANSWER_YES,
    AgentAdapter,
    AgentTurn,
    AgentTurnFailed,
    AgentUnavailable,
    ConfirmAnswer,
    PendingConfirmation,
)
from bridge.config import ProjectConfig, normalise_utterance
from bridge.logging_setup import FILE_ONLY
from bridge.remote_adapter import NOTICE_SENTENCES, OUT_OF_BUDGET

logger = logging.getLogger("bridge.full")

# D36, step 4: the bridge keeps a read-back's id and hash this long. The agent
# host keeps the call for the same 60 seconds, counted from a little earlier.
PENDING_TTL_S = 60.0

CANCELLED = "Cancelled."
# The same words as for a silent recording (server.NOT_HEARD).
NOT_HEARD = "Sorry, I didn't catch that."
# After a yes was sent and no answer came back that says what happened.
UNKNOWN_IF_RAN = "I do not know whether it ran. Check at the desk."
NOTHING_CONFIRMED = "Nothing was confirmed."
TOOK_TOO_LONG = "That took too long. Ask again."
# The bridge stopped waiting so that the phone gets an answer; the turn goes on.
STILL_RUNNING = "It's still running at the desk. Ask me for the result in a bit."
# D36, step 6: put in front of an ordinary request that came instead of a yes.
NOT_CONFIRMED_LINE = ("The driver did not confirm the action that was read back. "
                      "It was not run and is no longer pending.")

# D30: the first sentence of a turn that was answered read-only instead.
FALLBACK_SENTENCES = {
    AgentUnavailable.UNREACHABLE:
        "The agent host could not be reached, so this answer is read-only.",
    AgentUnavailable.NO_CHECKOUT:
        "The agent host does not have this project, so this answer is read-only.",
    AgentUnavailable.DISABLED:
        "Full mode is switched off on the agent host, so this answer is read-only.",
    AgentUnavailable.UNSAFE_SETTINGS:
        "This project's settings need the desk, so this answer is read-only.",
}

# No turn was started on the agent host: these can simply be said.
NOT_STARTED_LINES = {
    "busy": "The agent host is busy. Try again in a moment.",
    "low_memory": "The agent host is busy: it is short of memory. Try again later.",
    "bad_request": "The agent host refused the request.",
    AgentTurnFailed.HOST_KEY:
        "The agent host's key is not the pinned one. Nothing was sent.",
    AgentUnavailable.UNREACHABLE: "The agent host could not be reached.",
    AgentUnavailable.NO_CHECKOUT: "The agent host does not have this project.",
    AgentUnavailable.DISABLED: "Full mode is switched off on the agent host.",
    AgentUnavailable.UNSAFE_SETTINGS: "This project's settings need the desk.",
}
# A turn started, or may have: said together with the promise of D30.
STARTED_LINES = {
    "timeout": "The agent took too long on the agent host and was stopped. "
               "I did not run it again.",
    "agent_failed": "The agent turn failed on the agent host. I did not run it again.",
    AgentTurnFailed.LOST: "I lost the agent host during the turn. I did not run it again. "
                          "It may still be running there.",
    AgentTurnFailed.PROTOCOL: "The agent host gave an answer I could not read. "
                              "I did not run it again.",
}


@dataclass(frozen=True)
class Outcome:
    """What the server speaks and puts in the headers."""

    spoken: str
    ref: str = ""
    needs_confirmation: bool = False


class PendingStore:
    """One pending confirmation per project, in memory only, single use.

    A restart of the bridge forgets every one of them: a yes after a restart
    releases nothing.
    """

    def __init__(self, *, ttl_s: float = PENDING_TTL_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl_s = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[PendingConfirmation, float]] = {}

    def put(self, project: str, confirmation: PendingConfirmation,
            expires_s: float | None = None) -> None:
        """Kept for 60 seconds, or less when the agent host keeps the call for less."""
        ttl_s = self._ttl_s if expires_s is None else min(self._ttl_s, expires_s)
        with self._lock:
            self._pending[project] = (confirmation, self._clock() + ttl_s)

    def take(self, project: str) -> PendingConfirmation | None:
        """Remove the project's pending confirmation; None when none, or too late."""
        with self._lock:
            entry = self._pending.pop(project, None)
            if entry is None:
                return None
            confirmation, deadline = entry
            if self._clock() >= deadline:
                return None
            return confirmation


class FullMode:
    def __init__(self, *, remote: AgentAdapter, readonly: AgentAdapter,
                 confirm_yes: tuple[str, ...], confirm_no: tuple[str, ...],
                 min_speech_s: float, pending: PendingStore | None = None) -> None:
        self._remote = remote
        self._readonly = readonly
        self._yes = frozenset(confirm_yes)
        self._no = frozenset(confirm_no)
        self._min_speech_s = min_speech_s
        self._pending = pending if pending is not None else PendingStore()

    def handle(self, project: ProjectConfig, transcript: str,
               speech_seconds: Callable[[], float]) -> Outcome:
        """One utterance of the driver for one project.

        `speech_seconds` measures the detected speech in the recording. Every
        utterance needs the minimum, not only a yes.
        """
        said = normalise_utterance(transcript)
        if not said:
            # Dots, a note sign, nothing a person said: silence. No turn, and a
            # read-back keeps waiting.
            return Outcome(NOT_HEARD)
        try:
            seconds = float(speech_seconds())
        except Exception:  # noqa: BLE001 - no measurement is no speech
            logger.exception("project=%s: detected speech could not be measured",
                             project.name)
            seconds = 0.0
        if not seconds >= self._min_speech_s:
            # Whisper writes short words ("yes", "you") from noise. A full agent
            # does not act on a sound that may not have been a word, and a noise
            # does not use up a read-back either.
            logger.info("project=%s: %.2fs of detected speech (minimum %.2fs): not "
                        "taken as an utterance", project.name, seconds, self._min_speech_s)
            return Outcome(NOT_HEARD)

        # Taken, not read: whatever this utterance is, the read-back is used up.
        pending = self._pending.take(project.name)
        if pending is None:
            # Nothing waiting, or 60 seconds gone: a "yes" is an ordinary utterance.
            return self._turn(project, transcript)

        if said in self._no:
            self._cancel(project, pending)
            return Outcome(CANCELLED)
        if said in self._yes:
            return self._turn(project, transcript,
                              ConfirmAnswer(pending.id, pending.sha256, ANSWER_YES))
        # Anything else is an ordinary request. It clears the pending call on the
        # agent host like every request, and the agent is told what happened.
        return self._turn(project, f"{NOT_CONFIRMED_LINE}\n\n{transcript}", said=transcript)

    def _cancel(self, project: ProjectConfig, pending: PendingConfirmation) -> None:
        """Send the no: the agent host clears the call and starts no agent turn."""
        try:
            self._remote.run_turn(
                project=project, prompt="",
                confirm=ConfirmAnswer(pending.id, pending.sha256, ANSWER_NO))
        except (AgentUnavailable, AgentTurnFailed) as exc:
            # This side has already dropped it, so no yes can be sent any more;
            # on the agent host it expires by itself.
            logger.warning("project=%s agent=full: the cancel did not reach the agent host "
                           "(%s)", project.name, _code(exc))
            logger.warning("project=%s cancel failed: %s", project.name, exc, extra=FILE_ONLY)

    def _turn(self, project: ProjectConfig, prompt: str,
              confirm: ConfirmAnswer | None = None, *, said: str | None = None) -> Outcome:
        """`said` is what the driver said, when `prompt` has a line in front of it."""
        try:
            turn = self._remote.run_turn(project=project, prompt=prompt, confirm=confirm)
        except AgentUnavailable as exc:
            logger.warning("project=%s agent=full outcome=%s", project.name, exc.reason)
            logger.warning("project=%s agent host unavailable: %s", project.name, exc,
                           extra=FILE_ONLY)
            if confirm is not None:
                # An answer is never turned into a read-only question.
                return Outcome(f"{NOT_STARTED_LINES[exc.reason]} {NOTHING_CONFIRMED}")
            return self._fallback(project, prompt if said is None else said, exc.reason)
        except AgentTurnFailed as exc:
            logger.error("project=%s agent=full turn=%s outcome=%s", project.name,
                         exc.turn_id or "-", exc.code)
            logger.error("project=%s agent turn failed: %s", project.name, exc,
                         extra=FILE_ONLY)
            return Outcome(_failure_line(exc, confirmed=confirm is not None), exc.turn_id)
        return self._outcome(project, turn)

    def _outcome(self, project: ProjectConfig, turn: AgentTurn) -> Outcome:
        if turn.confirmation is not None:
            self._pending.put(project.name, turn.confirmation, turn.expires_s)
        logger.info("project=%s agent=full turn=%s outcome=%s", project.name, turn.ref or "-",
                    "needs_confirmation" if turn.confirmation is not None else "ok")
        return Outcome(turn.spoken, turn.ref, turn.confirmation is not None)

    def _fallback(self, project: ProjectConfig, prompt: str, reason: str) -> Outcome:
        """The one turn answered read-only instead, saying so first (D30)."""
        turn = self._readonly.run_turn(project=project, prompt=prompt)
        return Outcome(f"{FALLBACK_SENTENCES[reason]} {turn.spoken}", turn.ref)


def _code(exc: AgentUnavailable | AgentTurnFailed) -> str:
    return exc.reason if isinstance(exc, AgentUnavailable) else exc.code


def _failure_line(exc: AgentTurnFailed, *, confirmed: bool) -> str:
    if exc.code == "expired":
        return TOOK_TOO_LONG
    if exc.code == AgentTurnFailed.STILL_RUNNING:
        return STILL_RUNNING
    if exc.code == "agent_failed" and exc.notice == OUT_OF_BUDGET:
        # The usage limit is shared with the coding session (design, Risks).
        return NOTICE_SENTENCES[OUT_OF_BUDGET]
    if exc.code in NOT_STARTED_LINES:
        line = NOT_STARTED_LINES[exc.code]
        return f"{line} {NOTHING_CONFIRMED}" if confirmed else line
    line = STARTED_LINES.get(exc.code, STARTED_LINES[AgentTurnFailed.PROTOCOL])
    # A yes went out and it is not known what became of it: never "nothing was
    # confirmed", the call may have run.
    return f"{line} {UNKNOWN_IF_RAN}" if confirmed else line
