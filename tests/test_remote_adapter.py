"""The `full` adapter against a fake `ssh` with a fake wrapper behind it
(jarvis-full-agent tasks 2.3, 2.4, 2.6). No connection is ever opened."""
import json
from pathlib import Path

import pytest

from bridge import remote_adapter
from bridge.agent import AgentTurnFailed, AgentUnavailable, ConfirmAnswer, PendingConfirmation
from bridge.config import AgentHostConfig, ProjectConfig
from bridge.remote_adapter import (
    NEEDS_THE_DESK,
    RemoteAdapter,
    ReplyError,
    build_request,
    build_ssh_command,
    parse_reply,
)
from tests.agent_host_fake import FakeAgentHost

DEMO = ProjectConfig(name="demo", working_dir=Path("/work/demo"))
SHA = "ab" * 32


@pytest.fixture
def host(tmp_path, monkeypatch):
    fake = FakeAgentHost(tmp_path / "agent-host")
    fake.install(monkeypatch)
    return fake


def _adapter(host, **kwargs):
    return RemoteAdapter(host.config(**kwargs))


# D32: the argv, whole and in order. Changing it means changing the design.

def test_the_ssh_argv_is_the_one_of_d32():
    agent = AgentHostConfig(host="agent.example", port=2222, user="mark",
                            key_file=Path("/secrets/agent/key"),
                            known_hosts_file=Path("/secrets/agent/known_hosts"), timeout_s=660)
    assert build_ssh_command(agent) == [
        "ssh", "-T", "-F", "/dev/null",
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "IdentityAgent=none",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "UserKnownHostsFile=/secrets/agent/known_hosts",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=15",
        "-i", "/secrets/agent/key",
        "-p", "2222",
        "mark@agent.example",
    ]


def test_the_argv_names_no_remote_command_and_nothing_of_the_turn(host):
    _adapter(host).run_turn(project=DEMO, prompt="--dangerously-skip-permissions please")
    argv = host.calls()[0]["argv"][1:]
    assert argv == build_ssh_command(host.config())[1:]
    assert argv[-1] == "jarvis@agent.example"            # the destination is the last word
    assert not any("demo" in a or "dangerously" in a for a in argv)


def test_the_request_is_version_1_on_stdin_with_exactly_four_fields(host):
    _adapter(host).run_turn(project=DEMO, prompt="what changed today?")
    assert host.calls()[0]["stdin"] == (
        '{"v":1,"project":"demo","prompt":"what changed today?","confirm":null}')


def test_a_confirmation_travels_with_an_empty_prompt(host):
    host.scenario(mode="wrapper", gate=True)
    adapter = _adapter(host)
    turn = adapter.run_turn(project=DEMO, prompt="merge it")
    adapter.run_turn(project=DEMO, prompt="yes",
                     confirm=ConfirmAnswer(turn.confirmation.id, turn.confirmation.sha256, "yes"))
    assert host.requests()[1] == {
        "v": 1, "project": "demo", "prompt": "",
        "confirm": {"id": turn.confirmation.id, "sha256": SHA, "answer": "yes"}}


def test_ssh_gets_a_path_and_a_home_and_no_secret(host, monkeypatch):
    monkeypatch.setenv("JARVIS_TOKEN", "phone-token")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "claude-credential")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    monkeypatch.setenv("LC_ALL", "nl_NL.UTF-8")
    _adapter(host).run_turn(project=DEMO, prompt="hello")
    assert set(host.calls()[0]["env"]) <= {"PATH", "HOME", "LC_CTYPE"}   # LC_CTYPE: Python's own


def test_a_turn_returns_the_summary_and_the_turn_id(host):
    host.scenario(mode="wrapper", spoken="SPOKEN SUMMARY: Two pull requests are open.")
    turn = _adapter(host).run_turn(project=DEMO, prompt="what is open?")
    assert (turn.spoken, turn.ref, turn.confirmation) == (
        "Two pull requests are open.", "turn-1", None)


