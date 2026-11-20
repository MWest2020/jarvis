"""The drive journal and the drive's own words (add-drive-mode phase 1, task 1.5): each
word alone and inside a question, a drive boundary, a restart keeps the journal, the
recap, retention and file modes."""
import json
import stat

import pytest

from bridge import journal
from bridge.config import DEFAULT_NOTE_WORDS, DEFAULT_RECAP_WORDS
from bridge.journal import NOTE, RECAP, Journal, Word


def match(text):
    return journal.match(text, recap_words=DEFAULT_RECAP_WORDS, note_words=DEFAULT_NOTE_WORDS)


@pytest.mark.parametrize("said", ["where was I", "Where was I?", "  where, was i.  ",
                                  "What was I doing?"])
def test_the_recap_word_alone(said):
    assert match(said) == Word(RECAP)


@pytest.mark.parametrize("said", ["Where was I with the config?",
                                  "Tell me where was I yesterday",
                                  "where were we", "go on", "what's new", "yes", ""])
def test_the_recap_word_inside_a_question_or_other_words_is_not_one(said):
    assert match(said) is None


@pytest.mark.parametrize("said, text", [
    ("Note: check the token rotation tonight.", "check the token rotation tonight."),
    ("note check the token rotation", "check the token rotation"),
    ("Make a note, call the garage.", "call the garage."),
    ("Take a note - Billbird invoices", "Billbird invoices"),
    ("Note that the index test is flaky", "that the index test is flaky"),
])
def test_a_note_keeps_what_follows_as_transcribed(said, text):
    assert match(said) == Word(NOTE, text)


@pytest.mark.parametrize("said", ["Note.", "note", "Make a note."])
def test_a_note_word_without_a_note_is_an_empty_note(said):
    assert match(said) == Word(NOTE, "")


@pytest.mark.parametrize("said", ["Notes on the design, what are they?",
                                  "Can you note the failing test?",
                                  "No, take the other one", "Nothing"])
def test_note_elsewhere_in_a_question_goes_to_the_agent(said):
    assert match(said) is None


def test_a_note_is_capped():
    assert len(match("note " + "x" * 5000).text) == journal.MAX_NOTE_CHARS


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(tmp_path, clock, gap_min=30, days=30):
    return Journal(tmp_path / "journal", gap_s=gap_min * 60, retention_s=days * 86400,
                   clock=clock)


def test_files_are_owner_only_and_one_json_line_per_event(tmp_path):
    clock = Clock()
    j = make(tmp_path, clock)
    j.record_turn(project="wordsworth", transcript="why does the index test fail?",
                  spoken="The fixture is stale.", session_id="s-1")
    j.add_note(project="wordsworth", text="check the token rotation")
    directory = tmp_path / "journal"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    [path] = directory.iterdir()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [e["kind"] for e in lines] == ["turn", "note"]
    assert lines[0]["session_id"] == "s-1" and lines[0]["project"] == "wordsworth"
    assert lines[1]["text"] == "check the token rotation"


def test_twenty_minutes_later_the_same_drive_continues(tmp_path):
    clock = Clock()
    j = make(tmp_path, clock)
    j.add_note(project="p", text="one")
    clock.t += 20 * 60
    j.add_note(project="p", text="two")
    assert len(j.drives()) == 1
    assert [e["text"] for e in j.events(j.drives()[0])] == ["one", "two"]


def test_after_the_gap_a_new_drive_starts_and_the_old_one_stays(tmp_path):
    clock = Clock()
    j = make(tmp_path, clock)
    j.add_note(project="p", text="evening")
    clock.t += 10 * 3600
    assert j.current_drive() is None
    j.add_note(project="p", text="morning")
    first, second = j.drives()
    assert [e["text"] for e in j.events(first)] == ["evening"]
    assert [e["text"] for e in j.events(second)] == ["morning"]
    assert j.current_drive() == second


