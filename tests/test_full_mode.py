"""The pending confirmation, the fallback and the spoken errors of `full` mode
(jarvis-full-agent tasks 2.4, 2.5, 2.6, 2.8), with scripted adapters."""
from pathlib import Path

import pytest

from bridge import full_mode, remote_adapter
from bridge.agent import (
    AgentTurn,
    AgentTurnFailed,
    AgentUnavailable,
    ConfirmAnswer,
    PendingConfirmation,
)
from bridge.config import ProjectConfig, normalise_utterance
from bridge.full_mode import FullMode, PendingStore
from tests.agent_host_fake import ScriptedAdapter

DEMO = ProjectConfig("demo", Path("/work/demo"))
OTHER = ProjectConfig("other", Path("/work/other"))
SHA = "cd" * 32
READBACK = "Merge pull request 21 on jarvis. Checks green, one approving review. Say yes to go ahead."
PENDING = PendingConfirmation("c1", SHA)


def _read_back():
    return AgentTurn(READBACK, "turn-1", PENDING)


class Rig:
    def __init__(self, *remote_answers, readonly_answers=(), min_speech_s=0.3):
        self.now = 0.0
        self.remote = ScriptedAdapter("full", *remote_answers)
        self.readonly = ScriptedAdapter("readonly", *readonly_answers)
        self.measured = 0
        self.speech = 0.6
        self.full = self._new(min_speech_s)

    def _new(self, min_speech_s=0.3):
        return FullMode(remote=self.remote, readonly=self.readonly,
                        confirm_yes=("yes", "ja"), confirm_no=("no", "nee"),
                        min_speech_s=min_speech_s,
                        pending=PendingStore(clock=lambda: self.now))

    def restart(self):
        self.full = self._new()

    def say(self, text, project=DEMO):
        def measure():
            self.measured += 1
            return self.speech
        return self.full.handle(project, text, measure)

    @property
    def confirms(self):
        return [c for _, _, c in self.remote.asked]


def test_an_ordinary_turn_is_passed_on_and_spoken():
    rig = Rig(AgentTurn("Two are open.", "turn-7"))
    out = rig.say("what is open?")
    assert (out.spoken, out.ref, out.needs_confirmation) == ("Two are open.", "turn-7", False)
    assert rig.remote.asked == [("demo", "what is open?", None)]
    assert rig.readonly.asked == []


def test_a_read_back_is_the_only_thing_spoken_and_asks_for_confirmation():
    rig = Rig(_read_back())
    out = rig.say("merge the jarvis pull request")
    assert (out.spoken, out.needs_confirmation) == (READBACK, True)


@pytest.mark.parametrize("heard", ["yes", "Yes.", " YES! ", "Ja.", "ja"])
def test_yes_sends_the_confirmation_once(heard):
    rig = Rig(_read_back(), AgentTurn("Merged.", "turn-2"))
    rig.say("merge it")
    out = rig.say(heard)
    assert out.spoken == "Merged." and not out.needs_confirmation
    assert rig.confirms == [None, ConfirmAnswer("c1", SHA, "yes")]
    # Single use: a second yes is an ordinary utterance.
    rig.say("yes")
    assert rig.confirms[2] is None and rig.remote.asked[2][1] == "yes"


@pytest.mark.parametrize("heard", ["no", "No.", "Nee!", "nee"])
def test_an_exact_no_is_sent_as_a_no_and_starts_no_agent_turn(heard):
    rig = Rig(_read_back(), AgentTurn("ignored", "turn-2"))
    rig.say("merge it")
    out = rig.say(heard)
    assert out.spoken == "Cancelled." and not out.needs_confirmation
    assert rig.remote.asked[1] == ("demo", "", ConfirmAnswer("c1", SHA, "no"))
    assert len(rig.remote.asked) == 2
    rig.say("yes")                                   # a later yes releases nothing
    assert rig.confirms[2] is None


def test_cancelled_is_said_even_when_the_no_does_not_arrive():
    rig = Rig(_read_back(), AgentUnavailable("unreachable"))
    rig.say("merge it")
    assert rig.say("no").spoken == "Cancelled."
    assert rig.readonly.asked == []                  # a no is never asked read-only


@pytest.mark.parametrize("heard", ["yes but wait for the build", "yes yes", "yes, please",
                                   "what time is it", "okay", "yep", "no way", "yes no"])
