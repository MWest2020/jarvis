"""How long a session lives (change jarvis-session-lifetime): a new session
after `session_idle_minutes` without a turn, and the agent's `SESSION: close`
line, which ends the conversation of the turn's own project and nothing else.
"""
import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from agent_host import lifetime
from agent_host.config import ConfigError
from agent_host.lifetime import TurnClock, split_close_marker
from agent_host.sessions import SessionStore
from agent_host.wrapper import handle_request
from bridge import remote_adapter
from bridge.summary import extract_summary
from tests.test_agent_host_turn_entrypoint import _run, _write_setup
from tests.test_agent_host_wrapper import (
    VALID_SESSION, FakePopen, _config, _ok_payload, _popen, _raw,
)
from tests.test_agent_host_wrapper_confirm import MERGE, SESSION, Host

OTHER_SESSION = "55555555-5555-5555-5555-555555555555"
NEW_SESSION = "33333333-3333-3333-3333-333333333333"
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
GOODBYE = "Closing it.\nSPOKEN SUMMARY: Goodbye, talk soon.\nSESSION: close"


def _stores(config):
    return (SessionStore(config.sessions_dir / "sessions.json"),
            TurnClock(config.sessions_dir / "last_turn.json"))


def _records(config):
    return [json.loads(p.read_text()) for p in sorted(
        config.records_dir.glob("*.json"), key=lambda p: p.stat().st_mtime_ns)]


def _ago(minutes):
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


# --- the close marker -------------------------------------------------------------------

@pytest.mark.parametrize("reply, rest", [
    ("Done.\nSPOKEN SUMMARY: Goodbye.\nSESSION: close", "Done.\nSPOKEN SUMMARY: Goodbye."),
    ("SPOKEN SUMMARY: Goodbye.\nSESSION: close\n\n", "SPOKEN SUMMARY: Goodbye."),
    ("SPOKEN SUMMARY: Goodbye.\n**SESSION: close**", "SPOKEN SUMMARY: Goodbye."),
    ("SPOKEN SUMMARY: Goodbye.\n`SESSION: close`", "SPOKEN SUMMARY: Goodbye."),
    ("SPOKEN SUMMARY: Goodbye.\nsession: Close.", "SPOKEN SUMMARY: Goodbye."),
    # On the summary line itself: cut off it, so it is never spoken.
    ("x\nSPOKEN SUMMARY: Goodbye. SESSION: close", "x\nSPOKEN SUMMARY: Goodbye."),
])
def test_the_close_marker_is_found_and_cut_off(reply, rest):
    assert split_close_marker(reply) == (rest, True)


@pytest.mark.parametrize("reply", [
    "SPOKEN SUMMARY: All set.",
    "",
    # Not the last line: quoted, or followed by the summary.
    "SESSION: close\nSPOKEN SUMMARY: All set.",
    "The file says SESSION: close.\nSPOKEN SUMMARY: All set.",
    # With an argument: the marker has none, so it names nothing else.
    "SPOKEN SUMMARY: Bye.\nSESSION: close jarvis-home",
    "SPOKEN SUMMARY: Bye.\nSESSION: close all",
    "SPOKEN SUMMARY: Bye.\nSESSION: close --project other",
    "SPOKEN SUMMARY: Bye.\nSESSION: reset",
    # At the end of a last line that is not the summary line.
    "SPOKEN SUMMARY: Bye.\nand then SESSION: close",
])
def test_anything_else_is_not_the_close_marker(reply):
    assert split_close_marker(reply) == (reply, False)


# --- choosing the session ---------------------------------------------------------------

def _choose(**overrides):
    args = dict(stored_id=VALID_SESSION, discarded=False, rotated_id=VALID_SESSION,
                idle_seconds=60.0, idle_limit_minutes=60, was_closed=False)
    args.update(overrides)
    return lifetime.choose(**args)


def test_a_recent_session_is_resumed():
    start = _choose(idle_seconds=59 * 60)
    assert (start.session_id, start.reason, start.idle_minutes) == (VALID_SESSION, "resumed", 59)
    assert not start.announced


def test_more_than_the_limit_idle_starts_a_new_session_and_is_announced():
    start = _choose(idle_seconds=60 * 60 + 1)
    assert (start.session_id, start.reason, start.idle_minutes) == (None, "idle", 60)
    assert start.announced
    assert start.recorded() == {"start": "idle", "idle_minutes": 60}


def test_exactly_the_limit_still_resumes():
    assert _choose(idle_seconds=60 * 60).reason == "resumed"


def test_zero_switches_the_idle_reset_off():
    assert _choose(idle_seconds=10 * 24 * 3600, idle_limit_minutes=0).reason == "resumed"