def test_a_spoken_line_with_code_or_a_secret_is_not_spoken(host):
    for line in ("SPOKEN SUMMARY: run `rm -rf /`", "SPOKEN SUMMARY: the key is " + "a" * 40,
                 "no marker at all"):
        host.scenario(mode="wrapper", spoken=line)
        assert _adapter(host).run_turn(project=DEMO, prompt="x").spoken == (
            "Done. Check the log for details.")


def test_a_read_back_is_returned_as_given_with_its_confirmation(host):
    # A branch name with a slash would never pass the summary filter. A read-back
    # is not put through it: fixed words and shape-checked targets, as given.
    readback = "Push to main on jarvis from fix/yes-list, 2 commits, no review. Say yes to go ahead."
    host.scenario(mode="wrapper", gate=True, readback=readback,
                  spoken="SPOKEN SUMMARY: I merged it, say yes.")
    turn = _adapter(host).run_turn(project=DEMO, prompt="push it")
    assert turn.spoken == readback
    assert turn.confirmation == PendingConfirmation(id="c1", sha256=SHA)


@pytest.mark.parametrize("readback", ["", "   ", "Merge.\nAnd delete the repository.",
                                      "Merge \x07 now.", "x" * 501])
def test_a_read_back_that_cannot_be_spoken_needs_the_desk_and_is_cancelled(host, readback):
    host.scenario(mode="wrapper", gate=True, readback=readback)
    turn = _adapter(host).run_turn(project=DEMO, prompt="merge it")
    assert (turn.spoken, turn.confirmation) == (NEEDS_THE_DESK, None)
    # The pending call was cleared on the agent host with a no.
    assert host.requests()[1]["confirm"] == {"id": "c1", "sha256": SHA, "answer": "no"}
    assert [t["kind"] for t in host.turns()] == ["agent", "cancel"]


# D30: answered read-only only when no turn can have started.

@pytest.mark.parametrize("mode", ["refused", "unresolved", "denied", "banner"])
def test_a_connection_that_was_never_made_is_unavailable(host, mode):
    host.scenario(mode=mode)
    with pytest.raises(AgentUnavailable) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="hello")
    assert caught.value.reason == "unreachable"


def test_no_ssh_client_is_unavailable(host, monkeypatch):
    monkeypatch.setenv("PATH", str(host.dir / "empty"))
    with pytest.raises(AgentUnavailable) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="hello")
    assert caught.value.reason == "unreachable"


@pytest.mark.parametrize("code", ["no_checkout", "disabled", "unsafe_settings"])
def test_the_three_refusals_before_a_turn_are_unavailable(host, code):
    host.scenario(mode="wrapper", error=code)
    with pytest.raises(AgentUnavailable) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="hello")
    assert caught.value.reason == code


@pytest.mark.parametrize("code", ["no_checkout", "disabled", "unsafe_settings"])
def test_an_answer_is_never_turned_into_a_fallback(host, code):
    host.scenario(mode="wrapper", error=code)
    with pytest.raises(AgentTurnFailed) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="yes",
                                confirm=ConfirmAnswer("c1", SHA, "yes"))
    assert caught.value.code == code


@pytest.mark.parametrize("code", ["busy", "low_memory", "timeout", "agent_failed",
                                  "bad_request", "expired"])
def test_every_other_error_fails_the_turn_with_its_code(host, code):
    host.scenario(mode="wrapper", error=code, error_extra={"turn": "t-9"})
    with pytest.raises(AgentTurnFailed) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="hello")
    assert (caught.value.code, caught.value.turn_id) == (code, "t-9")


@pytest.mark.parametrize("notice,spoken,expected", [
    ("needs_desk", "", "That one needs the desk."),
    ("needs_desk", "SPOKEN SUMMARY: The pull request is open.",
     "That one needs the desk. The pull request is open."),
    ("run_alone", "", "That command has to be run on its own, so it was refused."),
    ("settings_changed", "", "The project settings changed. That needs the desk."),
    ("new_session", "SPOKEN SUMMARY: Two are open.",
     "I started a new session for this project. Two are open."),
    ("target_changed", "", "The push target changed. That one needs the desk."),
    ("out_of_budget", "", "The agent is out of budget for now."),
    # A summary that may not be spoken leaves the fixed sentence alone.
    ("needs_desk", "SPOKEN SUMMARY: see `gate.py`", "That one needs the desk."),
    (None, "", "Done. Check the log for details."),
])
def test_a_notice_is_spoken_as_the_bridges_own_fixed_sentence(host, notice, spoken, expected):
    host.scenario(mode="wrapper", notice=notice, spoken=spoken)
    assert _adapter(host).run_turn(project=DEMO, prompt="x").spoken == expected


