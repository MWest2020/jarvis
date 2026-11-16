import json
import subprocess
from datetime import datetime

import pytest

from agent_host import wrapper
from agent_host.config import config_from_dict
from agent_host.sessions import SessionStore
from agent_host.turnlock import acquire_turn_lock
from agent_host.wrapper import V1_FIELDS, handle_request
from bridge.summary import extract_summary

VALID_SESSION = "22222222-2222-2222-2222-222222222222"


def _config(tmp_path, **overrides):
    root = tmp_path / "projects"
    demo = root / "demo"
    (demo / ".git").mkdir(parents=True, exist_ok=True)
    env_file = tmp_path / "environment"
    env_file.write_text("PATH=/usr/bin:/bin\n")
    turn_settings = tmp_path / "turn-settings.json"
    turn_settings.write_text("{}")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("be jarvis")
    user_settings = tmp_path / "user-settings.json"
    user_settings.write_text("{}")
    data = {
        "projects_root": str(root),
        "state_dir": str(tmp_path / "state"),
        "environment_file": str(env_file),
        "turn_settings": str(turn_settings),
        "prompt_file": str(prompt_file),
        "session_identity": "jarvis",
        "user_settings_path": str(user_settings),
    }
    data.update(overrides)
    return config_from_dict(data)


def _raw(**overrides):
    data = {"v": 1, "project": "demo", "prompt": "what is 2+2", "confirm": None}
    data.update(overrides)
    return json.dumps(data).encode()


class FakePopen:
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


def _ok_payload(reply_text, session_id=VALID_SESSION):
    return json.dumps({"result": reply_text, "session_id": session_id})


# --- every reply has exactly the version 1 fields (1.6b) ---------------

def test_every_reply_has_exactly_the_version_1_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload("hi\nSPOKEN SUMMARY: hi.")))
    config = _config(tmp_path)
    reply = handle_request(_raw(), config)
    assert set(reply) == set(V1_FIELDS)
    assert reply["status"] == "ok"


def test_an_error_reply_also_has_exactly_the_version_1_fields(tmp_path):
    config = _config(tmp_path, enabled=False)
    reply = handle_request(_raw(), config)
    assert set(reply) == set(V1_FIELDS)
    assert reply["status"] == "error"
    assert reply["error"] == "disabled"


# --- the kill switch (1.6b) ---------------------------------------------

def test_the_kill_switch_answers_disabled_and_does_nothing_else(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: called.append(1))
    config = _config(tmp_path, enabled=False)

    reply = handle_request(_raw(), config)

    assert reply["error"] == "disabled"
    assert reply["turn"] is None
    assert not called
    assert not config.records_dir.exists()


# --- malformed requests: all refused (1.6b) -----------------------------

def test_an_extra_field_is_bad_request(tmp_path):
    config = _config(tmp_path)
    raw = json.dumps(
        {"v": 1, "project": "demo", "prompt": "hi", "confirm": None, "extra": 1}
    ).encode()
    assert handle_request(raw, config)["error"] == "bad_request"


def test_a_confirm_with_a_non_empty_prompt_is_bad_request(tmp_path):
    config = _config(tmp_path)
    raw = _raw(prompt="not empty", confirm={"id": "a", "sha256": "b", "answer": "yes"})
    assert handle_request(raw, config)["error"] == "bad_request"


def test_an_oversized_body_is_bad_request(tmp_path):
    config = _config(tmp_path)
    raw = _raw(prompt="x" * 100_000)
    assert handle_request(raw, config)["error"] == "bad_request"


def test_a_confirm_with_nothing_pending_is_expired(tmp_path):
    # Phase 1a has no gate: nothing is ever pending, so a confirm always
    # answers that its call is gone.
    config = _config(tmp_path)
    raw = _raw(prompt="", confirm={"id": "a", "sha256": "b", "answer": "yes"})
    reply = handle_request(raw, config)
    assert reply["error"] == "expired"
    assert reply["turn"] is None


# --- project resolution failures: all refused as no_checkout (1.6b) -----

def test_a_path_as_project_is_no_checkout(tmp_path):
    config = _config(tmp_path)
    reply = handle_request(_raw(project="../projects/demo"), config)
    assert reply["error"] == "no_checkout"


