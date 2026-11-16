"""`full` mode through the real HTTP server, the real remote adapter and a fake
`ssh` with a fake wrapper behind it (jarvis-full-agent task 2.8): a read-back and
a yes, end to end, without an agent host."""
import http.client
import json
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
        # No speech model in these tests: decoding, transcript and detector are stubs.
        self.decoded = []
        samples = object()                               # stands for the decoded array

        def decode(audio_bytes):
            self.decoded.append(audio_bytes)
            return samples

        def transcribe(audio, model_size):
            self.transcribed_from = audio
            return self.heard

        def speech_seconds(audio):
            self.measured_from = audio
            return self.speech
        self.samples = samples
        monkeypatch.setattr(stt, "decode", decode)
        monkeypatch.setattr(stt, "transcribe", transcribe)
        monkeypatch.setattr(stt, "speech_seconds", speech_seconds)
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


def test_the_recording_is_decoded_once_for_transcript_and_detector(bridge):
    bridge.say("what is open?")
    assert bridge.decoded == [b"audio"]
    assert bridge.transcribed_from is bridge.samples and bridge.measured_from is bridge.samples


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


def test_a_read_back_whose_speech_fails_is_cancelled_and_never_pending(bridge, monkeypatch):
    bridge.host.scenario(mode="wrapper", gate=True)

    def broken(text, voice):
        raise RuntimeError("no voice today")
    monkeypatch.setattr(tts, "synthesize", broken)
    response, _ = bridge.say("merge it")
    assert response.status == 500
    assert bridge.kinds() == ["agent", "cancel"]           # the agent host was told no
    monkeypatch.setattr(tts, "synthesize", lambda text, voice: b"WAV:" + text.encode())
    bridge.host.scenario(mode="wrapper")
    bridge.say("yes")
    assert bridge.host.requests()[-1]["confirm"] is None   # an ordinary utterance


def test_a_read_back_that_cannot_be_written_to_the_phone_is_cancelled(bridge, monkeypatch):
    bridge.host.scenario(mode="wrapper", gate=True)
    real = server.Handler._respond_audio

    def gone(self, *args, **kwargs):
        raise BrokenPipeError("the phone hung up")
    monkeypatch.setattr(server.Handler, "_respond_audio", gone)
    try:
        bridge.say("merge it")
    except Exception:  # noqa: BLE001 - the client side of a broken reply
        pass
    monkeypatch.setattr(server.Handler, "_respond_audio", real)
    assert bridge.kinds() == ["agent", "cancel"]
    bridge.host.scenario(mode="wrapper")
    bridge.say("yes")
    assert "confirmed" not in bridge.kinds()


def test_a_yes_to_a_busy_agent_host_can_be_said_again(bridge):
    bridge.host.scenario(mode="wrapper", gate=True)
    bridge.say("merge it")
    bridge.host.scenario(mode="wrapper", error="busy")
    response, said = bridge.say("yes")
    assert said == "WAV:The agent host is busy. Say yes again in a moment."
    assert response.getheader("X-Jarvis-Needs-Confirmation") == "1"
    bridge.host.scenario(mode="wrapper")
    # (The fake clears a pending call only when it handles a request; a busy one
    # is answered before that, as the real wrapper's lock is taken first.)
    _, said = bridge.say("yes")
    assert said == "WAV:Merged." and bridge.kinds() == ["agent", "confirmed"]


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


def test_a_project_only_on_the_agent_host_is_asked_there(bridge):
    # jarvis-general-conversation D52: the agent host decides which projects exist.
    response, said = bridge.say("hello", project="only-on-the-agent-host")
    assert response.status == 200 and said == "WAV:Done on the agent host."
    assert bridge.host.requests()[-1]["project"] == "only-on-the-agent-host"


@pytest.mark.parametrize("name", ["..", ".hidden", "-rf", "a/b", "x" * 65])
def test_a_name_the_agent_host_would_not_accept_is_refused_before_it(bridge, name):
    response, _ = bridge.say("hello", project=name)
    assert response.status == 400
    assert bridge.host.calls() == []


