"""`full` mode through the real HTTP server, the real remote adapter and a fake
`ssh` with a fake wrapper behind it (jarvis-full-agent task 2.8): a read-back and
a yes, end to end, without an agent host."""
import http.client
import threading
import urllib.parse

import pytest

from bridge import claude_adapter, server, stt, tts
from bridge.config import MODE_FULL, BridgeConfig, ProjectConfig
from tests.agent_host_fake import FakeAgentHost

READBACK = "Merge pull request 21 on jarvis. Checks green, one approving review. Say yes to go ahead."


class Bridge:
    def __init__(self, tmp_path, monkeypatch, *, mode=MODE_FULL):
        self.host = FakeAgentHost(tmp_path / "agent-host")
        self.host.install(monkeypatch)
        self.heard = "what is open?"
        self.speech = 0.6
        self.readonly_prompts = []
        monkeypatch.setattr(stt, "transcribe", lambda audio_bytes, model_size: self.heard)
        monkeypatch.setattr(stt, "speech_seconds", lambda audio_bytes: self.speech)
        monkeypatch.setattr(tts, "synthesize", lambda text, voice: b"WAV:" + text.encode())

        def readonly_turn(**kwargs):
            self.readonly_prompts.append(kwargs["prompt"])
            return "full reply\nSPOKEN SUMMARY: It is a voice bridge.", "session-ro"
        monkeypatch.setattr(claude_adapter, "run_turn", readonly_turn)

        for name in ("demo", "other"):
            (tmp_path / name).mkdir(exist_ok=True)
        self.config = BridgeConfig(
            bind_host="127.0.0.1", port=0, token="test-token",
            projects={n: ProjectConfig(n, tmp_path / n) for n in ("demo", "other")},
            allowed_tools=("Read", "Glob", "Grep"),
            session_store_path=tmp_path / "sessions.json", log_path=tmp_path / "bridge.log",
            whisper_model="base", kokoro_voice="af_heart", claude_timeout_s=30,
            agent_mode=mode, agent_host=self.host.config() if mode == MODE_FULL else None,
        )
        self.httpd = None
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
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=30)
        conn.request("POST", "/turn?" + urllib.parse.urlencode({"project": project}),
                     body=b"audio", headers={"Authorization": "Bearer test-token"})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, data.decode()

    def kinds(self):
        return [t["kind"] for t in self.host.turns()]


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    b = Bridge(tmp_path, monkeypatch)
    yield b
    b.stop()


def test_a_full_turn_is_spoken_and_carries_the_agent_hosts_turn_id(bridge):
    response, said = bridge.say("what is open?")
    assert response.status == 200
    assert said == "WAV:Done on the agent host."
    assert response.getheader("X-Jarvis-Session-Id") == "turn-1"
    assert response.getheader("X-Jarvis-Needs-Confirmation") is None
    assert bridge.readonly_prompts == []
    assert bridge.host.requests() == [
        {"v": 1, "project": "demo", "prompt": "what is open?", "confirm": None}]


def test_yes_after_a_read_back_runs_the_call_once(bridge):
    bridge.host.scenario(mode="wrapper", gate=True)
    response, said = bridge.say("merge the jarvis pull request")
    assert said == "WAV:" + READBACK                       # the read-back and nothing else
    assert response.getheader("X-Jarvis-Needs-Confirmation") == "1"
    assert urllib.parse.unquote(response.getheader("X-Jarvis-Summary")) == READBACK

    bridge.host.scenario(mode="wrapper")
    response, said = bridge.say("Yes.")
    assert said == "WAV:Merged."
    assert response.getheader("X-Jarvis-Needs-Confirmation") is None
    assert bridge.kinds() == ["agent", "confirmed"]

    bridge.say("yes")                                      # a second yes releases nothing
    assert bridge.kinds() == ["agent", "confirmed", "agent"]


def test_no_cancels_on_the_agent_host_and_starts_no_agent_turn(bridge):
    bridge.host.scenario(mode="wrapper", gate=True)
    bridge.say("merge it")
    response, said = bridge.say("No.")
    assert said == "WAV:Cancelled."
    assert bridge.kinds() == ["agent", "cancel"]
    bridge.host.scenario(mode="wrapper")
    bridge.say("yes")
    assert bridge.kinds() == ["agent", "cancel", "agent"]


def test_something_else_cancels_and_goes_to_the_agent_as_a_request(bridge):
    bridge.host.scenario(mode="wrapper", gate=True)
    bridge.say("merge it")
    bridge.host.scenario(mode="wrapper")
    bridge.say("yes but wait for the build")
    assert bridge.host.requests()[1]["confirm"] is None
    assert bridge.host.requests()[1]["prompt"].endswith("\n\nyes but wait for the build")
    bridge.say("yes")
    assert "confirmed" not in bridge.kinds()


def test_a_short_noise_transcribed_as_yes_does_not_confirm(bridge):
    bridge.host.scenario(mode="wrapper", gate=True)
    bridge.say("merge it")
    bridge.speech = 0.1
    _, said = bridge.say("Yes.")
    assert said == "WAV:" + server.NOT_HEARD
    assert bridge.kinds() == ["agent"] and len(bridge.host.calls()) == 1


