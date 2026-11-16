"""Orchestrates one turn (1.1-1.6, 1.7d, 1.12a, 1.13, D33/D34/D36/D37/D38).

Phase 1b1: the gate (agent_host/gate) decides and refuses. It denies every
gated call and makes nothing pending, so `pending`, `readback` and `confirm`
are still always the empty values of D33's table, and a `confirm` from the
caller still always answers `expired` (nothing can be pending until 1b2
adds the hash, the grant and the read-back).

What this build adds here is the wrapper's half of the checkout settings
rule (D36): a turn is refused with `unsafe_settings` when the checkout's own
`.claude/settings.json` or `settings.local.json` carries `env`, `hooks` or
`disableAllHooks`, and the two files are read again after the turn. If a key
appeared during the turn the reply carries the notice `settings_changed`
and the record says so; dropping a pending call at that point is 1b2's,
when there is one to drop.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_host import checkout_settings, command, identity, projects
from agent_host import record as record_mod
from agent_host import request as request_mod
from agent_host.config import DEFAULT_CONFIG_PATH, AgentHostConfig, load_config
from agent_host.sessions import KeyValueStore, SessionStore
from agent_host.turnlock import Busy, acquire_turn_lock, available_memory_mb
from bridge.summary import last_marker_line

V1_FIELDS: tuple[str, ...] = (
    "v", "status", "error", "turn", "spoken", "pending", "readback", "confirm", "notice",
)

# The closed error list of D33.
ERROR_CODES = frozenset({
    "bad_request", "no_checkout", "busy", "low_memory", "timeout",
    "agent_failed", "disabled", "unsafe_settings", "expired",
})

# The closed notice list of D33. This build sets `new_session` and
# `settings_changed`; the others arrive with the pending call (1b2).
NOTICE_CODES = frozenset({
    "needs_desk", "run_alone", "settings_changed", "target_changed",
    "out_of_budget", "new_session",
})


def load_runtime_config() -> AgentHostConfig:
    """The wrapper's own configuration file. JARVIS_AGENT_CONFIG overrides the
    installed default path - a test seam only; the child's environment (what
    `claude` sees) is never built from this process's environment, whatever
    it is (agent_host.identity).
    """
    path = Path(os.environ.get("JARVIS_AGENT_CONFIG", str(DEFAULT_CONFIG_PATH)))
    return load_config(path)


def _reply(
    *, status: str, error: str | None = None, turn: str | None = None, spoken: str = "",
    pending: bool = False, readback: str | None = None, confirm: dict | None = None,
    notice: str | None = None,
) -> dict:
    assert status in ("ok", "error")
    assert error is None or error in ERROR_CODES
    assert notice is None or notice in NOTICE_CODES
    return {
        "v": 1,
        "status": status,
        "error": error,
        "turn": turn,
        "spoken": spoken,
        "pending": pending,
        "readback": readback,
        "confirm": confirm,
        "notice": notice,
    }


def disabled_reply() -> dict:
    return _reply(status="error", error="disabled")


def handle_request(raw: bytes, config: AgentHostConfig) -> dict:
    if not config.enabled:
        return disabled_reply()

    try:
        req = request_mod.parse_request(raw)
    except request_mod.RequestError:
        return _reply(status="error", error="bad_request")

    if req.confirm is not None:
        # No pending call can exist without a gate (phase 1b): any confirm
        # answers that its call is gone, exactly as D33 defines `expired`.
        return _reply(status="error", error="expired")

    try:
        working_dir = projects.resolve_project(config, req.project)
    except projects.ProjectError:
        return _reply(status="error", error="no_checkout")

    if command.settings_default_to_bypass(config.user_settings_path):
        return _reply(status="error", error="unsafe_settings")
    # The checkout settings rule, before the turn (D36, check 1).
    if _checkout_settings_problem(working_dir, config) is not None:
        return _reply(status="error", error="unsafe_settings")

    try:
        with acquire_turn_lock(config.lock_path):
            if available_memory_mb() < config.memory_floor_mb:
                return _reply(status="error", error="low_memory")
            return _run_turn(req, working_dir, config)
    except Busy:
        return _reply(status="error", error="busy")


def _checkout_settings_problem(working_dir: Path, config: AgentHostConfig) -> str | None:
    return checkout_settings.problem(working_dir, user_settings_path=config.user_settings_path)


def _uses_inline_prompt(config: AgentHostConfig) -> bool:
    # The fallback switch of D34 (--append-system-prompt-file missing on this
    # Claude Code), or a second, optional prompt file (D35, Mark's own
    # additions) that has no second --append-system-prompt-file slot to go
    # in: either way the combined text travels as one --append-system-prompt
    # argument instead of a file path.
    return config.append_system_prompt_fallback or config.second_prompt_path is not None


def _prompt_text(config: AgentHostConfig) -> str:
    try:
        text = config.prompt_path.read_text()
    except OSError:
        text = ""
    if config.second_prompt_path is not None:
        try:
            text += "\n\n" + config.second_prompt_path.read_text()
        except OSError:
            pass
    return text


def _rotation_day(now: datetime) -> str:
    """D34's other fallback: the first turn of a project after 04:00 local
    time starts a new session, so a long resumed session is never older
    than a day even when --system-prompt-snapshot off turns out not to
    work. The "day" runs from 04:00 to the next day's 04:00."""
    return (now - timedelta(hours=4)).date().isoformat()