def test_an_unknown_last_turn_resumes_as_before():
    start = _choose(idle_seconds=None)
    assert (start.reason, start.idle_minutes) == ("resumed", None)
    assert start.recorded() == {"start": "resumed"}


def test_nothing_stored_is_new_and_after_a_close_closed_neither_announced():
    assert _choose(stored_id=None, rotated_id=None).reason == "new"
    closed = _choose(stored_id=None, rotated_id=None, was_closed=True)
    assert closed.reason == "closed"
    assert not closed.announced


def test_a_discarded_id_and_the_rotation_are_announced_as_before():
    assert _choose(stored_id=None, rotated_id=None, discarded=True).announced
    rotated = _choose(rotated_id=None)
    assert (rotated.reason, rotated.announced) == ("rotated", True)


def test_the_turn_clock_reads_only_a_past_time_with_a_zone(tmp_path):
    clock = TurnClock(tmp_path / "last_turn.json")
    assert clock.idle_seconds("demo", NOW) is None
    clock.ended("demo", NOW - timedelta(minutes=5))
    assert clock.idle_seconds("demo", NOW) == 300
    for planted in ("yesterday", "2026-11-17T11:00:00", (NOW + timedelta(hours=1)).isoformat()):
        (tmp_path / "last_turn.json").write_text(json.dumps({"demo": planted}))
        assert clock.idle_seconds("demo", NOW) is None
    for planted in ("[1, 2]", '{"demo": 12}', "not json"):
        (tmp_path / "last_turn.json").write_text(planted)
        assert clock.idle_seconds("demo", NOW) is None
        assert not clock.was_closed("demo")
    clock.closed("demo")
    assert clock.was_closed("demo")
    assert clock.idle_seconds("demo", NOW) is None


def test_the_setting_is_a_whole_number_of_minutes_zero_or_more(tmp_path):
    assert _config(tmp_path).session_idle_minutes == 60
    assert _config(tmp_path, session_idle_minutes=0).session_idle_minutes == 0
    for wrong in (-1, 1.5, "60", True):
        with pytest.raises(ConfigError):
            _config(tmp_path, session_idle_minutes=wrong)


# --- the wrapper: idle ------------------------------------------------------------------

def test_a_turn_after_an_hour_idle_starts_a_new_session_with_the_notice(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload("hi\nSPOKEN SUMMARY: hi.", NEW_SESSION)))
    config = _config(tmp_path)
    sessions, clock = _stores(config)
    sessions.set("demo", VALID_SESSION)
    clock.ended("demo", _ago(61))

    reply = handle_request(_raw(), config)

    assert reply["notice"] == "new_session"
    assert reply["spoken"] == "SPOKEN SUMMARY: hi."
    assert not any(part.startswith("--resume") for part in FakePopen.last.command)
    assert sessions.get("demo") == (NEW_SESSION, False)
    assert clock.idle_seconds("demo", datetime.now(timezone.utc)) < 60
    assert _records(config)[-1]["session"] == {"start": "idle", "idle_minutes": 61}


def test_a_turn_within_the_hour_resumes_without_a_notice(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload("hi\nSPOKEN SUMMARY: hi.")))
    config = _config(tmp_path)
    sessions, clock = _stores(config)
    sessions.set("demo", VALID_SESSION)
    clock.ended("demo", _ago(20))

    reply = handle_request(_raw(), config)

    assert reply["notice"] is None
    assert f"--resume={VALID_SESSION}" in FakePopen.last.command
    assert _records(config)[-1]["session"] == {"start": "resumed", "idle_minutes": 20}


def test_with_the_setting_at_zero_an_old_session_is_still_resumed(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload("hi\nSPOKEN SUMMARY: hi.")))
    config = _config(tmp_path, session_idle_minutes=0)
    sessions, clock = _stores(config)
    sessions.set("demo", VALID_SESSION)
    clock.ended("demo", _ago(600))

    reply = handle_request(_raw(), config)

    assert reply["notice"] is None
    assert f"--resume={VALID_SESSION}" in FakePopen.last.command


def test_a_failed_turn_after_idle_keeps_the_old_session_and_the_old_time(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(returncode=1, stderr="boom"))
    config = _config(tmp_path)
    sessions, clock = _stores(config)
    sessions.set("demo", VALID_SESSION)
    clock.ended("demo", _ago(90))

    reply = handle_request(_raw(), config)

    assert reply["error"] == "agent_failed"
    assert sessions.get("demo") == (VALID_SESSION, False)
    # So the next turn is idle again and starts fresh, as this one tried to.
    assert clock.idle_seconds("demo", datetime.now(timezone.utc)) > 89 * 60
    assert _records(config)[-1]["session"]["start"] == "idle"