def test_a_symlinked_project_is_no_checkout(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    (root / "link").symlink_to(outside)
    config = _config(tmp_path, projects_root=str(root))

    reply = handle_request(_raw(project="link"), config)
    assert reply["error"] == "no_checkout"


def test_a_bare_repository_is_no_checkout(tmp_path):
    root = tmp_path / "projects"
    (root / "bare").mkdir(parents=True)
    config = _config(tmp_path, projects_root=str(root))

    reply = handle_request(_raw(project="bare"), config)
    assert reply["error"] == "no_checkout"


# --- unsafe user settings (1.4) -----------------------------------------

def test_a_bypass_default_refuses_the_turn(tmp_path):
    config = _config(tmp_path)
    config.user_settings_path.write_text('{"defaultMode": "bypassPermissions"}')
    reply = handle_request(_raw(), config)
    assert reply["error"] == "unsafe_settings"


# --- the lock and the memory floor (1.6b) --------------------------------

def test_busy_when_another_turn_holds_the_lock(tmp_path, monkeypatch):
    config = _config(tmp_path)
    with acquire_turn_lock(config.lock_path):
        reply = handle_request(_raw(), config)
    assert reply["error"] == "busy"
    assert reply["turn"] is None


def test_low_memory_when_below_the_floor(tmp_path, monkeypatch):
    config = _config(tmp_path, memory_floor_mb=99999)
    monkeypatch.setattr(wrapper, "available_memory_mb", lambda: 1.0)
    reply = handle_request(_raw(), config)
    assert reply["error"] == "low_memory"
    assert reply["turn"] is None


# --- the timeout kills the process group (1.6b) --------------------------

def test_a_timeout_kills_the_process_group_and_the_turn_is_recorded(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(subprocess, "Popen", _popen(behaviour="timeout"))
    monkeypatch.setattr(wrapper.os, "killpg", lambda pid, sig: killed.append(pid))
    config = _config(tmp_path)

    reply = handle_request(_raw(), config)

    assert reply["error"] == "timeout"
    assert reply["turn"] is not None
    assert killed == [999999]
    records = list(config.records_dir.glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["outcome"] == "timeout"


def test_agent_failed_on_nonzero_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(returncode=1, stderr="boom"))
    config = _config(tmp_path)
    reply = handle_request(_raw(), config)
    assert reply["error"] == "agent_failed"


def test_agent_failed_on_unparsable_json(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout="not json"))
    config = _config(tmp_path)
    reply = handle_request(_raw(), config)
    assert reply["error"] == "agent_failed"


# --- a successful turn ----------------------------------------------------

def test_a_successful_turn_builds_the_right_command_and_env(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload("done.\nSPOKEN SUMMARY: all done.")))
    config = _config(tmp_path)

    reply = handle_request(_raw(), config)

    assert reply["status"] == "ok"
    assert reply["error"] is None
    assert reply["turn"] is not None
    assert reply["pending"] is False
    assert reply["readback"] is None
    assert reply["confirm"] is None

    popen = FakePopen.last
    assert popen.command[0] == "claude"
    assert popen.kwargs["cwd"] == (tmp_path / "projects" / "demo").resolve()
    assert popen.kwargs["start_new_session"] is True
    assert popen.kwargs["env"]["IDENT"] == "jarvis"
    assert popen.kwargs["env"]["JARVIS_TURN"] == "1"
    assert "JARVIS_TURN_ID" in popen.kwargs["env"]
    assert popen.stdin_text == "what is 2+2"


def test_spoken_is_the_marker_line_verbatim_and_matches_extract_summary(tmp_path, monkeypatch):
    reply_text = "I did the thing, in detail.\nSPOKEN SUMMARY: The thing is done."
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload(reply_text)))
    config = _config(tmp_path)

    reply = handle_request(_raw(), config)

    assert reply["spoken"] == "SPOKEN SUMMARY: The thing is done."
    assert extract_summary(reply["spoken"]) == extract_summary(reply_text)