def test_a_restart_keeps_the_journal_and_the_open_drive(tmp_path):
    clock = Clock()
    make(tmp_path, clock).record_turn(project="jarvis", transcript="what is open?",
                                      spoken="Two pull requests.")
    clock.t += 60
    again = make(tmp_path, clock)                      # the bridge restarted
    again.add_note(project="jarvis", text="after the restart")
    assert len(again.drives()) == 1
    assert again.recap() == ("Last, on jarvis, you asked: what is open? "
                             "I said: Two pull requests.")


def test_a_line_cut_off_by_a_crash_is_skipped(tmp_path):
    clock = Clock()
    j = make(tmp_path, clock)
    j.add_note(project="p", text="kept")
    [path] = (tmp_path / "journal").iterdir()
    with path.open("a") as fh:
        fh.write('{"t": 1, "kind": "no')
    again = make(tmp_path, clock)
    assert [e["text"] for e in again.events(again.drives()[0])] == ["kept"]


def test_the_recap_is_at_most_three_sentences_and_names_what_runs(tmp_path):
    clock = Clock()
    j = make(tmp_path, clock)
    j.record_turn(project="wordsworth", transcript="Why does the index test fail?",
                  spoken="The fixture is stale.")
    assert j.recap(running=["jarvis"]) == (
        "Last, on wordsworth, you asked: Why does the index test fail? "
        "I said: The fixture is stale. A turn on jarvis is still running.")
    assert j.recap(running=["b", "a", "c"]).endswith("Turns on a, b and c are still running.")


def test_the_recap_names_the_question_not_the_yes(tmp_path):
    clock = Clock()
    j = make(tmp_path, clock)
    j.record_turn(project="jarvis", transcript="merge pull request 21",
                  spoken="Merge pull request 21 on jarvis. Say yes to go ahead.",
                  turn_id="t-1", read_back=True)
    j.record_turn(project="jarvis", transcript="Yes.", spoken="Merged.", turn_id="t-2",
                  answer=True)
    assert j.recap() == "Last, on jarvis, you asked: merge pull request 21. I said: Merged."


def test_in_a_new_drive_the_recap_is_of_the_last_one(tmp_path):
    clock = Clock()
    j = make(tmp_path, clock)
    j.record_turn(project="ratatoskr", transcript="is the digest sent", spoken="Yes, at seven.")
    clock.t += 12 * 3600
    j.record_recap(project="ratatoskr")                # "where was I" opens the new drive
    assert j.recap() == ("Last drive, on ratatoskr, you asked: is the digest sent. "
                         "I said: Yes, at seven.")


def test_an_empty_journal_says_so(tmp_path):
    assert make(tmp_path, Clock()).recap() == journal.NOTHING_TO_RECAP


def test_long_questions_and_answers_are_cut_for_the_ear(tmp_path):
    clock = Clock()
    j = make(tmp_path, clock)
    j.record_turn(project="p", transcript="word " * 100, spoken="answer " * 100)
    recap = j.recap()
    assert recap.count("word") == journal.RECAP_QUESTION_WORDS
    assert recap.count("answer") == journal.RECAP_ANSWER_WORDS


def test_drives_older_than_the_retention_are_deleted(tmp_path):
    clock = Clock()
    j = make(tmp_path, clock, days=30)
    j.add_note(project="p", text="old")
    clock.t += 31 * 86400
    j.add_note(project="p", text="new")                # a new drive prunes
    assert [e["text"] for d in j.drives() for e in j.events(d)] == ["new"]


def test_a_recent_drive_survives_the_pruning(tmp_path):
    clock = Clock()
    j = make(tmp_path, clock, days=30)
    j.add_note(project="p", text="last week")
    clock.t += 7 * 86400
    j.add_note(project="p", text="today")
    assert len(j.drives()) == 2


# --- review of #85 ----------------------------------------------------------------


@pytest.mark.parametrize("said", ["Waar was ik?", "waar was ik", "Wat was ik aan het doen?"])
def test_the_dutch_recap_words_alone(said):
    assert match(said) == Word(RECAP)


