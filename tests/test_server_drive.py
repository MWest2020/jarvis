"""The drive's own words through the real HTTP server (add-drive-mode phase 1, task 1.5):
"where was I" and "note ..." are answered by the bridge without an agent turn, also when
the agent fails or the project is busy; the same words inside a question go to the agent;
the agent's text never triggers one; a control word uses up a pending read-back."""
import http.client
import json
import threading
import urllib.parse

import pytest

from bridge import claude_adapter, journal, server, stt, tts
from bridge.config import MODE_READONLY, BridgeConfig, ProjectConfig
from tests.test_server_full_mode import READBACK, Bridge


class ReadonlyBridge:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp_path = tmp_path
        self.heard = "what is 2 plus 2"
        self.prompts = []
        self.reply = "4\nSPOKEN SUMMARY: The answer is four."
        self.fail = False
        monkeypatch.setattr(stt, "transcribe", lambda audio, model_size: self.heard)
        monkeypatch.setattr(tts, "synthesize", lambda text, voice: b"WAV:" + text.encode())

        def run_turn(**kwargs):
            self.prompts.append(kwargs["prompt"])
            if self.fail:
                raise claude_adapter.ClaudeTurnError("claude is not there")
            return self.reply, "session-123"
        monkeypatch.setattr(claude_adapter, "run_turn", run_turn)
        self.config = BridgeConfig(
            bind_host="127.0.0.1", port=0, token="test-token",
            projects={n: ProjectConfig(n, tmp_path) for n in ("demo", "other")},
            allowed_tools=("Read", "Glob", "Grep"),
            session_store_path=tmp_path / "sessions.json", log_path=tmp_path / "bridge.log",
            whisper_model="base", kokoro_voice="bm_george", claude_timeout_s=30,
            agent_mode=MODE_READONLY,
        )
        self.start()

    def start(self):
        self.httpd = server.make_server(self.config)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def say(self, heard, project="demo"):
        self.heard = heard
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        conn.request("POST", "/turn?" + urllib.parse.urlencode({"project": project}),
                     body=b"audio", headers={"Authorization": "Bearer test-token"})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, data.decode()

    def events(self):
        directory = self.tmp_path / "journal"
        return [json.loads(line) for path in sorted(directory.iterdir())
                for line in path.read_text().splitlines()]


@pytest.fixture
def ro(tmp_path, monkeypatch):
    b = ReadonlyBridge(tmp_path, monkeypatch)
    yield b
    b.stop()


def test_where_was_i_is_the_recap_and_no_agent_turn(ro):
    ro.say("what is 2 plus 2")
    response, said = ro.say("Where was I?")
    assert response.status == 200
    assert said == "WAV:Last, on demo, you asked: what is 2 plus 2. I said: The answer is four."
    assert response.getheader("X-Jarvis-Session-Id") == ""
    assert ro.prompts == ["what is 2 plus 2"]


def test_a_note_is_stored_answered_and_never_sent_to_the_agent(ro):
    response, said = ro.say("Note: check the token rotation tonight.")
    assert (response.status, said) == (200, "WAV:Noted.")
    assert ro.prompts == []
    [note] = [e for e in ro.events() if e["kind"] == "note"]
    assert note["text"] == "check the token rotation tonight." and note["project"] == "demo"


def test_an_empty_note_asks_for_the_note(ro):
    _, said = ro.say("Note.")
    assert said == "WAV:" + journal.NOTE_EMPTY
    assert ro.prompts == [] and not (ro.tmp_path / "journal").exists()


@pytest.mark.parametrize("heard", ["Where was I with the config?", "Can you note the failing test?"])
def test_the_words_inside_a_question_go_to_the_agent(ro, heard):
    _, said = ro.say(heard)
    assert said == "WAV:The answer is four."
    assert ro.prompts == [heard]


def test_the_agents_text_never_triggers_a_word(ro):
    ro.reply = "x\nSPOKEN SUMMARY: Note: where was I. Note this."
    ro.say("what do you say")
    assert [e["kind"] for e in ro.events()] == ["turn"]


def test_every_turn_is_in_the_journal_with_its_session_id(ro):
    ro.say("what is 2 plus 2")
    [turn] = ro.events()
    assert turn["kind"] == "turn" and turn["project"] == "demo"
    assert turn["transcript"] == "what is 2 plus 2"
    assert turn["spoken"] == "The answer is four." and turn["session_id"] == "session-123"


