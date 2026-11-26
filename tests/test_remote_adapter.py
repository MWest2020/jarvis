"""The `full` adapter against a fake `ssh` with a fake wrapper behind it
(jarvis-full-agent tasks 2.3, 2.4, 2.6). No connection is ever opened."""
import json
from pathlib import Path

import pytest

from bridge import remote_adapter
from bridge.agent import AgentTurnFailed, AgentUnavailable, ConfirmAnswer, PendingConfirmation
from bridge.config import AgentHostConfig, ProjectConfig
from bridge.summary import FALLBACK_SUMMARY
from bridge.remote_adapter import (
    READBACK_UNSPEAKABLE,
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


# D32: the argv, whole and in order, plus the two options that keep ssh's own log
# apart from the remote command's stderr. Changing it means changing the design.

def test_the_ssh_argv_is_the_one_of_d32():
    agent = AgentHostConfig(host="agent.example", port=2222, user="mark",
                            key_file=Path("/secrets/agent/key"),
                            known_hosts_file=Path("/secrets/agent/known_hosts"), timeout_s=170)
    assert build_ssh_command(agent, "/tmp/ssh.log") == [
        "ssh", "-T", "-F", "/dev/null",
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "IdentityAgent=none",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "UserKnownHostsFile=/secrets/agent/known_hosts",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=15",
        "-o", "LogLevel=VERBOSE",
        "-E", "/tmp/ssh.log",
        "-o", "IdentityFile=/secrets/agent/key",
        "-p", "2222",
        "mark@agent.example",
    ]


def test_the_real_ssh_reads_the_argv_as_one_key_and_one_known_hosts_file(tmp_path):
    # `ssh -G` prints the configuration ssh would use, without connecting. The key
    # file does not exist here: it must stay the only identity all the same, with
    # none of the default keys of ~/.ssh next to it (what `-i` would give).
    import shutil
    import subprocess
    ssh = shutil.which("ssh")
    if ssh is None:
        pytest.skip("no ssh client on this machine")
    agent = AgentHostConfig(host="agent.example", port=2222, user="mark",
                            key_file=tmp_path / "missing-key",
                            known_hosts_file=tmp_path / "known_hosts", timeout_s=170)
    argv = build_ssh_command(agent, str(tmp_path / "ssh.log"))
    done = subprocess.run([ssh, "-G", *argv[1:]], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    shown = [line.split(" ", 1) for line in done.stdout.splitlines()]
    values = lambda key: [v[1] for v in shown if v[0] == key and len(v) == 2]  # noqa: E731
    assert values("identityfile") == [str(tmp_path / "missing-key")]
    assert values("identitiesonly") == ["yes"]
    assert values("identityagent") == ["none"]
    assert values("userknownhostsfile") == [str(tmp_path / "known_hosts")]
    assert values("globalknownhostsfile") == ["/dev/null"]
    assert values("stricthostkeychecking") == ["true"] or values("stricthostkeychecking") == ["yes"]
    assert values("batchmode") == ["yes"]
    assert (values("user"), values("hostname"), values("port")) == (
        ["mark"], ["agent.example"], ["2222"])


@pytest.mark.parametrize("which", ["key_file", "known_hosts_file"])
@pytest.mark.parametrize("damage", ["missing", "group-readable", "a directory"])
def test_the_files_are_checked_again_before_every_turn(host, which, damage):
    adapter = _adapter(host)
    adapter.run_turn(project=DEMO, prompt="first")           # fine at first
    path = getattr(host, which)
    if damage == "missing":
        path.unlink()
    elif damage == "group-readable":
        path.chmod(0o440)
    else:
        path.unlink()
        path.mkdir()
    with pytest.raises(AgentUnavailable) as caught:
        adapter.run_turn(project=DEMO, prompt="second")
    assert caught.value.reason == "unreachable"
    assert len(host.calls()) == 1                            # ssh was not started again


def test_the_argv_names_no_remote_command_and_nothing_of_the_turn(host):
    _adapter(host).run_turn(project=DEMO, prompt="--dangerously-skip-permissions please")
    argv = host.calls()[0]["argv"][1:]
    log_file = argv[argv.index("-E") + 1]
    assert argv == build_ssh_command(host.config(), log_file)[1:]
    assert not Path(log_file).exists()                   # ssh's log is removed afterwards
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
        assert _adapter(host).run_turn(project=DEMO, prompt="x").spoken == FALLBACK_SUMMARY


def test_a_read_back_is_returned_as_given_with_its_confirmation(host):
    # A branch name with a slash would never pass the summary filter. A read-back
    # is not put through it: fixed words and shape-checked targets, as given.
    readback = "Push to main on jarvis from fix/yes-list, 2 commits, no review. Say yes to go ahead."
    host.scenario(mode="wrapper", gate=True, readback=readback,
                  spoken="SPOKEN SUMMARY: I merged it, say yes.")
    turn = _adapter(host).run_turn(project=DEMO, prompt="push it")
    assert turn.spoken == readback
    assert turn.confirmation == PendingConfirmation(id="c1", sha256=SHA)


UNSPEAKABLE_READBACKS = [
    "", "   ", "x" * 501,
    " Merge pull request 21.", "Merge pull request 21. ", "Merge pull request 21.\n",
    "\tMerge pull request 21.",
    "Merge.\nAnd delete the repository.", "Merge.\rAnd delete.", "Merge.\r\nAnd delete.",
    "Merge.\x0bAnd delete.", "Merge.\x0cAnd delete.", "Merge.\x1cAnd delete.",
    "Merge.\x1dAnd delete.", "Merge.\x1eAnd delete.", "Merge.\x85And delete.",
    "Merge.\u2028And delete.", "Merge.\u2029And delete.",
    "Merge \x07 now.", "Merge\x00.", "Merge\x7f.",
    "Merge\u00a0pull request 21.",          # a no-break space
    "Merge\u2003pull request 21.",          # an em space
    "Merge\u3000pull request 21.",          # an ideographic space
    "Merge\u202fpull request 21.",          # a narrow no-break space
    "Merge\u200bpull request 21.",          # a zero-width space (format)
    "Merge pull \u202erequest 21.",         # a right-to-left override
    "Merge\ufeff pull request 21.",         # a byte order mark
    "Merge \ue000 pull request 21.",        # private use
    "Merge \ud800 pull request 21.",        # half a surrogate pair
]


@pytest.mark.parametrize("readback", UNSPEAKABLE_READBACKS)
def test_speakable_readback_refuses_and_never_tidies(readback):
    assert remote_adapter.speakable_readback(readback) is None


@pytest.mark.parametrize("readback", [
    "Merge pull request 21 on jarvis, at commit 3f9c2ab. Checks green. Say yes to go ahead.",
    "Push to main on jarvis from release slash 1.2, 2 commits, no review.",
    "Send mail to mark@westerweel.work.",
    "Verwijder de map caf\u00e9.", "x" * 500,
])
def test_speakable_readback_returns_the_text_itself(readback):
    assert remote_adapter.speakable_readback(readback) is readback


@pytest.mark.parametrize("readback", ["", "   ", "Merge.\nAnd delete the repository.",
                                      "Merge \x07 now.", "x" * 501, " Merge.", "Merge. ",
                                      "Merge\u00a0now.", "Merge.\u2028Delete."])
def test_a_read_back_that_cannot_be_spoken_is_cancelled_and_said_so(host, readback):
    host.scenario(mode="wrapper", gate=True, readback=readback)
    turn = _adapter(host).run_turn(project=DEMO, prompt="merge it")
    assert (turn.spoken, turn.confirmation) == (READBACK_UNSPEAKABLE, None)
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


@pytest.mark.parametrize("code,turn", [("busy", None), ("low_memory", None),
                                       ("bad_request", None), ("expired", None),
                                       ("timeout", "t-9"), ("agent_failed", "t-9")])
def test_every_other_error_fails_the_turn_with_its_code(host, code, turn):
    host.scenario(mode="wrapper", error=code, error_extra={"turn": turn})
    with pytest.raises(AgentTurnFailed) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="hello")
    assert (caught.value.code, caught.value.turn_id) == (code, turn or "")


@pytest.mark.parametrize("notice,spoken,expected", [
    ("needs_desk", "", "I passed that on to the coding session."),
    ("needs_desk", "SPOKEN SUMMARY: The pull request is open.",
     "I passed that on to the coding session. The pull request is open."),
    ("run_alone", "", "That command has to be run on its own, so it was refused."),
    ("settings_changed", "", "The project settings changed. I passed that on to the coding session."),
    ("new_session", "SPOKEN SUMMARY: Two are open.",
     "I started a new session for this project. Two are open."),
    ("target_changed", "", "The push target changed. I passed that on to the coding session."),
    ("out_of_budget", "", "The agent is out of budget for now."),
    # A summary that may not be spoken leaves the fixed sentence alone.
    ("needs_desk", "SPOKEN SUMMARY: run `rm -rf /`", "I passed that on to the coding session."),
    (None, "", FALLBACK_SUMMARY),
])
def test_a_notice_is_spoken_as_the_bridges_own_fixed_sentence(host, notice, spoken, expected):
    host.scenario(mode="wrapper", notice=notice, spoken=spoken)
    assert _adapter(host).run_turn(project=DEMO, prompt="x").spoken == expected


def test_agent_failed_carries_the_out_of_budget_notice(host):
    host.scenario(mode="wrapper", error="agent_failed",
                  error_extra={"turn": "t-3", "notice": "out_of_budget"})
    with pytest.raises(AgentTurnFailed) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="hello")
    assert (caught.value.code, caught.value.notice) == ("agent_failed", "out_of_budget")


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