def test_a_discarded_stored_session_id_never_reaches_argv_and_notes_new_session(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload("hi\nSPOKEN SUMMARY: hi.")))
    config = _config(tmp_path)
    sessions = SessionStore(config.sessions_dir / "sessions.json")
    for planted in ("--dangerously-skip-permissions", "--version"):
        sessions.set("demo", planted)

        reply = handle_request(_raw(), config)

        assert reply["notice"] == "new_session"
        assert "--resume" not in " ".join(FakePopen.last.command)
        assert planted not in FakePopen.last.command


def test_a_valid_stored_session_id_is_resumed_with_no_notice(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload("hi\nSPOKEN SUMMARY: hi.")))
    config = _config(tmp_path)
    sessions = SessionStore(config.sessions_dir / "sessions.json")
    sessions.set("demo", VALID_SESSION)

    reply = handle_request(_raw(), config)

    assert reply["notice"] is None
    assert f"--resume={VALID_SESSION}" in FakePopen.last.command


def test_a_second_prompt_file_is_concatenated_and_sent_inline(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload("hi\nSPOKEN SUMMARY: hi.")))
    second = tmp_path / "second-prompt.md"
    second.write_text("Mark's own additions.")
    config = _config(tmp_path, second_prompt_file=str(second))

    handle_request(_raw(), config)

    command = FakePopen.last.command
    assert "--append-system-prompt-file" not in command
    idx = command.index("--append-system-prompt")
    assert command[idx + 1] == "be jarvis\n\nMark's own additions."


# --- the session rotation fallback (D34's other fallback) ---------------

def test_rotation_day_turns_over_at_04_00_local_not_midnight():
    assert wrapper._rotation_day(datetime(2026, 11, 15, 3, 59)) == "2026-11-14"
    assert wrapper._rotation_day(datetime(2026, 11, 15, 4, 0)) == "2026-11-15"


def test_resolve_session_id_is_a_no_op_when_the_fallback_is_off(tmp_path):
    config = _config(tmp_path, session_rotation_fallback=False)
    result = wrapper._resolve_session_id(
        config, "demo", VALID_SESSION, now=datetime(2026, 11, 16, 5, 0),
    )
    assert result == VALID_SESSION


def test_resolve_session_id_starts_fresh_on_the_first_turn_of_a_new_day(tmp_path):
    config = _config(tmp_path, session_rotation_fallback=True)
    first = wrapper._resolve_session_id(
        config, "demo", VALID_SESSION, now=datetime(2026, 11, 15, 10, 0),
    )
    assert first is None  # first turn ever recorded: a new rotation day starts

    second = wrapper._resolve_session_id(
        config, "demo", VALID_SESSION, now=datetime(2026, 11, 15, 20, 0),
    )
    assert second == VALID_SESSION  # same rotation day: keep resuming

    third = wrapper._resolve_session_id(
        config, "demo", VALID_SESSION, now=datetime(2026, 11, 16, 5, 30),
    )
    assert third is None  # past the next 04:00: a fresh session again


def test_the_new_session_id_is_stored_after_a_successful_turn(tmp_path, monkeypatch):
    new_id = "33333333-3333-3333-3333-333333333333"
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload("hi\nSPOKEN SUMMARY: hi.", new_id)))
    config = _config(tmp_path)

    handle_request(_raw(), config)

    sessions = SessionStore(config.sessions_dir / "sessions.json")
    session_id, discarded = sessions.get("demo")
    assert session_id == new_id
    assert discarded is False


# --- the checkout settings rule: before and after every turn (1.7d, D36) ----

def _checkout_settings(tmp_path, name, content):
    target = tmp_path / "projects" / "demo" / ".claude" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


@pytest.mark.parametrize("name", ["settings.json", "settings.local.json"])
@pytest.mark.parametrize("content", [
    '{"env": {"JARVIS_GRANT": "abc"}}',
    '{"hooks": {"PreToolUse": []}}',
    '{"disableAllHooks": true}',
    '{"disableAllHooks": false}',
    "{ not json",
])
def test_a_checkout_whose_settings_carry_one_of_the_three_keys_is_refused(
    tmp_path, monkeypatch, name, content,
):
    called = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: called.append(1))
    config = _config(tmp_path)
    _checkout_settings(tmp_path, name, content)

    reply = handle_request(_raw(), config)

    assert reply["status"] == "error"
    assert reply["error"] == "unsafe_settings"
    assert reply["turn"] is None
    assert set(reply) == set(V1_FIELDS)
    assert not called
    assert not config.records_dir.exists()


