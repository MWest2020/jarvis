"""Orchestrates one turn (1.1-1.13, D33/D34/D36/D37/D38).

One request is one of three things:

- **A prompt.** The turn runs. If the gate made a call pending during it
  (agent_host.decision), the wrapper derives everything about that call
  again, from the call itself and with the same modules the gate used
  (agent_host.naming): the classes, the targets, the hash, the sentence. It
  completes the sentence with what only it can fetch (a merge's review and
  check status), stores the call as the project's pending one, and answers
  with `pending: true`, the read-back in `readback`, `confirm: {id, sha256,
  class, expires_s}` and `spoken: ""`: the agent's own line is not sent at
  all. If the read-back cannot be built, **nothing is pending** and the
  notice is `needs_desk`; there is no fallback sentence, ever.
- **A `confirm` with `yes`.** Accepted only when its id is the project's
  pending call, not older than 60 seconds on this host, and its hash is the
  hash of that call as derived now. Then a new turn starts with that hash
  as the grant in the environment of the `claude` process and a fixed
  prompt: what was confirmed, the directory, the exact call. Anything else
  answers `expired` and nothing runs. After a confirmed call ran, the
  wrapper (not the agent) runs the configured notify command with the
  read-back.
- **A `confirm` with `no`.** The pending call is cleared and no agent turn
  starts.

Every request for a project clears its pending call first, whatever the
request is: a pending call is single use and never survives the next
request. One exception, which is part of the contract with the bridge: a
yes that finds the host `busy` or `low_memory` is answered with that code
before the pending call is touched. Nothing ran, the call stays pending
with the same id and the same expiry, and the bridge asks for the yes again.

The checkout settings rule (D36) holds around every turn, the confirming
one included: refused with `unsafe_settings` before it, and when a key
appeared during it the pending call is dropped and the notice is
`settings_changed`.

The state directory is writable by the agent (D40). The wrapper therefore
trusts nothing in it beyond the call itself: if the stored call was
altered, the read-back describes the altered call and the hash releases the
altered call. What is heard and what a yes releases are the same call.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_host import (
    checkout_settings, classify, command, decision, gatehash, identity, naming, pending,
    projects, prstatus,
)
from agent_host import record as record_mod
from agent_host import request as request_mod
from agent_host.config import DEFAULT_CONFIG_PATH, PENDING_SECONDS, AgentHostConfig, load_config
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

# The closed notice list of D33. `out_of_budget` is not set by any code yet
# (tasks.md 2.6b).
NOTICE_CODES = frozenset({
    "needs_desk", "run_alone", "settings_changed", "target_changed",
    "out_of_budget", "new_session",
})


# One reply carries one notice. When more than one applies they must not
# hide each other by accident: the most important one is sent, by this
# fixed order. First what means "something is wrong with this checkout or
# this call, go to the desk", last what is only good to know.
NOTICE_ORDER: tuple[str, ...] = (
    "settings_changed", "target_changed", "needs_desk", "run_alone", "out_of_budget",
    "new_session",
)
assert set(NOTICE_ORDER) == NOTICE_CODES


def most_important_notice(notices) -> str | None:
    present = {notice for notice in notices if notice is not None}
    for notice in NOTICE_ORDER:
        if notice in present:
            return notice
    return None


def load_runtime_config() -> AgentHostConfig:
    """The wrapper's own configuration file. JARVIS_AGENT_CONFIG overrides the
    installed default path - a test seam only; the child's environment (what
    `claude` sees) is never built from this process's environment, whatever
    it is (agent_host.identity).
    """
    path = Path(os.environ.get("JARVIS_AGENT_CONFIG", str(DEFAULT_CONFIG_PATH)))
    # Made absolute here: the gate is told this path through the turn's
    # environment and runs in another directory (the checkout, a worktree),
    # where a relative path would name another file, one the agent can write.
    return load_config(Path(os.path.abspath(path)))


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

    try:
        working_dir = projects.resolve_project(config, req.project)
    except projects.ProjectError:
        return _reply(status="error", error="no_checkout")

    if req.confirm is not None and req.confirm.answer == "yes":
        # A yes that meets a busy or short-of-memory host is answered with
        # that code BEFORE the pending call is touched: nothing ran, the
        # pending call stays (same id, same expiry), and the bridge asks for
        # the yes again. So the lock comes first here, and only here.
        try:
            with acquire_turn_lock(config.lock_path):
                if available_memory_mb() < config.memory_floor_mb:
                    return _reply(status="error", error="low_memory")
                return _handle(req, working_dir, config, locked=True)
        except Busy:
            return _reply(status="error", error="busy")
    return _handle(req, working_dir, config, locked=False)


def _handle(req: request_mod.Request, working_dir: Path, config: AgentHostConfig, *,
            locked: bool) -> dict:
    # Every other request for the project clears its pending call, whatever
    # the request is (D36, step 4): read once here, gone afterwards.
    stale = pending.take_pending(config.pending_dir, req.project)

    turn_id = str(uuid.uuid4())
    env = identity.build_child_env(
        config.environment_file,
        identity_env_var=config.identity_env_var,
        session_identity=config.session_identity,
        turn_id=turn_id,
        config_path=config.config_path,
        checkout=working_dir,
    )

    confirmed = None
    prompt = req.prompt
    if req.confirm is not None:
        if req.confirm.answer == "no":
            _record_cancelled(config, req.project, stale, env)
            return _reply(status="ok")
        if stale is None or stale.id != req.confirm.id or stale.expired():
            return _reply(status="error", error="expired")

    if command.settings_default_to_bypass(config.user_settings_path):
        return _reply(status="error", error="unsafe_settings")
    # The checkout settings rule, before the turn, the confirming one
    # included (D36, check 1).
    if _checkout_settings_problem(working_dir, config) is not None:
        return _reply(status="error", error="unsafe_settings")

    if req.confirm is not None:
        derived, _ = _derive(stale.call, config, env)
        # The grant is the hash the bridge sent, and only when it equals the
        # hash of the pending call as it is derived now.
        if derived is not None and derived.verdict.rule.startswith("push:") \
                and derived.hash != req.confirm.sha256 \
                and derived.base_hash == gatehash.call_hash(
                    stale.call["tool"], stale.call["cwd"], stale.call["input"]):
            # The pending call is a push and its hash is no longer the one
            # that was read back: the address it resolves to has changed
            # (or cannot be resolved any more). Nothing runs (D36).
            _write_record(
                config, turn_id=turn_id, project=req.project, session_id=None, prompt="",
                reply_text="", duration_s=0.0, outcome="target_changed",
                gated_actions=[{"outcome": "refused", "code": "target_changed"}],
            )
            return _reply(status="ok", notice="target_changed")
        if derived is None or not isinstance(derived.outcome, naming.Named) \
                or derived.hash is None or derived.hash != req.confirm.sha256:
            return _reply(status="error", error="expired")
        confirmed = _Confirmed(req.confirm.sha256, derived.base_hash, derived.outcome.sentence)
        prompt = confirming_prompt(confirmed.sentence, stale.call)
        env["JARVIS_GRANT"] = confirmed.grant
        env["JARVIS_GRANT_BASE"] = confirmed.base_hash

    if locked:
        return _run_turn(req.project, prompt, working_dir, config, turn_id=turn_id,
                         env=env, confirmed=confirmed)
    try:
        with acquire_turn_lock(config.lock_path):
            if available_memory_mb() < config.memory_floor_mb:
                return _reply(status="error", error="low_memory")
            return _run_turn(req.project, prompt, working_dir, config, turn_id=turn_id,
                             env=env, confirmed=confirmed)
    except Busy:
        return _reply(status="error", error="busy")


@dataclass(frozen=True)
class _Confirmed:
    grant: str        # the hash the bridge sent with the yes
    base_hash: str    # the same call without its push address
    sentence: str     # what was confirmed, as derived now


def confirming_prompt(sentence: str, call: dict) -> str:
    """The fixed prompt of a confirming turn (D36, step 5). The wrapper
    writes it; a `confirm` request carries no prompt of its own."""
    if call["tool"] == "Bash":
        what = call["input"]["command"]
    else:
        what = (f"the tool {call['tool']} with exactly this input: "
                f"{gatehash.call_text(call['tool'], call['input'])}")
    return (
        f"The driver confirmed: {sentence} Run exactly this now, in {call['cwd']}: {what}\n"
        "Run it as one call, byte for byte, in that directory, and nothing else that"
        " needs a yes. Then say in your spoken line whether it worked."
    )


def _derive(call: dict, config: AgentHostConfig, env: dict[str, str]):
    """Everything about a stored call, from the call alone, with the gate's
    own modules. (None, None) when that cannot be done."""
    try:
        if not pending.valid_call(call):
            return None, None
        ctx = decision.build_context(config, Path(call["cwd"]), git=classify.git_runner(env))
        return naming.derive(call["tool"], call["input"], ctx, config), ctx
    except Exception:  # noqa: BLE001 - whatever went wrong: no read-back, nothing pending
        return None, None


MAX_READBACK_CHARS = 500


def build_readback(derived: naming.Derived, ctx, call: dict, config: AgentHostConfig,
                   env: dict[str, str]) -> str | None:
    """The sentence Mark hears: the template's words and shape-checked
    targets, what the wrapper fetched about them, and the question. None
    when it cannot be built; then nothing is pending."""
    named = derived.outcome
    if not isinstance(named, naming.Named) or derived.hash is None:
        return None
    parts = [named.sentence]
    cwd = Path(call["cwd"])
    if named.status is not None and named.status[0] == "merge":
        _, number, repo, pin = named.status
        parts.append(prstatus.merge_status(
            number, repo, pin, command=config.pr_status_command, cwd=cwd, env=env))
    elif named.status is not None and named.status[0] == "push" \
            and "merge" in derived.verdict.classes:
        _, remote, pairs = named.status
        parts.append(prstatus.push_status(
            remote, pairs, ctx.rules.default_branches, cwd=cwd, env=env))
    parts.append(naming.SAY_YES)
    text = " ".join(parts)
    if len(text) > MAX_READBACK_CHARS or any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        return None
    return text


def _record_cancelled(config: AgentHostConfig, project: str, stale, env: dict[str, str]) -> None:
    """An exact no: nothing runs, and the record says what was cancelled."""
    if stale is None:
        return
    derived, _ = _derive(stale.call, config, env)
    sentence = derived.outcome.sentence if derived is not None \
        and isinstance(derived.outcome, naming.Named) else None
    rec = record_mod.TurnRecord(
        turn_id=str(uuid.uuid4()), time=datetime.now(timezone.utc).isoformat(),
        project=project, session_id=None, prompt="", reply="", duration_s=0.0,
        outcome="cancelled",
        gated_actions=[{"outcome": "cancelled", "readback": sentence}],
    )
    record_mod.write_record(config.records_dir, rec)


def _notify(config: AgentHostConfig, sentence: str, cwd: Path, env: dict[str, str]) -> str:
    """Run the configured notify command with the read-back (D36, step 7):
    a confirmation Mark did not give is then visible outside the voice
    channel. Run by the wrapper, never by the agent."""
    if not config.notify_command:
        return "not configured"
    try:
        proc = subprocess.run(
            [*config.notify_command, sentence], cwd=cwd, env=env, capture_output=True,
            timeout=30, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return "failed"
    return "sent" if proc.returncode == 0 else "failed"


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
    settings_changed: str | None = None, gated_actions: list[dict] | None = None,
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
        gated_actions=gated_actions or [],
    )
    record_mod.write_record(config.records_dir, rec)


def _after_turn(
    config: AgentHostConfig, project: str, turn_id: str, working_dir: Path,
    env: dict[str, str], confirmed: _Confirmed | None, *, changed: str | None, ok: bool,
) -> tuple[list[dict], dict | None, str | None]:
    """What the gate left behind in this turn: (the gated actions for the
    record, the pending reply fields or None, the notice or None)."""
    state = pending.read_turn_state(config.gate_dir, turn_id)
    actions = [dict(event) for event in state["events"] if isinstance(event, dict)]
    # More than one refusal in a turn: the most important notice, by the
    # fixed order, never just the last one.
    notice = most_important_notice(
        event.get("code") for event in actions if event.get("code") in NOTICE_CODES)

    if confirmed is not None:
        if state["grant_used"] is True:
            actions.append({
                "outcome": "confirmed", "readback": confirmed.sentence,
                "notify": _notify(config, confirmed.sentence, working_dir, env),
            })
        else:
            actions.append({"outcome": "confirmed, not run", "readback": confirmed.sentence})

    call = state["pending"]
    if changed is not None:
        # D36, check 3: a key appeared during the turn. Whatever was pending
        # is dropped, and the record says why.
        if call is not None:
            actions.append({"outcome": "dropped", "why": changed})
        return actions, None, "settings_changed"
    if call is None or not ok:
        return actions, None, notice

    derived, ctx = _derive(call, config, env)
    readback = build_readback(derived, ctx, call, config, env) if derived is not None else None
    if readback is None:
        # No fallback sentence, ever: nothing is pending (D36, step 4).
        actions.append({"outcome": "refused", "code": "needs_desk",
                        "why": "the read-back could not be built"})
        return actions, None, "needs_desk"
    pending_id = str(uuid.uuid4())
    pending.store_pending(config.pending_dir, project, pending_id, call)
    actions.append({"outcome": "read back", "readback": readback})
    fields = {
        "readback": readback,
        "confirm": {
            "id": pending_id, "sha256": derived.hash,
            "class": ", ".join(derived.verdict.classes), "expires_s": PENDING_SECONDS,
        },
    }
    return actions, fields, None


def _run_turn(
    project: str, prompt: str, working_dir: Path, config: AgentHostConfig, *,
    turn_id: str, env: dict[str, str], confirmed: _Confirmed | None = None,
) -> dict:
    sessions = SessionStore(config.sessions_dir / "sessions.json")
    stored_id, discarded = sessions.get(project)
    rotated_id = _resolve_session_id(config, project, stored_id, now=datetime.now())
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

    started = time.monotonic()
    # The prompt goes over stdin, never argv (as for the coding session's
    # own adapter, bridge/claude_adapter.py); start_new_session=True so a
    # timeout can kill the whole process group, not just `claude` itself.
    proc = subprocess.Popen(
        argv, cwd=working_dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=env, start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(prompt, timeout=config.timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        stdout = stderr = ""
    duration = time.monotonic() - started
    # The checkout settings rule, after the turn (D36, check 3): the files
    # were safe when the turn started, so a problem now appeared during it.
    changed = _checkout_settings_problem(working_dir, config)

    reply_text, new_session_id, outcome = "", stored_id, "ok"
    if timed_out:
        outcome = "timeout"
    elif proc.returncode != 0:
        reply_text, outcome = stderr, "agent_failed"
    else:
        try:
            payload = json.loads(stdout)
            reply_text = payload["result"]
            new_session_id = payload["session_id"]
            if not isinstance(reply_text, str) or not isinstance(new_session_id, str):
                raise TypeError
        except (json.JSONDecodeError, KeyError, TypeError):
            reply_text, new_session_id, outcome = stdout, stored_id, "agent_failed"

    actions, pending_fields, notice = _after_turn(
        config, project, turn_id, working_dir, env, confirmed,
        changed=changed, ok=outcome == "ok",
    )
    if outcome == "ok":
        sessions.set(project, new_session_id)
    _write_record(
        config, turn_id=turn_id, project=project, session_id=new_session_id,
        prompt=prompt, reply_text=reply_text, duration_s=duration, outcome=outcome,
        settings_changed=changed, gated_actions=actions,
    )

    if outcome != "ok":
        return _reply(status="error", error=outcome, turn=turn_id, notice=notice)
    if pending_fields is not None:
        # While a confirmation is pending, the agent's own line is not sent.
        return _reply(status="ok", turn=turn_id, spoken="", pending=True, **pending_fields)
    spoken = last_marker_line(reply_text)
    notice = most_important_notice([notice, "new_session" if discarded else None])
    return _reply(status="ok", turn=turn_id, spoken=spoken, notice=notice)