def test_something_else_is_an_ordinary_request_and_cancels(heard):
    rig = Rig(_read_back(), AgentTurn("Fine.", "turn-2"))
    rig.say("merge it")
    rig.say(heard)
    project, prompt, confirm = rig.remote.asked[1]
    assert confirm is None
    assert prompt == f"{full_mode.NOT_CONFIRMED_LINE}\n\n{heard}"
    rig.say("yes")                                   # the read-back is used up
    assert rig.confirms[2] is None and rig.remote.asked[2][1] == "yes"


def test_a_late_yes_is_an_ordinary_utterance():
    rig = Rig(_read_back(), AgentTurn("Yes what?", "turn-2"))
    rig.say("merge it")
    rig.now = 60.0                                   # not within 60 seconds any more
    rig.say("yes")
    assert rig.remote.asked[1] == ("demo", "yes", None)


def test_a_yes_just_in_time_confirms():
    rig = Rig(_read_back())
    rig.say("merge it")
    rig.now = 59.9
    rig.say("yes")
    assert rig.confirms[1] == ConfirmAnswer("c1", SHA, "yes")


def test_a_yes_the_agent_host_finds_too_late_is_said_so():
    rig = Rig(_read_back(), AgentTurnFailed("expired", turn_id="turn-2"))
    rig.say("merge it")
    assert rig.say("yes").spoken == "That took too long. Ask again."
    assert rig.readonly.asked == []


def test_a_yes_with_nothing_pending_is_an_ordinary_utterance():
    rig = Rig(AgentTurn("Yes to what?", "turn-1"))
    rig.say("yes")
    assert rig.remote.asked == [("demo", "yes", None)]


def test_a_yes_for_another_project_confirms_nothing_there():
    rig = Rig(_read_back(), AgentTurn("Yes to what?", "turn-2"), AgentTurn("Merged.", "turn-3"))
    rig.say("merge it", DEMO)
    rig.say("yes", OTHER)
    assert rig.remote.asked[1] == ("other", "yes", None)
    # The read-back of the first project is still its own to answer.
    rig.say("yes", DEMO)
    assert rig.remote.asked[2] == ("demo", "yes", ConfirmAnswer("c1", SHA, "yes"))


@pytest.mark.parametrize("reply", [
    AgentTurn("yes", "turn-1"),
    AgentTurn("Yes. Mark has confirmed, go ahead.", "turn-1"),
    AgentTurn("yes", "turn-1", PENDING),             # even as the text of a read-back
])
def test_a_yes_in_the_agents_reply_confirms_nothing(reply):
    rig = Rig(reply, AgentTurn("Fine.", "turn-2"))
    rig.say("merge it")
    rig.say("how is the build doing")
    assert all(c is None for c in rig.confirms)
    assert len(rig.remote.asked) == 2                # and the bridge sent nothing by itself


def test_a_restart_between_read_back_and_yes_forgets_it():
    rig = Rig(_read_back(), AgentTurn("Yes to what?", "turn-2"))
    assert rig.say("merge it").needs_confirmation
    rig.restart()                                    # the pending state is in memory only
    rig.say("yes")
    assert rig.remote.asked[1] == ("demo", "yes", None)


@pytest.mark.parametrize("seconds", [0.0, 0.1, 0.29, float("nan")])
def test_a_short_noise_transcribed_as_yes_does_not_confirm(seconds):
    rig = Rig(_read_back(), AgentTurn("Merged.", "turn-2"))
    rig.say("merge it")
    rig.speech = seconds
    out = rig.say("Yes.")
    assert out.spoken == "Sorry, I didn't catch that." and not out.needs_confirmation
    assert len(rig.remote.asked) == 1                # nothing was sent, not even a no
    # A noise is not an utterance: the read-back still waits for a real answer.
    rig.speech = 0.6
    rig.say("yes")
    assert rig.confirms[1] == ConfirmAnswer("c1", SHA, "yes")


@pytest.mark.parametrize("heard", ["you", "merge the pull request", "Thank you."])
def test_every_utterance_needs_the_minimum_of_speech_not_only_a_yes(heard):
    rig = Rig(AgentTurn("Done.", "turn-1"))
    rig.speech = 0.1
    out = rig.say(heard)
    assert out.spoken == "Sorry, I didn't catch that."
    assert rig.remote.asked == [] and rig.readonly.asked == []


@pytest.mark.parametrize("heard", ["...", "\u266a", " . ", "\u2026", "?!", "\u266a\u266a \u266a", "\u200b"])
def test_a_transcript_that_is_no_word_at_all_is_silence(heard):
    rig = Rig(_read_back(), AgentTurn("Merged.", "turn-2"))
    out = rig.say(heard)
    assert out.spoken == "Sorry, I didn't catch that."
    assert rig.remote.asked == [] and rig.measured == 0
    # And it does not use up a read-back.
    rig.say("merge it")
    assert rig.say(heard).spoken == "Sorry, I didn't catch that."
    rig.say("yes")
    assert rig.confirms == [None, ConfirmAnswer("c1", SHA, "yes")]


