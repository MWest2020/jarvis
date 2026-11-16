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
from bridge.remote_adapter import (
    BAD_REQUEST,
    BUSY,
    EXPIRED,
    LOW_MEMORY,
    NOTICE_SENTENCES,
    OUT_OF_BUDGET,
    TRY_AGAIN_CODES,
)

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
# A yes met a busy agent host: nothing ran, and the read-back is still good.
SAY_YES_AGAIN = "The agent host is busy. Say yes again in a moment."
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

# D52 (jarvis-general-conversation): a fallback reason, for a project the bridge has
# no copy of. Nothing can be answered read-only, so only what happened is said.
NO_COPY_LINES = {
    AgentUnavailable.UNREACHABLE: "The agent host is not reachable right now.",
    AgentUnavailable.NO_CHECKOUT: "The agent host does not have this project.",
    AgentUnavailable.DISABLED: "Full mode is switched off on the agent host.",
    AgentUnavailable.UNSAFE_SETTINGS: "This project's settings need the desk.",
}

# Where the phone's project list came from (GET /projects in `full` mode, D52).
SOURCE_AGENT_HOST = "agent_host"
SOURCE_BRIDGE = "bridge"

# No turn was started on the agent host: these can simply be said.
NOT_STARTED_LINES = {
    BUSY: "The agent host is busy. Try again in a moment.",
    LOW_MEMORY: "The agent host is busy: it is short of memory. Try again later.",
    BAD_REQUEST: "The agent host refused the request.",
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
    """What the server speaks and puts in the headers.

    `to_keep` is a read-back that is not pending yet: it becomes pending when the
    server has spoken it (FullMode.delivered), and is cancelled on the agent host
    when it could not (FullMode.undelivered).
    """

    spoken: str
    ref: str = ""
    needs_confirmation: bool = False
    to_keep: tuple[PendingConfirmation, float] | None = None   # with its deadline
    # A new project is ready on the agent host: the phone fetches its list again.
    projects_changed: bool = False


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

    def deadline(self, expires_s: float | None = None) -> float:
        """From now: 60 seconds, or less when the agent host keeps the call for less."""
        return self._clock() + (self._ttl_s if expires_s is None
                                else min(self._ttl_s, expires_s))

    def put(self, project: str, confirmation: PendingConfirmation,
            expires_s: float | None = None) -> None:
        self.put_until(project, confirmation, self.deadline(expires_s))

    def put_until(self, project: str, confirmation: PendingConfirmation,
                  deadline: float) -> None:
        """Keep it until a deadline that was fixed earlier: never a fresh 60 seconds."""
        with self._lock:
            self._pending[project] = (confirmation, deadline)

    def take_entry(self, project: str) -> tuple[PendingConfirmation, float] | None:
        """Remove the project's pending confirmation; None when none, or too late."""
        with self._lock:
            entry = self._pending.pop(project, None)
            if entry is None or self._clock() >= entry[1]:
                return None
            return entry

    def take(self, project: str) -> PendingConfirmation | None:
        entry = self.take_entry(project)
        return None if entry is None else entry[0]


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

    def project_names(self, fallback: Callable[[], list[str]]) -> tuple[list[str], str]:
        """The phone's project list in `full` mode (D52): the agent host's, or, when
        it cannot give one, the bridge's own (`fallback`). With where it came from."""
        try:
            return self._remote.list_projects(), SOURCE_AGENT_HOST
        except (AgentUnavailable, AgentTurnFailed) as exc:
            logger.warning("agent=full: the agent host gave no project list (%s); the "
                           "bridge's own projects are listed", exc.code)
            logger.warning("project list from the agent host failed: %s", exc,
                           extra=FILE_ONLY)
            return fallback(), SOURCE_BRIDGE

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
        entry = self._pending.take_entry(project.name)
        if entry is None:
            # Nothing waiting, or 60 seconds gone: a "yes" is an ordinary utterance.
            return self._turn(project, transcript)

        pending, deadline = entry
        if said in self._no:
            self._cancel(project, pending)
            return Outcome(CANCELLED)
        if said in self._yes:
            return self._turn(project, transcript,
                              ConfirmAnswer(pending.id, pending.sha256, ANSWER_YES),
                              answered=entry)
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
                           "(%s)", project.name, exc.code)
            logger.warning("project=%s cancel failed: %s", project.name, exc, extra=FILE_ONLY)

    def delivered(self, project: ProjectConfig, outcome: Outcome) -> None:
        """The reply was synthesised and written to the phone. Only now does a
        read-back count as heard, and only now can a yes release it. The time is
        counted from when it arrived, not from now."""
        if outcome.to_keep is not None:
            self._pending.put_until(project.name, *outcome.to_keep)

    def undelivered(self, project: ProjectConfig, outcome: Outcome) -> None:
        """The reply could not be spoken or sent. A read-back nobody heard is not
        kept here and is cancelled on the agent host."""
        if outcome.to_keep is not None:
            logger.warning("project=%s agent=full: the read-back did not reach the phone; "
                           "the pending call is cancelled", project.name)
            self._cancel(project, outcome.to_keep[0])

    def _turn(self, project: ProjectConfig, prompt: str,
              confirm: ConfirmAnswer | None = None, *, said: str | None = None,
              answered: tuple[PendingConfirmation, float] | None = None) -> Outcome:
        """`said` is what the driver said, when `prompt` has a line in front of it.
        `answered` is the read-back a yes was given to, with its deadline."""
        try:
            turn = self._remote.run_turn(project=project, prompt=prompt, confirm=confirm)
        except AgentUnavailable as exc:
            logger.warning("project=%s agent=full outcome=%s", project.name, exc.reason)
            logger.warning("project=%s agent host unavailable: %s", project.name, exc,
                           extra=FILE_ONLY)
            if confirm is not None:
                # An answer is never turned into a read-only question.
                return Outcome(f"{NOT_STARTED_LINES[exc.reason]} {NOTHING_CONFIRMED}")
            if project.working_dir is None:
                # Only the agent host has this project: nothing to answer from here.
                return Outcome(NO_COPY_LINES[exc.reason])
            return self._fallback(project, prompt if said is None else said, exc.reason)
        except AgentTurnFailed as exc:
            logger.error("project=%s agent=full turn=%s outcome=%s", project.name,
                         exc.turn_id or "-", exc.code)
            logger.error("project=%s agent turn failed: %s", project.name, exc,
                         extra=FILE_ONLY)
            if answered is not None and exc.code in TRY_AGAIN_CODES:
                # Nothing ran and nothing was refused for good. The read-back is put
                # back as it was, same id, same deadline: the yes is not lost.
                self._pending.put_until(project.name, *answered)
                return Outcome(SAY_YES_AGAIN, needs_confirmation=True)
            return Outcome(_failure_line(exc, confirmed=confirm is not None), exc.turn_id)
        return self._outcome(project, turn)

    def _outcome(self, project: ProjectConfig, turn: AgentTurn) -> Outcome:
        to_keep = None
        if turn.confirmation is not None:
            # Not pending yet: first it has to be spoken (delivered()).
            to_keep = (turn.confirmation, self._pending.deadline(turn.expires_s))
        logger.info("project=%s agent=full turn=%s outcome=%s", project.name, turn.ref or "-",
                    "needs_confirmation" if turn.confirmation is not None else "ok")
        return Outcome(turn.spoken, turn.ref, turn.confirmation is not None, to_keep,
                       projects_changed=turn.projects_changed)

    def _fallback(self, project: ProjectConfig, prompt: str, reason: str) -> Outcome:
        """The one turn answered read-only instead, saying so first (D30)."""
        turn = self._readonly.run_turn(project=project, prompt=prompt)
        return Outcome(f"{FALLBACK_SENTENCES[reason]} {turn.spoken}", turn.ref)


def _failure_line(exc: AgentTurnFailed, *, confirmed: bool) -> str:
    if exc.code == EXPIRED:
        return TOOK_TOO_LONG
    if exc.code == AgentTurnFailed.STILL_RUNNING:
        return STILL_RUNNING
    if exc.code == "agent_failed" and exc.notice == OUT_OF_BUDGET:
        # The usage limit is shared with the coding session (design, Risks). It can
        # be reached in the middle of a turn, so after a yes the call may have run.
        line = NOTICE_SENTENCES[OUT_OF_BUDGET]
        return f"{line} {UNKNOWN_IF_RAN}" if confirmed else line
    if exc.code in NOT_STARTED_LINES:
        line = NOT_STARTED_LINES[exc.code]
        return f"{line} {NOTHING_CONFIRMED}" if confirmed else line
    line = STARTED_LINES.get(exc.code, STARTED_LINES[AgentTurnFailed.PROTOCOL])
    # A yes went out and it is not known what became of it: never "nothing was
    # confirmed", the call may have run.
    return f"{line} {UNKNOWN_IF_RAN}" if confirmed else line
