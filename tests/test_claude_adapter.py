import json
import subprocess
from pathlib import Path

import pytest

from bridge.claude_adapter import ClaudeTurnError, run_turn


def _fake_run(returncode=0, stdout="", stderr=""):
    def _run(command, cwd, capture_output, text, timeout, check):
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)

    return _run


def test_builds_the_expected_command_and_parses_the_reply(monkeypatch):
    captured = {}

    def fake_run(command, cwd, capture_output, text, timeout, check):
        captured["command"] = command
        captured["cwd"] = cwd
        payload = {"result": "the reply", "session_id": "new-session"}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    reply, session_id = run_turn(
        prompt="what is 2+2",
        working_dir=Path("/tmp/project"),
        session_id="old-session",
        allowed_tools=("Read", "Glob", "Grep"),
        timeout_s=30,
    )

    assert reply == "the reply"
    assert session_id == "new-session"
    command = captured["command"]
    assert command[0] == "claude"
    assert "-p" in command
    assert "--output-format" in command and "json" in command
    assert "--allowedTools" in command
    assert command[command.index("--allowedTools") + 1] == "Read,Glob,Grep"
    assert "--resume" in command and "old-session" in command
    assert captured["cwd"] == Path("/tmp/project")


def test_omits_resume_when_there_is_no_prior_session(monkeypatch):
    captured = {}

    def fake_run(command, cwd, capture_output, text, timeout, check):
        captured["command"] = command
        payload = {"result": "reply", "session_id": "s1"}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    run_turn(
        prompt="hello",
        working_dir=Path("/tmp/project"),
        session_id=None,
        allowed_tools=("Read",),
        timeout_s=30,
    )

    assert "--resume" not in captured["command"]


def test_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(returncode=1, stderr="boom"))
    with pytest.raises(ClaudeTurnError):
        run_turn(
            prompt="hello",
            working_dir=Path("/tmp"),
            session_id=None,
            allowed_tools=("Read",),
            timeout_s=30,
        )


def test_raises_on_unparsable_json(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(returncode=0, stdout="not json"))
    with pytest.raises(ClaudeTurnError):
        run_turn(
            prompt="hello",
            working_dir=Path("/tmp"),
            session_id=None,
            allowed_tools=("Read",),
            timeout_s=30,
        )


def test_raises_on_missing_fields(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", _fake_run(returncode=0, stdout=json.dumps({"result": "x"}))
    )
    with pytest.raises(ClaudeTurnError):
        run_turn(
            prompt="hello",
            working_dir=Path("/tmp"),
            session_id=None,
            allowed_tools=("Read",),
            timeout_s=30,
        )


def test_timeout_is_reported_as_a_claude_turn_error(monkeypatch):
    def fake_run(command, cwd, capture_output, text, timeout, check):
        raise subprocess.TimeoutExpired(cmd=command, timeout=timeout)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ClaudeTurnError):
        run_turn(
            prompt="hello",
            working_dir=Path("/tmp"),
            session_id=None,
            allowed_tools=("Read",),
            timeout_s=1,
        )