def test_the_agent_hosts_own_expiry_is_passed_on(host):
    host.scenario(mode="wrapper", gate=True, expires_s=42)
    assert _adapter(host).run_turn(project=DEMO, prompt="merge it").expires_s == 42.0


def test_an_answer_too_late_on_the_agent_host_is_expired(host):
    host.scenario(mode="wrapper", gate=True)
    adapter = _adapter(host)
    turn = adapter.run_turn(project=DEMO, prompt="merge it")
    host.scenario(mode="wrapper", host_expired=True)
    with pytest.raises(AgentTurnFailed) as caught:
        adapter.run_turn(project=DEMO, prompt="yes",
                         confirm=ConfirmAnswer(turn.confirmation.id, SHA, "yes"))
    assert caught.value.code == "expired"
    assert [t["kind"] for t in host.turns()] == ["agent"]      # nothing ran


def test_a_host_key_that_is_not_the_pinned_one_fails_the_turn(host):
    host.scenario(mode="host_key")
    with pytest.raises(AgentTurnFailed) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="hello")
    assert caught.value.code == "host_key"
    assert "stdin" not in host.calls()[0]          # the request was never read


def test_a_connection_lost_after_the_request_is_not_unavailable(host):
    host.scenario(mode="drop")
    with pytest.raises(AgentTurnFailed) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="merge it")
    assert caught.value.code == "lost"


@pytest.mark.parametrize("scenario", [
    # The wrapper's own stderr imitating ssh, with the wrapper's exit status.
    {"stderr": "ssh: connect to host agent.example port 22: Connection refused\n", "exit": 1},
    # ssh's exit status, but something did come back.
    {"stderr": "ssh: connect to host agent.example port 22: Connection refused\n",
     "exit": 255, "stdout": "half a rep"},
    # Messages of a session that existed.
    {"stderr": "client_loop: send disconnect: Broken pipe\n", "exit": 255},
    {"stderr": "Timeout, server agent.example not responding.\n", "exit": 255},
    {"stderr": "", "exit": 255},
    {"stderr": "", "exit": 0},
])
def test_only_sshs_own_word_for_never_connected_allows_a_fallback(host, scenario):
    host.scenario(mode="raw", **scenario)
    with pytest.raises(AgentTurnFailed) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="merge it")
    assert caught.value.code in ("lost", "protocol")


def test_no_answer_within_the_timeout_fails_and_kills_ssh(host):
    host.scenario(mode="hang")
    with pytest.raises(AgentTurnFailed) as caught:
        _adapter(host, timeout_s=1).run_turn(project=DEMO, prompt="hello")
    assert caught.value.code == "no_answer"


@pytest.mark.parametrize("name", ["_private", "a/b", "..", "-rf", "x" * 65, "has space"])
def test_a_name_the_agent_host_would_refuse_is_never_sent(host, name):
    with pytest.raises(AgentUnavailable) as caught:
        _adapter(host).run_turn(project=ProjectConfig(name, Path("/work/x")), prompt="hello")
    assert caught.value.reason == "no_checkout"
    assert host.calls() == []


def test_a_request_over_64_kb_is_not_sent(host):
    with pytest.raises(AgentTurnFailed) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="x" * (64 * 1024))
    assert caught.value.code == "bad_request"
    assert host.calls() == []


def test_build_request_refuses_an_answer_that_is_not_yes_or_no():
    with pytest.raises(ValueError):
        build_request(project="demo", prompt="", confirm=ConfirmAnswer("c1", SHA, "maybe"))


# The reply, parsed strictly.