AUTH = 'Authenticated to agent.example ([100.64.0.9]:22) using "publickey".\n'
REFUSED = "ssh: connect to host github.com port 22: Connection refused\n"
DENIED = "git@github.com: Permission denied (publickey).\n"


@pytest.mark.parametrize("scenario", [
    # What the wrapper, the agent or a git child prints on the agent host arrives on
    # ssh's stderr, never in ssh's own log. An OOM kill then gives exit 255 and no
    # reply: the turn started, whatever those lines look like.
    {"stderr": REFUSED, "exit": 255},
    {"stderr": DENIED, "exit": 255},
    {"stderr": REFUSED + DENIED + "Host key verification failed.\n", "exit": 255},
    # ssh's own word for never connected, but the agent host had let the key in.
    {"log": AUTH + REFUSED, "exit": 255},
    {"log": AUTH + "Host key verification failed.\n", "exit": 255},
    # ssh's own word, but not ssh's exit status for it, or something did come back.
    {"log": REFUSED, "exit": 1},
    {"log": REFUSED, "exit": 0},
    {"log": REFUSED, "exit": 255, "stdout": "half a rep"},
    # Lines OpenSSH also writes for a session that broke after it began.
    {"log": "Connection reset by 100.64.0.9 port 22\n", "exit": 255},
    {"log": "Connection closed by 100.64.0.9 port 22\n", "exit": 255},
    {"log": AUTH + "client_loop: send disconnect: Broken pipe\n", "exit": 255},
    {"log": AUTH + "Timeout, server agent.example not responding.\n", "exit": 255},
    # No account of the connection at all.
    {"log": "", "exit": 255},
    {"log": "", "exit": 0},
])
def test_only_sshs_own_log_of_a_connection_never_made_allows_a_fallback(host, scenario):
    host.scenario(mode="raw", **scenario)
    with pytest.raises(AgentTurnFailed) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="merge it")
    assert caught.value.code in ("lost", "protocol")