@pytest.mark.parametrize("said", ["Waar was ik gebleven met de config?",
                                  "Weet je waar was ik gisteren", "waar waren we"])
def test_the_dutch_recap_words_inside_a_question_are_not_one(said):
    assert match(said) is None


@pytest.mark.parametrize("said, text", [
    ("Noteer: token rotatie checken vanavond.", "token rotatie checken vanavond."),
    ("Notitie: garage bellen", "garage bellen"),
    ("Maak een notitie, Billbird facturen.", "Billbird facturen."),
])
def test_a_dutch_note(said, text):
    assert match(said) == Word(NOTE, text)


@pytest.mark.parametrize("said", ["Notitie over de release, wat staat erin?",
                                  "Kun je noteren wat er faalt?",
                                  "Note the config, is it broken?",
                                  "Nee, de andere"])
def test_a_question_is_never_a_note(said):
    assert match(said) is None


def test_a_line_cut_in_a_multibyte_character_does_not_stop_the_journal(tmp_path):
    clock = Clock()
    j = make(tmp_path, clock)
    j.record_turn(project="p", transcript="wat is open", spoken="Twee pull requests.")
    [path] = (tmp_path / "journal").iterdir()
    with path.open("ab") as fh:
        fh.write('{"t": 1, "kind": "note", "text": "café'.encode()[:-1])   # half an é
    again = make(tmp_path, clock)                      # the bridge restarted
    assert again.recap().startswith("Last, on p, you asked: wat is open.")
    again.add_note(project="p", text="na de crash")    # not glued onto the cut line
    texts = [e.get("text") for e in again.events(again.drives()[0]) if e["kind"] == "note"]
    assert texts == ["na de crash"]
    assert path.read_bytes().endswith(b"}\n")


def test_an_unreadable_journal_directory_is_an_os_error_and_pruning_survives_it(tmp_path):
    import os
    j = make(tmp_path, Clock())
    directory = tmp_path / "journal"
    directory.mkdir(mode=0o700)
    directory.chmod(0o300)                             # writable, not listable
    try:
        if os.access(directory, os.R_OK):              # root reads everything
            pytest.skip("running as root")
        j._prune()                                     # logged, not raised
        with pytest.raises(OSError):                   # the server falls back (test_server_drive)
            make(tmp_path, Clock())
    finally:
        directory.chmod(0o700)


def test_the_recap_is_three_sentences_whatever_the_question_and_reply_held(tmp_path):
    import re
    j = make(tmp_path, Clock())
    j.record_turn(project="jarvis", transcript="Is the build done? And did it pass? Tell me.",
                  spoken="The build is done. It passed. Two warnings. Want the details?")
    recap = j.recap(running=["wordsworth"])
    assert recap == ("Last, on jarvis, you asked: Is the build done? "
                     "I said: The build is done. A turn on wordsworth is still running.")
    assert len(re.split(r"(?<=[.?!])\s+", recap)) == 3


def test_a_drive_past_the_retention_is_not_recapped(tmp_path):
    clock = Clock()
    j = make(tmp_path, clock, days=30)
    j.record_turn(project="old", transcript="q", spoken="a")
    clock.t += 40 * 86400                              # no event since, so nothing pruned
    assert len(j.drives()) == 1
    assert j.recap() == journal.NOTHING_TO_RECAP


def test_secrets_are_redacted_before_they_are_written(tmp_path):
    from bridge.logging_setup import RedactingFilter
    redact = RedactingFilter(("tok-1234567890",)).redact
    j = Journal(tmp_path / "journal", gap_s=1800, retention_s=86400, clock=Clock(),
                redact=redact)
    j.record_turn(project="p", transcript="my token is tok-1234567890",
                  spoken="Key sk-ant-abc_DEF-123 seen.")
    j.add_note(project="p", text="rotate tok-1234567890 and sk-ant-xyz")
    raw = next((tmp_path / "journal").iterdir()).read_text()
    assert "tok-1234567890" not in raw and "sk-ant-" not in raw
    assert raw.count("[REDACTED]") == 4