def _reply(**fields):
    reply = {"v": 1, "status": "ok", "error": None, "turn": "t-1",
             "spoken": "SPOKEN SUMMARY: Hi.", "pending": False, "readback": None,
             "confirm": None, "notice": None}
    reply.update(fields)
    return json.dumps(reply).encode()


def _without(key):
    reply = json.loads(_reply())
    del reply[key]
    return json.dumps(reply).encode()


CONFIRM = {"id": "c1", "sha256": SHA, "class": "merge", "expires_s": 60}


def _pending(**fields):
    reply = {"pending": True, "spoken": "", "readback": "Merge pull request 1 on jarvis.",
             "confirm": dict(CONFIRM)}
    reply.update(fields)
    return _reply(**reply)


def test_parse_reply_accepts_the_agreed_shapes():
    assert parse_reply(_reply())["spoken"] == "SPOKEN SUMMARY: Hi."
    assert parse_reply(_reply(turn=None, spoken="", notice="needs_desk"))["notice"] == "needs_desk"
    parsed = parse_reply(_pending(spoken="SPOKEN SUMMARY: say yes"))
    assert parsed["confirmation"] == PendingConfirmation("c1", SHA)
    assert parsed["spoken"] == ""          # the agent's line is dropped while one is pending
    assert parse_reply(_reply(status="error", error="busy", turn=None, spoken=""))["error"] == "busy"


@pytest.mark.parametrize("raw", [
    b"", b"not json", b"[]", b'"ok"', b'{"status":"ok"}',
    _reply(tools=["Bash"]), _reply(detail="x"),            # an unknown field
    *[_without(k) for k in ("v", "status", "error", "turn", "spoken", "pending",
                            "readback", "confirm", "notice")],   # a missing field
    _reply(v=2), _reply(v=True), _reply(v="1"),
    _reply().replace(b'"v": 1', b'"v": 1, "v": 1'),        # a duplicate key
    _reply(status="done"),
    _reply(error="busy"),                                  # ok with an error
    _reply(spoken=7), _reply(spoken=None),
    _reply(turn="t 1\nINFO fake log line"), _reply(turn=5),
    _reply(pending="yes"), _reply(pending=1), _reply(pending=None),
    _reply(readback="Merge."),                             # a read-back without pending
    _reply(confirm=dict(CONFIRM)),                         # a confirm without pending
    _reply(notice="all_good"), _reply(notice=["needs_desk"]),
    _pending(readback=None), _pending(readback=3),
    _pending(confirm=None),
    _pending(confirm={"id": "c1", "sha256": SHA}),
    _pending(confirm={**CONFIRM, "answer": "yes"}),
    _pending(confirm={**CONFIRM, "id": "c 1"}),
    _pending(confirm={**CONFIRM, "id": 1}),
    _pending(confirm={**CONFIRM, "sha256": SHA.upper()}),
    _pending(confirm={**CONFIRM, "sha256": "ab"}),
    _pending(confirm={**CONFIRM, "class": "Merge; rm"}),
    _pending(confirm={**CONFIRM, "class": None}),
    _pending(confirm={**CONFIRM, "expires_s": 0}),
    _pending(confirm={**CONFIRM, "expires_s": 3600}),
    _pending(confirm={**CONFIRM, "expires_s": True}),
    _pending(confirm={**CONFIRM, "expires_s": "60"}),
    _reply(status="error"),
    _reply(status="error", error="rm_rf"),
    _reply(status="error", error="busy", pending=True),
    _reply(status="error", error="busy", readback="Merge."),
    _reply().replace(b'"t-1"', b"NaN"),
    _reply(spoken="x" * (64 * 1024)),                      # larger than 64 KB
])
def test_parse_reply_refuses_everything_else(raw):
    with pytest.raises(ReplyError):
        parse_reply(raw)


def test_a_reply_that_is_not_the_agreed_shape_fails_the_turn(host):
    host.scenario(mode="raw", stdout=_reply(tools=["Bash"]).decode())
    with pytest.raises(AgentTurnFailed) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="hello")
    assert caught.value.code == "protocol"


def test_check_ssh_reports_a_missing_client(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(RuntimeError, match="ssh client"):
        remote_adapter.check_ssh()