@pytest.mark.parametrize("log,expected", [
    (REFUSED, "unreachable"),
    ("ssh: Could not resolve hostname agent.example: Name or service not known\n", "unreachable"),
    ("jarvis@agent.example: Permission denied (publickey).\n", "unreachable"),
    ("kex_exchange_identification: read: Connection reset by peer\n", "unreachable"),
    ("Connection timed out during banner exchange\n", "unreachable"),
    ("Host key for agent.example has changed.\nHost key verification failed.\n", "host_key"),
    (AUTH, "lost"),
    (AUTH + REFUSED, "lost"),
    ("Connection reset by 100.64.0.9 port 22\n", "lost"),
    ("Connection closed by 100.64.0.9 port 22\n", "lost"),
    ("prefix ssh: connect to host a port 22: Connection refused\n", "lost"),
    ("", "lost"),
])
def test_classify_no_reply(log, expected):
    assert remote_adapter.classify_no_reply(255, log) == expected
    assert remote_adapter.classify_no_reply(1, log) == "lost"


def test_the_remote_stderr_of_a_good_turn_changes_nothing(host):
    host.scenario(mode="wrapper", stderr=REFUSED + DENIED)
    assert _adapter(host).run_turn(project=DEMO, prompt="x").spoken == "Done on the agent host."