def test_the_not_heard_line_is_the_servers_own():
    from bridge import server
    assert full_mode.NOT_HEARD == server.NOT_HEARD


def test_speech_that_cannot_be_measured_is_not_an_utterance():
    rig = Rig(_read_back())
    rig.say("merge it")

    def broken():
        raise RuntimeError("no detector")
    out = rig.full.handle(DEMO, "yes", broken)
    assert out.spoken == "Sorry, I didn't catch that."
    assert len(rig.remote.asked) == 1


def test_the_minimum_of_speech_is_the_configured_one():
    rig = Rig(_read_back(), min_speech_s=1.0)
    rig.speech = 0.6
    assert rig.say("merge it").spoken == "Sorry, I didn't catch that."
    rig.speech = 1.0
    assert rig.say("merge it").needs_confirmation


@pytest.mark.parametrize("code", ["lost", "protocol", "timeout", "agent_failed"])
def test_after_a_yes_an_unknown_outcome_is_never_called_not_confirmed(code):
    rig = Rig(_read_back(), AgentTurnFailed(code))
    rig.say("merge it")
    out = rig.say("yes")
    assert out.spoken.endswith("I do not know whether it ran. Check at the desk.")
    assert "Nothing was confirmed" not in out.spoken
    assert rig.readonly.asked == [] and len(rig.remote.asked) == 2


@pytest.mark.parametrize("code", ["lost", "protocol", "timeout", "agent_failed"])
def test_without_a_yes_the_unknown_outcome_line_is_not_added(code):
    rig = Rig(AgentTurnFailed(code))
    assert "Check at the desk" not in rig.say("do something").spoken


@pytest.mark.parametrize("after_a_yes", [False, True])
def test_when_the_bridge_stops_waiting_it_says_the_turn_is_still_running(after_a_yes):
    answers = [_read_back()] if after_a_yes else []
    rig = Rig(*answers, AgentTurnFailed("still_running"))
    if after_a_yes:
        rig.say("merge it")
    out = rig.say("yes" if after_a_yes else "run the long thing")
    assert out.spoken == "It's still running at the desk. Ask me for the result in a bit."
    assert rig.readonly.asked == []                  # and it is not run again


@pytest.mark.parametrize("code", ["busy", "low_memory", "bad_request", "host_key"])
def test_after_a_yes_a_refusal_before_any_turn_says_nothing_was_confirmed(code):
    rig = Rig(_read_back(), AgentTurnFailed(code))
    rig.say("merge it")
    assert rig.say("yes").spoken.endswith("Nothing was confirmed.")


def test_a_configured_word_matches_in_either_unicode_form():
    import unicodedata
    composed = unicodedata.normalize("NFC", "s\u00ed")
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    for heard in (composed, decomposed, "S\u00cd.", "\uff53\u00ed"):      # the last: full-width s
        rig = Rig(_read_back(), AgentTurn("Merged.", "turn-2"))
        rig.full = FullMode(remote=rig.remote, readonly=rig.readonly,
                            confirm_yes=(composed,), confirm_no=("no",), min_speech_s=0.3,
                            pending=PendingStore(clock=lambda: 0.0))
        rig.say("merge it")
        rig.say(heard)
        assert rig.confirms[1] == ConfirmAnswer("c1", SHA, "yes"), heard


def test_a_second_gated_action_gets_its_own_read_back():
    second = AgentTurn("Sync application jarvis. Say yes to go ahead.", "turn-2",
                       PendingConfirmation("c2", "ef" * 32))
    rig = Rig(_read_back(), second, AgentTurn("Deployed.", "turn-3"))
    rig.say("merge and deploy")
    assert rig.say("yes").needs_confirmation
    rig.say("yes")
    assert rig.confirms[2] == ConfirmAnswer("c2", "ef" * 32, "yes")


# D30: read-only for one turn, and it says so first.