def test_a_project_the_agent_host_does_not_have_and_the_pod_does_not_either(bridge):
    bridge.host.scenario(mode="wrapper", projects=["demo"])
    response, said = bridge.say("hello", project="birdsong")
    assert said == "WAV:The agent host does not have this project."
    assert bridge.readonly_prompts == []


def test_a_project_the_agent_host_does_not_have_falls_back_to_the_pods_copy(bridge):
    bridge.host.scenario(mode="wrapper", projects=["demo"])
    _, said = bridge.say("hello", project="other")
    assert said == ("WAV:The agent host does not have this project, so this answer is "
                    "read-only. It is a voice bridge.")
    assert bridge.readonly_prompts == ["hello"]


@pytest.mark.parametrize("scenario,line", [
    ({"mode": "refused"}, "The agent host is not reachable right now."),
    ({"mode": "wrapper", "error": "disabled"}, "Full mode is switched off on the agent host."),
])
def test_without_a_copy_in_the_pod_there_is_no_read_only_answer(bridge, scenario, line):
    bridge.host.scenario(**scenario)
    _, said = bridge.say("hello", project="only-on-the-agent-host")
    assert said == "WAV:" + line
    assert bridge.readonly_prompts == []


def test_the_pods_copy_is_still_answered_read_only_when_unreachable(bridge):
    bridge.host.scenario(mode="refused")
    _, said = bridge.say("hello", project="demo")
    assert said.startswith("WAV:The agent host could not be reached, so this answer is read-only.")


def _get_projects(bridge):
    conn = http.client.HTTPConnection("127.0.0.1", bridge.httpd.server_address[1], timeout=30)
    conn.request("GET", "/projects", headers={"Authorization": "Bearer test-token"})
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response.status, json.loads(body)


def test_the_list_comes_from_the_agent_host(bridge):
    bridge.host.scenario(mode="wrapper", projects=["jarvis-home", "birdsong", "demo"])
    assert _get_projects(bridge) == (
        200, {"projects": ["birdsong", "demo", "jarvis-home"], "source": "agent_host"})
    assert bridge.host.requests() == [{"v": 1, "list": True}]


@pytest.mark.parametrize("scenario", [
    {"mode": "refused"},
    {"mode": "wrapper", "error": "disabled"},
    {"mode": "wrapper", "old_wrapper": True},
    {"mode": "raw", "stdout": "not json"},
    {"mode": "host_key"},
])
def test_without_the_agent_hosts_list_the_pods_list_is_given(bridge, scenario):
    bridge.host.scenario(**scenario)
    assert _get_projects(bridge) == (200, {"projects": ["demo", "other"], "source": "bridge"})


def test_a_list_that_takes_too_long_is_stopped(bridge, monkeypatch):
    from bridge import remote_adapter
    monkeypatch.setattr(remote_adapter, "LIST_TIMEOUT_S", 0.5)
    bridge.host.scenario(mode="hang")
    assert _get_projects(bridge)[1]["source"] == "bridge"


def test_a_new_project_is_announced_after_the_summary_and_in_a_header(bridge):
    bridge.host.scenario(mode="wrapper", notice="project_ready",
                         spoken="SPOKEN SUMMARY: I created birdsong.")
    response, said = bridge.say("yes")
    assert said == "WAV:I created birdsong. It's ready. You can switch to it."
    assert response.getheader("X-Jarvis-Projects-Changed") == "1"


def test_no_header_without_a_new_project(bridge):
    response, _ = bridge.say("what is open?")
    assert response.getheader("X-Jarvis-Projects-Changed") is None


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
        assert b.decoded == [] and b.transcribed_from == b"audio"   # the bytes, as before
        assert response.getheader("X-Jarvis-Needs-Confirmation") is None
        assert [k for k, _ in response.getheaders()] == [
            "Server", "Date", "Content-Type", "Content-Length", "X-Jarvis-Session-Id",
            "X-Jarvis-Summary"]
        assert b.host.calls() == [] and server.Handler.full is None
    finally:
        b.stop()