# D30: a fallback code is only believed without a turn id.

@pytest.mark.parametrize("code", ["no_checkout", "disabled", "unsafe_settings", "busy",
                                  "low_memory", "bad_request", "expired"])
def test_a_not_started_code_with_a_turn_id_is_a_turn_that_ran(host, code):
    host.scenario(mode="wrapper", error=code, error_extra={"turn": "t-9"})
    with pytest.raises(AgentTurnFailed) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="merge it")
    assert (caught.value.code, caught.value.turn_id) == ("protocol", "t-9")


def _alive(pid: int) -> bool:
    import os
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A process that ended and was not reaped yet still answers to signal 0.
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def _wait_until(condition, seconds=10.0):
    import time
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


def test_when_the_wait_is_over_the_bridge_stops_waiting_and_does_not_cut_the_turn(host):
    import time
    host.scenario(mode="slow", delay=2)
    started = time.monotonic()
    with pytest.raises(AgentTurnFailed) as caught:
        _adapter(host, timeout_s=0.5).run_turn(project=DEMO, prompt="the long thing")
    assert caught.value.code == "still_running"
    assert time.monotonic() - started < 1.5              # the phone gets its answer
    pid = host.pids()[0]
    assert _alive(pid)                                   # ssh was not killed
    # The connection stays, so the turn runs to its end by itself...
    assert _wait_until(lambda: (host.dir / "finished").exists())
    assert host.turns()[0]["prompt"] == "the long thing"
    # ...and then ssh is gone and its log file with it: nothing piles up.
    assert _wait_until(lambda: not _alive(pid))
    log_file = host.calls()[0]["argv"][host.calls()[0]["argv"].index("-E") + 1]
    assert _wait_until(lambda: not Path(log_file).exists())


def test_an_ssh_that_never_ends_is_stopped_in_the_end(host):
    host.scenario(mode="hang")
    adapter = RemoteAdapter(host.config(timeout_s=0.3), reap_after_s=0.5)
    with pytest.raises(AgentTurnFailed) as caught:
        adapter.run_turn(project=DEMO, prompt="hello")
    assert caught.value.code == "still_running"
    assert _wait_until(lambda: not _alive(host.pids()[0]))


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_more_than_64_kb_is_not_read_and_ssh_is_stopped(host, stream):
    import time
    host.scenario(mode="flood", stream=stream)
    started = time.monotonic()
    with pytest.raises(AgentTurnFailed) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="hello")
    assert caught.value.code == "protocol"
    assert time.monotonic() - started < 10
    assert not _alive(host.pids()[0])                    # killed, not left writing


def test_a_reply_of_exactly_the_cap_is_still_read(host):
    reply = json.loads(_reply())
    reply["spoken"] = "SPOKEN SUMMARY: " + "a " * 100
    body = json.dumps(reply)
    body += " " * (64 * 1024 - len(body))                # JSON may end in spaces
    host.scenario(mode="raw", stdout=body)
    assert _adapter(host).run_turn(project=DEMO, prompt="x").ref == "t-1"
    host.scenario(mode="raw", stdout=body + " ")
    with pytest.raises(AgentTurnFailed) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="x")
    assert caught.value.code == "protocol"


def test_the_kill_switch_answers_without_reading_the_request(host):
    # The real wrapper does not read stdin when it is disabled; the bridge must
    # not trip over the closed pipe, however large the request.
    host.scenario(mode="wrapper", error="disabled")
    with pytest.raises(AgentUnavailable) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="x" * 60000)
    assert caught.value.reason == "disabled"
    assert "stdin" not in host.calls()[0]


# ssh stopped before it had a log at all.

def test_no_passwd_entry_for_the_bridges_uid_is_never_connected(host):
    host.scenario(mode="no_user")
    with pytest.raises(AgentUnavailable) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="hello")
    assert caught.value.reason == "unreachable"


