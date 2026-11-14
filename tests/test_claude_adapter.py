import json
import subprocess
from pathlib import Path

import pytest

from bridge import claude_adapter
from bridge.claude_adapter import ClaudeTurnError, build_command, run_turn

TOOLS = ("Read", "Glob", "Grep")


class FakePopen:
    """Stands in for subprocess.Popen; records what run_turn passed it."""

    last = None

    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs
        self.pid = 999999
        self.stdin_text = None
        FakePopen.last = self

    def communicate(self, input=None, timeout=None):  # noqa: A002 - stdlib signature
        if input is not None:
            self.stdin_text = input
        if self.behaviour == "timeout" and timeout is not None:
            raise subprocess.TimeoutExpired(self.command, timeout)
        return self.stdout, self.stderr


def _popen(returncode=0, stdout="", stderr="", behaviour="ok"):
    class P(FakePopen):
        pass

    P.returncode = returncode
    P.stdout = stdout
    P.stderr = stderr
    P.behaviour = behaviour
    return P


def _turn(session_id="old-session", prompt="what is 2+2"):
    return run_turn(prompt=prompt, working_dir=Path("/tmp/project"),
                    session_id=session_id, allowed_tools=TOOLS, timeout_s=30)


def test_builds_the_expected_command_and_parses_the_reply(monkeypatch):
    payload = {"result": "the reply", "session_id": "new-session"}
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=json.dumps(payload)))

    reply, session_id = _turn()

    assert (reply, session_id) == ("the reply", "new-session")
    p = FakePopen.last
    assert p.command[0] == "claude" and "-p" in p.command
    assert "--resume" in p.command and "old-session" in p.command
    assert p.kwargs["cwd"] == Path("/tmp/project")
    assert p.kwargs["start_new_session"] is True


def test_the_prompt_goes_over_stdin_never_argv(monkeypatch):
    payload = {"result": "r", "session_id": "s"}
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=json.dumps(payload)))

    _turn(prompt="- version please")

    p = FakePopen.last
    assert p.stdin_text == "- version please"
    assert "- version please" not in p.command


def test_the_token_does_not_reach_claude(monkeypatch):
    monkeypatch.setenv("JARVIS_TOKEN", "secret")
    payload = {"result": "r", "session_id": "s"}
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=json.dumps(payload)))

    _turn()

    assert "JARVIS_TOKEN" not in FakePopen.last.kwargs["env"]


def test_the_command_restricts_tools_not_just_preapproves_them():
    command = build_command(allowed_tools=TOOLS, session_id=None)
    assert command[command.index("--tools") + 1] == "Read,Glob,Grep"
    assert command[command.index("--allowedTools") + 1] == "Read,Glob,Grep"
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert "--strict-mcp-config" in command
    assert "--mcp-config" not in command
    denied = command[command.index("--disallowedTools") + 1].split(",")
    assert {"Bash", "Edit", "Write"} <= set(denied)
    assert "--dangerously-skip-permissions" not in command
    assert "bypassPermissions" not in command


def test_omits_resume_when_there_is_no_prior_session():
    assert "--resume" not in build_command(allowed_tools=TOOLS, session_id=None)


def test_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(returncode=1, stderr="boom"))
    with pytest.raises(ClaudeTurnError):
        _turn()


def test_raises_on_unparsable_json(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout="not json"))
    with pytest.raises(ClaudeTurnError):
        _turn()


def test_raises_on_missing_fields(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=json.dumps({"result": "x"})))
    with pytest.raises(ClaudeTurnError):
        _turn()


def test_a_timeout_kills_the_process_group(monkeypatch):
    killed = []
    monkeypatch.setattr(subprocess, "Popen", _popen(behaviour="timeout"))
    monkeypatch.setattr(claude_adapter.os, "killpg", lambda pid, sig: killed.append(pid))
    with pytest.raises(ClaudeTurnError):
        _turn()
    assert killed == [999999]