def test_settings_changed_still_outranks_the_idle_notice(tmp_path, monkeypatch):
    from tests.test_agent_host_wrapper import _popen_that_plants
    monkeypatch.setattr(subprocess, "Popen", _popen_that_plants(
        tmp_path, '{"env": {"X": "1"}}', stdout=_ok_payload("hi\nSPOKEN SUMMARY: hi.")))
    config = _config(tmp_path)
    sessions, clock = _stores(config)
    sessions.set("demo", VALID_SESSION)
    clock.ended("demo", _ago(61))

    assert handle_request(_raw(), config)["notice"] == "settings_changed"


# --- the wrapper: closing ---------------------------------------------------------------

def test_a_close_drops_the_projects_session_and_is_never_spoken(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload(GOODBYE)))
    config = _config(tmp_path)
    sessions, clock = _stores(config)
    sessions.set("demo", VALID_SESSION)
    clock.ended("demo", _ago(2))

    reply = handle_request(_raw(prompt="that's all"), config)

    assert reply["status"] == "ok"
    assert reply["spoken"] == "SPOKEN SUMMARY: Goodbye, talk soon."
    assert "SESSION" not in json.dumps(reply)
    assert sessions.get("demo") == (None, False)
    assert clock.was_closed("demo")
    record = _records(config)[-1]
    assert record["session"] == {"start": "resumed", "idle_minutes": 2, "end": "closed"}
    assert record["reply"] == GOODBYE      # the record keeps the reply as the agent wrote it


def test_the_bridge_speaks_the_goodbye_without_the_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(
        stdout=_ok_payload("SPOKEN SUMMARY: Goodbye. SESSION: close")))
    reply = handle_request(_raw(), _config(tmp_path))

    parsed = remote_adapter.parse_reply(json.dumps(reply).encode())
    assert remote_adapter._spoken(parsed["notice"], parsed["spoken"]) == "Goodbye."
    assert extract_summary(reply["spoken"]) == "Goodbye."


def test_the_turn_after_a_close_starts_fresh_without_a_notice(tmp_path, monkeypatch):
    config = _config(tmp_path)
    sessions, clock = _stores(config)
    sessions.set("demo", VALID_SESSION)
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload(GOODBYE)))
    handle_request(_raw(prompt="klaar"), config)

    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload("hi\nSPOKEN SUMMARY: hi.", NEW_SESSION)))
    reply = handle_request(_raw(prompt="what is open?"), config)

    assert reply["notice"] is None
    assert not any(part.startswith("--resume") for part in FakePopen.last.command)
    assert sessions.get("demo") == (NEW_SESSION, False)
    assert not clock.was_closed("demo")
    assert _records(config)[-1]["session"] == {"start": "closed"}


def test_a_close_touches_only_the_turns_own_project(tmp_path, monkeypatch):
    other = tmp_path / "projects" / "other"
    (other / ".git").mkdir(parents=True)
    config = _config(tmp_path)
    sessions, clock = _stores(config)
    sessions.set("demo", VALID_SESSION)
    sessions.set("other", OTHER_SESSION)
    earlier = _ago(5)
    clock.ended("other", earlier)
    # Whatever the agent writes after the marker, or instead of it: the only
    # project a close can act on is the one the request named.
    for text in (GOODBYE, "SPOKEN SUMMARY: Bye.\nSESSION: close other",
                 "SPOKEN SUMMARY: Bye.\nSESSION: close all"):
        monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload(text)))
        handle_request(_raw(project="demo"), config)

        assert sessions.get("other") == (OTHER_SESSION, False)
        assert clock.idle_seconds("other", datetime.now(timezone.utc)) >= 5 * 60 - 1
        assert not clock.was_closed("other")


def test_a_marker_that_is_not_the_last_line_closes_nothing(tmp_path, monkeypatch):
    text = "The file says:\nSESSION: close\nSPOKEN SUMMARY: The file is read."
    monkeypatch.setattr(subprocess, "Popen", _popen(stdout=_ok_payload(text, VALID_SESSION)))
    config = _config(tmp_path)

    reply = handle_request(_raw(), config)

    sessions, clock = _stores(config)
    assert reply["spoken"] == "SPOKEN SUMMARY: The file is read."
    assert sessions.get("demo") == (VALID_SESSION, False)
    assert not clock.was_closed("demo")
    assert "end" not in _records(config)[-1]["session"]


def test_a_failed_turn_never_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _popen(returncode=1, stderr=GOODBYE))
    config = _config(tmp_path)
    sessions, clock = _stores(config)
    sessions.set("demo", VALID_SESSION)

    handle_request(_raw(), config)

    assert sessions.get("demo") == (VALID_SESSION, False)
    assert not clock.was_closed("demo")