def _resolve_session_id(
    config: AgentHostConfig, project: str, stored_id: str | None, *, now: datetime,
) -> str | None:
    if not config.session_rotation_fallback:
        return stored_id
    rotation = KeyValueStore(config.state_dir / "rotation.json")
    today = _rotation_day(now)
    if rotation.get(project) != today:
        rotation.set(project, today)
        return None
    return stored_id


def _write_record(
    config: AgentHostConfig, *, turn_id: str, project: str, session_id: str | None,
    prompt: str, reply_text: str, duration_s: float, outcome: str,
    settings_changed: str | None = None,
) -> None:
    rec = record_mod.TurnRecord(
        turn_id=turn_id,
        time=datetime.now(timezone.utc).isoformat(),
        project=project,
        session_id=session_id,
        prompt=prompt,
        reply=reply_text,
        duration_s=duration_s,
        outcome=outcome,
        settings_changed=settings_changed,
    )
    record_mod.write_record(config.records_dir, rec)


def _run_turn(req: request_mod.Request, working_dir: Path, config: AgentHostConfig) -> dict:
    turn_id = str(uuid.uuid4())
    sessions = SessionStore(config.sessions_dir / "sessions.json")
    stored_id, discarded = sessions.get(req.project)
    rotated_id = _resolve_session_id(config, req.project, stored_id, now=datetime.now())
    if rotated_id != stored_id:
        discarded = True
    stored_id = rotated_id

    inline = _uses_inline_prompt(config)
    argv = command.build_command(
        settings_path=config.turn_settings_path,
        prompt_path=config.prompt_path,
        session_id=stored_id,
        append_prompt_inline=inline,
        prompt_text=_prompt_text(config) if inline else "",
    )
    env = identity.build_child_env(
        config.environment_file,
        identity_env_var=config.identity_env_var,
        session_identity=config.session_identity,
        turn_id=turn_id,
        config_path=config.config_path,
        checkout=working_dir,
    )

    started = time.monotonic()
    # The prompt goes over stdin, never argv (as for the coding session's
    # own adapter, bridge/claude_adapter.py); start_new_session=True so a
    # timeout can kill the whole process group, not just `claude` itself.
    proc = subprocess.Popen(
        argv, cwd=working_dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=env, start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(req.prompt, timeout=config.timeout_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        duration = time.monotonic() - started
        changed = _checkout_settings_problem(working_dir, config)
        _write_record(
            config, turn_id=turn_id, project=req.project, session_id=stored_id,
            prompt=req.prompt, reply_text="", duration_s=duration, outcome="timeout",
            settings_changed=changed,
        )
        return _reply(status="error", error="timeout", turn=turn_id,
                      notice="settings_changed" if changed else None)
    duration = time.monotonic() - started
    # The checkout settings rule, after the turn (D36, check 3): the files
    # were safe when the turn started, so a problem now appeared during it.
    changed = _checkout_settings_problem(working_dir, config)
    changed_notice = "settings_changed" if changed else None

    if proc.returncode != 0:
        _write_record(
            config, turn_id=turn_id, project=req.project, session_id=stored_id,
            prompt=req.prompt, reply_text=stderr, duration_s=duration, outcome="agent_failed",
            settings_changed=changed,
        )
        return _reply(status="error", error="agent_failed", turn=turn_id, notice=changed_notice)

    try:
        payload = json.loads(stdout)
        reply_text = payload["result"]
        new_session_id = payload["session_id"]
        if not isinstance(reply_text, str) or not isinstance(new_session_id, str):
            raise TypeError
    except (json.JSONDecodeError, KeyError, TypeError):
        _write_record(
            config, turn_id=turn_id, project=req.project, session_id=stored_id,
            prompt=req.prompt, reply_text=stdout, duration_s=duration, outcome="agent_failed",
            settings_changed=changed,
        )
        return _reply(status="error", error="agent_failed", turn=turn_id, notice=changed_notice)

    sessions.set(req.project, new_session_id)
    _write_record(
        config, turn_id=turn_id, project=req.project, session_id=new_session_id,
        prompt=req.prompt, reply_text=reply_text, duration_s=duration, outcome="ok",
        settings_changed=changed,
    )

    spoken = last_marker_line(reply_text)
    notice = changed_notice or ("new_session" if discarded else None)
    return _reply(status="ok", turn=turn_id, spoken=spoken, notice=notice)