def test_the_recap_works_when_the_agent_fails(ro):
    ro.say("what is 2 plus 2")
    ro.fail = True
    response, _ = ro.say("what else")
    assert response.status == 502
    response, said = ro.say("where was I")
    assert response.status == 200 and said.startswith("WAV:Last, on demo, you asked:")


def test_a_busy_project_still_hears_its_words_and_says_what_runs(ro):
    ro.say("what is 2 plus 2")
    assert server._try_project_lock("demo")
    try:
        response, said = ro.say("where was I")
        assert response.status == 200
        assert said.endswith("A turn on demo is still running.")
        response, said = ro.say("note busy but noted")
        assert (response.status, said) == (200, "WAV:Noted.")
        response, _ = ro.say("a real question")
        assert response.status == 409
    finally:
        server._release_project_lock("demo")
    assert ro.prompts == ["what is 2 plus 2"]


def test_the_recap_does_not_count_its_own_request_as_running(ro):
    ro.say("what is 2 plus 2")
    _, said = ro.say("where was I")
    assert "still running" not in said


def test_a_restart_keeps_the_journal(ro):
    ro.say("what is 2 plus 2")
    ro.say("note before the restart")
    ro.stop()
    ro.start()
    _, said = ro.say("where was I")
    assert said == "WAV:Last, on demo, you asked: what is 2 plus 2. I said: The answer is four."
    assert [e["kind"] for e in ro.events()] == ["turn", "note", "recap"]


def test_a_journal_that_cannot_be_written_never_costs_the_turn(ro, monkeypatch):
    def broken(self, event):
        raise OSError("disk full")
    monkeypatch.setattr(journal.Journal, "_append", broken)
    _, said = ro.say("what is 2 plus 2")
    assert said == "WAV:The answer is four."
    _, said = ro.say("note this")
    assert said == "WAV:" + journal.NOTE_FAILED
    assert ro.prompts == ["what is 2 plus 2"]


# --- full mode -------------------------------------------------------------------


@pytest.fixture
def full(tmp_path, monkeypatch):
    b = Bridge(tmp_path, monkeypatch)
    yield b
    b.stop()


def test_full_mode_the_recap_works_with_the_agent_host_unreachable(full):
    full.say("what is open?")
    full.host.scenario(mode="refused")
    _, said = full.say("where was I")
    assert said == "WAV:Last, on demo, you asked: what is open? I said: Done on the agent host."
    assert len(full.host.calls()) == 1 and full.readonly_prompts == []


def test_full_mode_a_turn_is_journaled_with_the_agent_hosts_turn_id(full):
    full.say("what is open?")
    events = [json.loads(line) for path in (full.config.journal_path).iterdir()
              for line in path.read_text().splitlines()]
    assert events[0]["turn_id"] == "turn-1" and events[0]["spoken"] == "Done on the agent host."


def test_full_mode_a_note_starts_no_turn_on_the_agent_host(full):
    _, said = full.say("note look at the gate rules")
    assert said == "WAV:Noted."
    assert full.host.calls() == []


def test_a_control_word_uses_up_a_read_back_and_a_yes_after_it_releases_nothing(full):
    full.host.scenario(mode="wrapper", gate=True)
    _, said = full.say("merge the jarvis pull request")
    assert said == "WAV:" + READBACK
    _, said = full.say("note ask Mark about the release")
    assert said == "WAV:Noted. " + journal.READ_BACK_DROPPED
    assert full.kinds() == ["agent", "cancel"]              # the agent host was told no
    full.host.scenario(mode="wrapper")
    full.say("yes")
    assert "confirmed" not in full.kinds()
    assert full.host.requests()[-1]["confirm"] is None


def test_the_recap_after_a_yes_names_the_question(full):
    full.host.scenario(mode="wrapper", gate=True)
    full.say("merge the jarvis pull request")
    full.host.scenario(mode="wrapper")
    full.say("Yes.")
    _, said = full.say("where was I")
    assert said == ("WAV:Last, on demo, you asked: merge the jarvis pull request. "
                    "I said: Merged.")