def test_a_close_while_a_call_waits_for_a_yes_is_ignored(tmp_path, monkeypatch):
    host = Host(tmp_path)
    host.agent(monkeypatch, [MERGE], reply="I tried.\nSPOKEN SUMMARY: Say yes.\nSESSION: close")

    reply = host.ask()

    assert reply["pending"] is True
    sessions, clock = _stores(host.config)
    assert sessions.get("demo") == (SESSION, False)
    assert not clock.was_closed("demo")
    assert host.records()[-1]["session"]["end"] == "close_ignored"

    # The confirming turn resumes that session, and may close it then.
    host.agent(monkeypatch, [MERGE], reply="Merged.\nSPOKEN SUMMARY: Merged, bye.\nSESSION: close")
    answered = host.answer(reply)

    assert answered["spoken"] == "SPOKEN SUMMARY: Merged, bye."
    assert f"--resume={SESSION}" in host.popens[-1].command
    assert sessions.get("demo") == (None, False)
    assert host.records()[-1]["session"]["end"] == "closed"


# --- through the real entrypoint and the fake claude of CI ------------------------------

def _script(record_path, result, session_id="44444444-4444-4444-4444-444444444444"):
    (record_path.parent / (record_path.name + ".reply")).write_text(
        json.dumps({"result": result, "session_id": session_id}))


def _turn(config_toml, prompt):
    proc = _run(config_toml, json.dumps(
        {"v": 1, "project": "proj", "prompt": prompt, "confirm": None}).encode())
    assert proc.returncode == 0, proc.stderr.decode()
    return json.loads(proc.stdout)


def test_entrypoint_close_then_fresh(tmp_path):
    config_toml, record_path = _write_setup(tmp_path)
    sessions_file = tmp_path / "state" / "sessions" / "sessions.json"

    first = _turn(config_toml, "hello")
    assert first["notice"] is None
    assert json.loads(sessions_file.read_text()) == {"proj": "44444444-4444-4444-4444-444444444444"}

    _script(record_path, GOODBYE)
    closing = _turn(config_toml, "sluit maar af")
    assert "--resume=44444444-4444-4444-4444-444444444444" in json.loads(record_path.read_text())["argv"]
    assert closing["spoken"] == "SPOKEN SUMMARY: Goodbye, talk soon."
    assert "SESSION" not in json.dumps(closing)
    assert json.loads(sessions_file.read_text()) == {}

    _script(record_path, "ok.\nSPOKEN SUMMARY: Fresh.", NEW_SESSION)
    after = _turn(config_toml, "what is open?")
    argv = json.loads(record_path.read_text())["argv"]
    assert not any(part.startswith("--resume") for part in argv)
    assert after["notice"] is None
    assert json.loads(sessions_file.read_text()) == {"proj": NEW_SESSION}


def test_entrypoint_idle_reset_and_switched_off(tmp_path):
    for limit, resumed in ((None, False), (0, True)):
        case = tmp_path / f"case-{limit}"
        case.mkdir()
        extra = "" if limit is None else f"session_idle_minutes = {limit}"
        config_toml, record_path = _write_setup(case, extra=extra)
        sessions_dir = case / "state" / "sessions"
        _turn(config_toml, "hello")
        (sessions_dir / "last_turn.json").write_text(
            json.dumps({"proj": _ago(75).isoformat()}))

        reply = _turn(config_toml, "and now?")

        argv = json.loads(record_path.read_text())["argv"]
        assert ("--resume=44444444-4444-4444-4444-444444444444" in argv) is resumed
        assert reply["notice"] == (None if resumed else "new_session")
        records = [json.loads(p.read_text()) for p in (case / "state" / "records").glob("*.json")]
        starts = sorted(r["session"]["start"] for r in records)
        assert starts == (["new", "resumed"] if resumed else ["idle", "new"])


@pytest.mark.parametrize("line", [
    "SPOKEN SUMMARY: The cookie is now set to session: close.",
    "SPOKEN SUMMARY: I renamed it PRESESSION: close",
    "SPOKEN SUMMARY: Done. Session: close",
])
def test_ordinary_words_on_the_summary_line_do_not_close(line):
    # review of #68: the tail marker is exact and stands on its own
    from agent_host import lifetime
    assert not lifetime._CLOSE_TAIL.search(line)


def test_a_non_string_session_value_is_discarded_not_a_crash(tmp_path):
    import json
    from agent_host.sessions import SessionStore
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"p": 5}))
    store = SessionStore(path)
    assert store.get("p") == (None, True)