def test_checkout_settings_without_the_three_keys_do_not_stop_a_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload("hi\nSPOKEN SUMMARY: hi.")))
    config = _config(tmp_path)
    _checkout_settings(tmp_path, "settings.json", '{"permissions": {"allow": ["Bash(ls:*)"]}}')
    _checkout_settings(tmp_path, "settings.local.json", "{}")

    reply = handle_request(_raw(), config)

    assert reply["status"] == "ok"
    assert reply["notice"] is None


def test_the_gate_gets_the_configuration_path_and_the_turn_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload("hi\nSPOKEN SUMMARY: hi.")))
    config = _config(tmp_path)

    handle_request(_raw(), config)

    env = FakePopen.last.kwargs["env"]
    assert env["JARVIS_AGENT_CONFIG"] == str(config.config_path)
    assert env["JARVIS_TURN_CHECKOUT"] == str((tmp_path / "projects" / "demo").resolve())


def _popen_that_plants(tmp_path, content, **kwargs):
    """A turn during which a settings file arrives in the checkout, as a
    `git pull` or `gh pr checkout` would bring it."""
    base = _popen(**kwargs)

    class Planting(base):
        def communicate(self, input=None, timeout=None):  # noqa: A002
            if input is not None:
                _checkout_settings(tmp_path, "settings.json", content)
            return super().communicate(input, timeout)

    return Planting


def test_settings_that_appear_during_the_turn_are_noticed_after_it(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen_that_plants(
        tmp_path, '{"env": {"X": "1"}}', stdout=_ok_payload("done.\nSPOKEN SUMMARY: done.")))
    config = _config(tmp_path)

    reply = handle_request(_raw(), config)

    assert reply["status"] == "ok"
    assert reply["notice"] == "settings_changed"
    assert reply["pending"] is False and reply["readback"] is None and reply["confirm"] is None
    (record_path,) = config.records_dir.glob("*.json")
    record = json.loads(record_path.read_text())
    assert record["settings_changed"] == "settings.json sets env"

    # And the next turn in that checkout is refused before it starts.
    reply = handle_request(_raw(), config)
    assert reply["error"] == "unsafe_settings"
    assert reply["turn"] is None


def test_settings_changed_outranks_new_session_and_rides_on_a_failed_turn(tmp_path, monkeypatch):
    config = _config(tmp_path)
    SessionStore(config.sessions_dir / "sessions.json").set("demo", "--version")
    monkeypatch.setattr(subprocess, "Popen", _popen_that_plants(
        tmp_path, '{"hooks": {}}', stdout=_ok_payload("done.\nSPOKEN SUMMARY: done.")))
    assert handle_request(_raw(), config)["notice"] == "settings_changed"

    (tmp_path / "projects" / "demo" / ".claude" / "settings.json").unlink()
    monkeypatch.setattr(subprocess, "Popen", _popen_that_plants(
        tmp_path, '{"disableAllHooks": true}', returncode=1, stderr="boom"))
    reply = handle_request(_raw(), config)
    assert reply["error"] == "agent_failed"
    assert reply["notice"] == "settings_changed"


def test_a_turn_that_leaves_the_settings_alone_records_no_change(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload("hi\nSPOKEN SUMMARY: hi.")))
    config = _config(tmp_path)
    handle_request(_raw(), config)
    (record_path,) = config.records_dir.glob("*.json")
    assert json.loads(record_path.read_text())["settings_changed"] is None


def test_a_confirm_still_answers_expired_whatever_the_settings_say(tmp_path):
    # 1b1: nothing can be pending, so a confirm starts no turn in any case.
    config = _config(tmp_path)
    _checkout_settings(tmp_path, "settings.json", '{"env": {}}')
    raw = _raw(prompt="", confirm={"id": "a", "sha256": "b", "answer": "yes"})
    reply = handle_request(raw, config)
    assert reply["error"] == "expired"
    assert reply["turn"] is None