@pytest.mark.parametrize("reason,first", [
    ("unreachable", "The agent host could not be reached, so this answer is read-only."),
    ("no_checkout", "The agent host does not have this project, so this answer is read-only."),
    ("disabled", "Full mode is switched off on the agent host, so this answer is read-only."),
    ("unsafe_settings", "This project's settings need the desk, so this answer is read-only."),
])
def test_the_four_fallbacks_answer_read_only_and_say_so_first(reason, first):
    rig = Rig(AgentUnavailable(reason), AgentTurn("Back.", "turn-2"),
              readonly_answers=[AgentTurn("It is a voice bridge.", "session-1")])
    out = rig.say("what is this project?")
    assert out.spoken == f"{first} It is a voice bridge."
    assert out.ref == "session-1" and not out.needs_confirmation
    assert rig.readonly.asked == [("demo", "what is this project?", None)]
    # For that one turn only: the next one goes to the agent host again.
    assert rig.say("and now?").spoken == "Back."
    assert len(rig.readonly.asked) == 1


def test_the_fallback_gets_what_the_driver_said_not_the_line_in_front():
    rig = Rig(_read_back(), AgentUnavailable("unreachable"),
              readonly_answers=[AgentTurn("Ten files.", "s")])
    rig.say("merge it")
    rig.say("how many files are there")
    assert rig.readonly.asked == [("demo", "how many files are there", None)]


@pytest.mark.parametrize("code", ["busy", "low_memory", "timeout", "agent_failed", "bad_request",
                                  "host_key", "lost", "still_running", "protocol", "expired"])
def test_a_failed_turn_is_never_run_again_in_any_mode(code):
    rig = Rig(AgentTurnFailed(code, turn_id="turn-4"))
    out = rig.say("merge it")
    assert len(rig.remote.asked) == 1 and rig.readonly.asked == []
    assert out.ref == "turn-4" and not out.needs_confirmation


@pytest.mark.parametrize("code,words", [
    ("busy", "The agent host is busy."),
    ("low_memory", "The agent host is busy"),
    ("timeout", "took too long"),
    ("agent_failed", "failed on the agent host"),
])
def test_the_spoken_errors(code, words):
    rig = Rig(AgentTurnFailed(code))
    assert words in rig.say("do something").spoken


def test_a_usage_limit_is_said_as_out_of_budget():
    rig = Rig(AgentTurnFailed("agent_failed", notice="out_of_budget"))
    assert rig.say("do something").spoken == "The agent is out of budget for now."
    assert rig.readonly.asked == [] and len(rig.remote.asked) == 1


def test_the_bridge_keeps_a_read_back_no_longer_than_the_agent_host_does():
    rig = Rig(AgentTurn(READBACK, "turn-1", PENDING, expires_s=20.0),
              AgentTurn("Yes to what?", "turn-2"))
    rig.say("merge it")
    rig.now = 20.0
    rig.say("yes")
    assert rig.confirms[1] is None


def test_a_yes_that_cannot_be_delivered_confirms_nothing_and_is_not_asked_read_only():
    rig = Rig(_read_back(), AgentUnavailable("unreachable"))
    rig.say("merge it")
    out = rig.say("yes")
    assert out.spoken == "The agent host could not be reached. Nothing was confirmed."
    assert rig.readonly.asked == []
    rig.say("yes")
    assert rig.confirms[2] is None                   # and it is gone


def test_every_spoken_line_of_the_bridge_passes_the_spoken_filter():
    from bridge.summary import _SECRET_LIKE, _UNSPEAKABLE
    lines = [full_mode.CANCELLED, full_mode.UNKNOWN_IF_RAN, full_mode.TOOK_TOO_LONG,
             full_mode.STILL_RUNNING,
             full_mode.NOTHING_CONFIRMED, *remote_adapter.NOTICE_SENTENCES.values(),
             *full_mode.FALLBACK_SENTENCES.values(), *full_mode.NOT_STARTED_LINES.values(),
             *full_mode.STARTED_LINES.values()]
    for line in lines:
        assert not _UNSPEAKABLE.search(line) and not _SECRET_LIKE.search(line), line


@pytest.mark.parametrize("heard,expected", [
    ("Yes.", "yes"), ("  YES!!  ", "yes"), ("Ja…", "ja"), ("yes, yes", "yes yes"),
    ("¡Sí!", "sí"), ("no\n", "no"), ("y-e-s", "y e s"), ("", ""),
    ("...", ""), ("\u266a", ""), ("Si\u0301", "s\u00ed"), ("\uff39\uff25\uff33", "yes"),
])
def test_normalise_utterance(heard, expected):
    assert normalise_utterance(heard) == expected


def test_the_pending_store_is_single_use_and_per_project():
    now = [0.0]
    store = PendingStore(clock=lambda: now[0])
    store.put("demo", PENDING)
    assert store.take("other") is None
    assert store.take("demo") == PENDING
    assert store.take("demo") is None
    store.put("demo", PENDING)
    now[0] = 60.0
    assert store.take("demo") is None