@pytest.mark.parametrize("heard", ["...", "\u266a", " you"])
def test_a_noise_or_a_non_word_starts_no_full_turn(bridge, heard):
    bridge.speech = 0.1 if heard.strip() == "you" else 0.6
    response, said = bridge.say(heard)
    assert (response.status, said) == (200, "WAV:" + server.NOT_HEARD)
    assert bridge.host.calls() == [] and bridge.readonly_prompts == []


def test_a_yes_whose_connection_breaks_is_not_called_unconfirmed(bridge):
    bridge.host.scenario(mode="wrapper", gate=True)
    bridge.say("merge it")
    bridge.host.scenario(mode="drop")
    _, said = bridge.say("yes")
    assert said.endswith("I do not know whether it ran. Check at the desk.")
    assert "Nothing was confirmed" not in said and bridge.readonly_prompts == []


def test_a_yes_for_another_project_confirms_nothing(bridge):
    bridge.host.scenario(mode="wrapper", gate=True)
    bridge.say("merge it", project="demo")
    bridge.host.scenario(mode="wrapper")
    bridge.say("yes", project="other")
    assert bridge.host.requests()[1] == {"v": 1, "project": "other", "prompt": "yes",
                                         "confirm": None}


def test_a_restart_between_read_back_and_yes_releases_nothing(bridge):
    bridge.host.scenario(mode="wrapper", gate=True)
    response, _ = bridge.say("merge it")
    assert response.getheader("X-Jarvis-Needs-Confirmation") == "1"
    bridge.stop()
    bridge.start()                                         # the bridge restarted
    bridge.host.scenario(mode="wrapper")
    bridge.say("yes")
    assert bridge.host.requests()[1]["confirm"] is None
    assert bridge.kinds() == ["agent", "agent"]


def test_a_yes_too_late_on_the_agent_host_is_said_so(bridge):
    bridge.host.scenario(mode="wrapper", gate=True)
    bridge.say("merge it")
    bridge.host.scenario(mode="wrapper", host_expired=True)
    response, said = bridge.say("yes")
    assert (response.status, said) == (200, "WAV:That took too long. Ask again.")
    assert bridge.kinds() == ["agent"]
    assert bridge.readonly_prompts == []


def test_an_unreachable_agent_host_is_answered_read_only_and_says_so(bridge):
    bridge.host.scenario(mode="refused")
    response, said = bridge.say("what is this project?")
    assert said == ("WAV:The agent host could not be reached, so this answer is read-only. "
                    "It is a voice bridge.")
    assert response.getheader("X-Jarvis-Session-Id") == "session-ro"
    assert bridge.readonly_prompts == ["what is this project?"]


def test_the_kill_switch_is_answered_read_only_and_says_so(bridge):
    bridge.host.scenario(mode="wrapper", error="disabled")
    _, said = bridge.say("what is this project?")
    assert said.startswith("WAV:Full mode is switched off on the agent host, so this answer "
                           "is read-only.")


def test_a_connection_lost_during_the_turn_is_reported_and_not_run_again(bridge):
    bridge.host.scenario(mode="drop")
    response, said = bridge.say("merge it")
    assert response.status == 200
    assert "I did not run it again" in said
    assert bridge.readonly_prompts == [] and len(bridge.host.calls()) == 1


def test_a_busy_agent_host_is_spoken(bridge):
    bridge.host.scenario(mode="wrapper", error="busy")
    _, said = bridge.say("do something")
    assert said == "WAV:The agent host is busy. Try again in a moment."
    assert bridge.readonly_prompts == []


def test_a_project_the_bridge_does_not_know_is_refused_before_the_agent_host(bridge):
    response, _ = bridge.say("hello", project="only-on-the-agent-host")
    assert response.status == 400
    assert bridge.host.calls() == []


def test_silence_leaves_a_read_back_waiting(bridge):
    bridge.host.scenario(mode="wrapper", gate=True)
    bridge.say("merge it")
    _, said = bridge.say("   ")
    assert said == "WAV:" + server.NOT_HEARD
    bridge.say("yes")
    assert bridge.kinds() == ["agent", "confirmed"]


def test_the_log_gets_the_turn_id_and_never_the_request_or_the_key(bridge, caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="bridge"):
        bridge.say("the secret plan is to merge")
    on_stdout = [r.getMessage() for r in caplog.records if not getattr(r, "file_only", False)]
    assert any("agent=full turn=turn-1 outcome=ok" in line for line in on_stdout)
    text = "\n".join(on_stdout)
    assert "secret plan" not in text and "id_ed25519" not in text and '"prompt"' not in text


def test_readonly_mode_never_connects_and_has_no_new_header(tmp_path, monkeypatch):
    b = Bridge(tmp_path, monkeypatch, mode="readonly")
    try:
        response, said = b.say("yes")
        assert said == "WAV:It is a voice bridge."
        assert response.getheader("X-Jarvis-Needs-Confirmation") is None
        assert [k for k, _ in response.getheaders()] == [
            "Server", "Date", "Content-Type", "Content-Length", "X-Jarvis-Session-Id",
            "X-Jarvis-Summary"]
        assert b.host.calls() == [] and server.Handler.full is None
    finally:
        b.stop()
