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
    ProjectNews,
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
# A cancel from the phone can arrive while the bridge is still writing the read-back
# (jarvis-streaming-speech S6): its id is remembered this long, at most this many, so
# that delivered() cancels instead of keeping it.
EARLY_CANCEL_TTL_S = PENDING_TTL_S
MAX_EARLY_CANCELS = 32

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
# D52 (jarvis-general-conversation): a fallback reason, for a project the bridge has
# no copy of. Nothing can be answered read-only, so only what happened is said: the
# same words as when nothing started, except for the connection.
NO_COPY_LINES = {
    **{reason: NOT_STARTED_LINES[reason] for reason in AgentUnavailable.REASONS},
    AgentUnavailable.UNREACHABLE: "The agent host is not reachable right now.",
}

# D53: what became of a repository the turn created, said after the summary (and
# before a read-back, whose question stays last). "Ready" has two forms: the app
# fetches a list from the bridge and will show it, or it shows names typed in its
# settings, where it has to be added by hand.
PROJECT_READY = "It's ready. You can switch to it."
PROJECT_READY_TYPED = "It's ready. Add it to your project list in the app."
PROJECT_SENTENCES = {
    "retrying": "I'm still setting it up at the desk.",
    "needs_desk": "The repository was created, but I could not set it up as a project. "
                  "I passed that on to the coding session.",
    "name_taken": "A different folder with that name exists. "
                  "I passed that on to the coding session.",
}


def project_sentence(news: ProjectNews, *, typed_list: bool) -> str:
    if news.ready:
        return PROJECT_READY_TYPED if typed_list else PROJECT_READY
    return PROJECT_SENTENCES[news.reason]


def _with_news(spoken: str, news: ProjectNews | None, *, typed_list: bool,
               before: bool = False) -> str:
    if news is None:
        return spoken
    sentence = project_sentence(news, typed_list=typed_list)
    if not spoken:
        return sentence
    return f"{sentence} {spoken}" if before else f"{spoken} {sentence}"


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
    # The read-back's id, sent to the phone so it can cancel a read-back it did not
    # play to the end (jarvis-streaming-speech S6). Empty when nothing is read back.
    confirmation_id: str = ""


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

    def take_if(self, project: str, confirmation_id: str) -> tuple[PendingConfirmation, float] | None:
        """Remove the project's pending confirmation only when it has this id; None when
        there is none, it is another one (left as it is), or it is too late."""
        with self._lock:
            entry = self._pending.get(project)
            if entry is None or entry[0].id != confirmation_id:
                return None
            del self._pending[project]
            return entry if self._clock() < entry[1] else None

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
        # delivered() and cancel_read_back() decide under this lock, so a cancel that
        # comes while the read-back is still being written is never lost.
        self._delivery_lock = threading.Lock()
        self._early_cancels: dict[tuple[str, str], float] = {}

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
               speech_seconds: Callable[[], float], *, typed_list: bool = False) -> Outcome:
        """One utterance of the driver for one project.

        `typed_list`: the phone shows project names typed in its settings, not the
        bridge's list (it says so in a header), so a new project is announced with
        "add it to your list" (D53).

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
            return self._turn(project, transcript, typed_list=typed_list)

        pending, deadline = entry
        if said in self._no:
            self._cancel(project, pending)
            return Outcome(CANCELLED)
        if said in self._yes:
            return self._turn(project, transcript,
                              ConfirmAnswer(pending.id, pending.sha256, ANSWER_YES),
                              answered=entry, typed_list=typed_list)
        # Anything else is an ordinary request. It clears the pending call on the
        # agent host like every request, and the agent is told what happened.
        return self._turn(project, f"{NOT_CONFIRMED_LINE}\n\n{transcript}", said=transcript,
                          typed_list=typed_list)

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
        if outcome.to_keep is None:
            return
        confirmation = outcome.to_keep[0]
        with self._delivery_lock:
            cancelled = self._early_cancels.pop((project.name, confirmation.id), None) is not None
            if not cancelled:
                self._pending.put_until(project.name, *outcome.to_keep)
        if cancelled:
            logger.warning("project=%s agent=full: the phone cancelled the read-back before it "
                           "was written to the end; the pending call is cancelled", project.name)
            self._cancel(project, confirmation)

    def cancel_read_back(self, project: ProjectConfig, confirmation_id: str) -> bool:
        """The phone did not play the read-back to its end (a tap, a cut stream, a
        playback error, the app closed): it was not heard, so it may not stay pending.
        The agent host gets a `no`, as for a read-back that could not be written
        (undelivered). True when a pending read-back with this id was cancelled now.

        Idempotent and safe late: an id that is not pending (answered, expired, already
        cancelled, or another read-back) changes nothing pending. When the reply is
        still being written, the id is remembered and delivered() cancels it.
        """
        with self._delivery_lock:
            entry = self._pending.take_if(project.name, confirmation_id)
            if entry is None:
                self._remember_early_cancel(project.name, confirmation_id)
        if entry is None:
            return False
        logger.warning("project=%s agent=full: the phone did not play the read-back to its "
                       "end; the pending call is cancelled", project.name)
        self._cancel(project, entry[0])
        return True

    def _remember_early_cancel(self, project: str, confirmation_id: str) -> None:
        now = time.monotonic()
        for key, until in list(self._early_cancels.items()):
            if until <= now:
                del self._early_cancels[key]
        while len(self._early_cancels) >= MAX_EARLY_CANCELS:
            del self._early_cancels[next(iter(self._early_cancels))]
        self._early_cancels[(project, confirmation_id)] = now + EARLY_CANCEL_TTL_S

    def undelivered(self, project: ProjectConfig, outcome: Outcome) -> None:
        """The reply could not be spoken or sent. A read-back nobody heard is not
        kept here and is cancelled on the agent host."""
        if outcome.to_keep is not None:
            logger.warning("project=%s agent=full: the read-back did not reach the phone; "
                           "the pending call is cancelled", project.name)
            self._cancel(project, outcome.to_keep[0])

    def _turn(self, project: ProjectConfig, prompt: str,
              confirm: ConfirmAnswer | None = None, *, said: str | None = None,
              answered: tuple[PendingConfirmation, float] | None = None,
              typed_list: bool = False) -> Outcome:
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
                return Outcome(SAY_YES_AGAIN, needs_confirmation=True,
                               confirmation_id=answered[0].id)
            news = exc.project
            line = _failure_line(exc, confirmed=confirm is not None)
            return Outcome(_with_news(line, news, typed_list=typed_list), exc.turn_id,
                           projects_changed=news is not None and news.ready)
        return self._outcome(project, turn, typed_list=typed_list)

    def _outcome(self, project: ProjectConfig, turn: AgentTurn, *,
                 typed_list: bool = False) -> Outcome:
        to_keep = None
        if turn.confirmation is not None:
            # Not pending yet: first it has to be spoken (delivered()).
            to_keep = (turn.confirmation, self._pending.deadline(turn.expires_s))
        logger.info("project=%s agent=full turn=%s outcome=%s", project.name, turn.ref or "-",
                    "needs_confirmation" if turn.confirmation is not None else "ok")
        news = turn.project
        spoken = _with_news(turn.spoken, news, typed_list=typed_list,
                            before=turn.confirmation is not None)
        return Outcome(spoken, turn.ref, turn.confirmation is not None, to_keep,
                       projects_changed=news is not None and news.ready,
                       confirmation_id=turn.confirmation.id if turn.confirmation else "")

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