@pytest.mark.parametrize("rc,stderr,expected", [
    (255, "No user exists for uid 10001\n", "unreachable"),
    (1, "Couldn't open logfile /tmp/jarvis-ssh-x.log: No space left on device\n", "unreachable"),
    # Not ssh's exit status for that line, or not the only line, or not the line.
    (1, "No user exists for uid 10001\n", "lost"),
    (255, "Couldn't open logfile /tmp/x.log: Permission denied\n", "lost"),
    (255, "No user exists for uid 10001\nConnection to a closed by remote host.\n", "lost"),
    (255, "remote: No user exists for uid 10001\n", "lost"),
    (255, "ssh: connect to host a port 22: Connection refused\n", "lost"),
    (255, "", "lost"),
])
def test_an_empty_log_is_only_never_connected_on_sshs_own_two_lines(rc, stderr, expected):
    assert remote_adapter.classify_no_reply(rc, "", stderr) == expected
    # With anything in ssh's log the real stderr decides nothing.
    assert remote_adapter.classify_no_reply(rc, AUTH, stderr) == "lost"


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
    for classes in ("merge", "merge, root", ["merge"], ["merge", "root"]):
        assert parse_reply(_pending(confirm={**CONFIRM, "class": classes}))["confirmation"]


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
    _reply(spoken="SPOKEN SUMMARY: half \ud800 a pair"),   # a lone surrogate
    _reply(spoken="\udfff"),
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
    _pending(confirm={**CONFIRM, "class": []}),
    _pending(confirm={**CONFIRM, "class": ["merge", 7]}),
    _pending(confirm={**CONFIRM, "class": ["rm -rf /"]}),
    _pending(confirm={**CONFIRM, "class": {"merge": True}}),
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


@pytest.mark.parametrize("spoken", [
    "SPOKEN SUMMARY: Done.", "SPOKEN SUMMARY: caf\u00e9 \U0001f600 \u2028 \x00", "", "\x7f\x80",
    "no marker", "SPOKEN SUMMARY: " + "\u00e9" * 400,
])
def test_whatever_a_parsed_reply_makes_the_bridge_speak_fits_in_a_header(host, spoken):
    # The summary goes into X-Jarvis-Summary through urllib.parse.quote, which
    # raises on text that cannot be encoded.
    import urllib.parse
    host.scenario(mode="raw", stdout=_reply(spoken=spoken).decode())
    turn = _adapter(host).run_turn(project=DEMO, prompt="x")
    assert urllib.parse.quote(turn.spoken)
    turn.spoken.encode("utf-8")
    for notice in remote_adapter.NOTICE_SENTENCES:
        host.scenario(mode="raw", stdout=_reply(spoken=spoken, notice=notice).decode())
        urllib.parse.quote(_adapter(host).run_turn(project=DEMO, prompt="x").spoken)


def test_a_read_back_that_is_spoken_fits_in_a_header():
    import urllib.parse
    for candidate in UNSPEAKABLE_READBACKS + ["Merge pull request 21 on jarvis."]:
        text = remote_adapter.speakable_readback(candidate)
        if text is not None:
            urllib.parse.quote(text)


def test_a_reply_that_is_not_the_agreed_shape_fails_the_turn(host):
    host.scenario(mode="raw", stdout=_reply(tools=["Bash"]).decode())
    with pytest.raises(AgentTurnFailed) as caught:
        _adapter(host).run_turn(project=DEMO, prompt="hello")
    assert caught.value.code == "protocol"


def test_check_ssh_reports_a_missing_client(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(RuntimeError, match="ssh client"):
        remote_adapter.check_ssh()



def test_only_the_marker_line_of_the_spoken_field_is_spoken(host):
    # Review of #104: lines around the marker in the field are never spoken.
    host.scenario(mode="wrapper", spoken="Private notes here.\nSPOKEN SUMMARY: Two are open.")
    assert _adapter(host).run_turn(project=DEMO, prompt="x").spoken == "Two are open."
    host.scenario(mode="wrapper", spoken="Private notes here.\nSPOKEN SUMMARY: run `rm -rf /`")
    assert _adapter(host).run_turn(project=DEMO, prompt="x").spoken == FALLBACK_SUMMARY
