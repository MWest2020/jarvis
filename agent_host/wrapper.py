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
- **A list** (`{"v":1,"list":true}`, jarvis-general-conversation D51). The
  names of the projects a turn would accept, in a reply of its own shape
  (`LIST_FIELDS`). No pending call touched, no agent, no record. A root that
  cannot be listed is `no_checkout`. Pending clones are retried after the
  answer, in the background, when the turn lock is free.

After a confirming turn in which a confirmed `gh repo create <owner>/<name>`
ran, the wrapper clones that repository into the project root, so that it
is a project at the next turn (D53, agent_host.clones). The agent never
writes there itself. What became of it travels in the reply's optional
`project` field, on every way the reply can take, and never competes with
the notices.

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
    checkout_settings, classify, clones, command, decision, egress, gatehash, identity,
    lifetime, naming, pending, projects, prstatus, trusted,
)
from agent_host import record as record_mod
from agent_host import request as request_mod
from agent_host import timing as timing_mod
from agent_host.boundedio import MAX_SETTINGS_BYTES, read_regular
from agent_host.config import PENDING_SECONDS, AgentHostConfig, hook_config_path, load_config
from agent_host.sessions import KeyValueStore, SessionStore
from agent_host.turnlock import Busy, acquire_turn_lock, available_memory_mb
from bridge.summary import spoken_line

V1_FIELDS: tuple[str, ...] = (
    "v", "status", "error", "turn", "spoken", "pending", "readback", "confirm", "notice",
)
# The answer to a list request (D51). A refusal of one comes in the shape
# above: the kill switch answers before it has read which request it is.
LIST_FIELDS: tuple[str, ...] = ("v", "status", "error", "projects")
# Added to the fields above only when there is something to say (D53): what
# became of a repository a confirmed `gh repo create` made,
# {"name", "ready", "reason"} (bridge.contract). Absent otherwise, so a bridge
# from before it reads every other reply as before.
PROJECT_FIELD = "project"

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
    # Installed, the configuration is one fixed path; the variable counts in
    # a source checkout only (agent_host.config.hook_config_path), where a
    # relative path is made absolute here: the gate runs in another
    # directory, where a relative path would name another file.
    given = os.environ.get("JARVIS_AGENT_CONFIG")
    environ = {"JARVIS_AGENT_CONFIG": os.path.abspath(given)} if given else {}
    path = hook_config_path(environ, Path(__file__).resolve().parent)
    return load_config(Path(os.path.abspath(path)))


def _reply(
    *, status: str, error: str | None = None, turn: str | None = None, spoken: str = "",
    pending: bool = False, readback: str | None = None, confirm: dict | None = None,
    notice: str | None = None, project: dict | None = None,
) -> dict:
    assert status in ("ok", "error")
    assert error is None or error in ERROR_CODES
    assert notice is None or notice in NOTICE_CODES
    reply = {
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
    if project is not None:
        reply[PROJECT_FIELD] = project
    return reply


def disabled_reply() -> dict:
    return _reply(status="error", error="disabled")


def list_reply(names: list[str]) -> dict:
    """The answer to a list request: exactly LIST_FIELDS (D51)."""
    return {"v": 1, "status": "ok", "error": None, "projects": list(names)}


def handle_request(raw: bytes, config: AgentHostConfig) -> dict:
    # The start of the wrapper's own part of the turn (agent_host.timing).
    request_started = time.monotonic()
    if not config.enabled:
        return disabled_reply()

    try:
        req = request_mod.parse_request(raw)
    except request_mod.RequestError:
        return _reply(status="error", error="bad_request")

    if isinstance(req, request_mod.ListRequest):
        try:
            names = projects.list_projects(config)
        except projects.ListError:
            # No list is not an empty list: the bridge lists its own (D52).
            return _reply(status="error", error="no_checkout")
        if clones.pending(config):
            # After the answer, never before it: the bridge waits 8 seconds
            # for a list, and a clone may take longer (D53).
            _in_background(lambda: _retry_clones_if_free(config))
        return list_reply(names)

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
                return _handle(req, working_dir, config, locked=True,
                               request_started=request_started)
        except Busy:
            return _reply(status="error", error="busy")
    return _handle(req, working_dir, config, locked=False, request_started=request_started)


def _handle(req: request_mod.Request, working_dir: Path, config: AgentHostConfig, *,
            locked: bool, request_started: float | None = None) -> dict:
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
    # included (D36, check 1), inside the turn lock: a confirming request
    # holds it already; an ordinary one checks below, once it has it.
    if locked and _checkout_settings_problem(working_dir, config) is not None:
        return _reply(status="error", error="unsafe_settings")

    if req.confirm is not None:
        derived, _ = _derive(stale.call, config, env)
        # The grant is the hash the bridge sent, and only when it equals the
        # hash of the pending call as it is derived now.
        if derived is not None and stale.sha256 == req.confirm.sha256 \
                and stale.base_hash and derived.base_hash == stale.base_hash \
                and derived.hash != stale.sha256:
            # The answer is for the call that was read back, the call is
            # still the same bytes in the same directory, and yet its hash
            # is another one now: what a push resolves to has changed (its
            # address, or the branch behind HEAD). Nothing runs (D36).
            _write_record(
                config, turn_id=turn_id, project=req.project, session_id=None, prompt="",
                reply_text="", duration_s=0.0, outcome="target_changed",
                gated_actions=[{"outcome": "refused", "code": "target_changed"}],
            )
            return _reply(status="ok", notice="target_changed")
        if derived is None or not isinstance(derived.outcome, naming.Named) \
                or derived.hash is None or derived.hash != req.confirm.sha256:
            return _reply(status="error", error="expired")
        confirmed = _Confirmed(req.confirm.sha256, derived.base_hash, derived.outcome.sentence,
                               _created_repository(derived))
        prompt = confirming_prompt(confirmed.sentence, stale.call)
        env["JARVIS_GRANT"] = confirmed.grant
        env["JARVIS_GRANT_BASE"] = confirmed.base_hash

    if locked:
        return _run_turn(req.project, prompt, working_dir, config, turn_id=turn_id,
                         env=env, confirmed=confirmed, request_started=request_started)
    try:
        with acquire_turn_lock(config.lock_path):
            # The checkout settings rule, before the turn (D36, check 1),
            # inside the lock: another turn that is still running in this
            # checkout could otherwise bring the file in between the check
            # and the start.
            if _checkout_settings_problem(working_dir, config) is not None:
                return _reply(status="error", error="unsafe_settings")
            if available_memory_mb() < config.memory_floor_mb:
                return _reply(status="error", error="low_memory")
            return _run_turn(req.project, prompt, working_dir, config, turn_id=turn_id,
                             env=env, confirmed=confirmed, request_started=request_started)
    except Busy:
        return _reply(status="error", error="busy")


@dataclass(frozen=True)
class _Confirmed:
    grant: str        # the hash the bridge sent with the yes
    base_hash: str    # the same call without its push address
    sentence: str     # what was confirmed, as derived now
    # "owner/name" when the confirmed call is a plain `gh repo create`, else None (D53).
    creates: str | None = None


def _created_repository(derived: naming.Derived) -> str | None:
    """The repository a confirmed call creates, when it is exactly a
    `gh repo create <owner>/<name>`: the class `create` and nothing else, no
    wrapper in front of it (`sudo`, an ssh host: then it was made elsewhere or
    as someone else), and the read-back's status names it."""
    named = derived.outcome
    if not isinstance(named, naming.Named) or set(derived.verdict.classes) != {"create"}:
        return None
    command_ = derived.verdict.command
    if command_ is None or command_.wrappers or command_.env:
        return None
    status = named.status
    if not (isinstance(status, tuple) and len(status) == 2 and status[0] == "create"):
        return None
    return status[1] if isinstance(status[1], str) else None


def _in_background(work) -> None:
    """Run `work` in a child that outlives this request: its own session, no
    stdin, stdout or stderr of ours (so the connection closes when we exit).
    The child never returns here."""
    try:
        pid = os.fork()
    except OSError:
        return
    if pid:
        return
    try:
        os.setsid()
        devnull = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(devnull, fd)
        work()
    except BaseException:  # noqa: BLE001 - nothing to report to
        pass
    finally:
        os._exit(0)


def _clone_env(config: AgentHostConfig) -> dict[str, str]:
    """The environment of a turn, for a clone retried outside one."""
    return identity.build_child_env(
        config.environment_file,
        identity_env_var=config.identity_env_var,
        session_identity=config.session_identity,
        turn_id=str(uuid.uuid4()),
        config_path=config.config_path,
        checkout=config.projects_root or config.state_dir,
    )


def _retry_clones_if_free(config: AgentHostConfig) -> None:
    """Pending clones, retried under the turn lock, only when it is free."""
    try:
        with acquire_turn_lock(config.lock_path):
            clones.retry_pending(config, _clone_env(config))
    except Busy:
        pass


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
        defaults = classify.default_branch_names(remote, ctx, cwd)
        parts.append(prstatus.push_status(
            remote, pairs, lambda destination: classify.is_default_destination(destination, defaults),
            cwd=cwd, env=env))
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
    """Run the configured notify command with the read-back and what became
    of it (D36, step 7): a confirmation Mark did not give is then visible
    outside the voice channel. Run by the wrapper, never by the agent, after
    every confirming turn, whatever the agent did with the grant."""
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
    timing: dict | None = None, claude_ended: float | None = None,
    session: dict | None = None, egress_field: dict | None = None,
) -> None:
    if timing is not None and claude_ended is not None:
        timing = {**timing, "wrapper_post_ms": timing_mod.ms(time.monotonic() - claude_ended)}
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
        timing=timing,
        session=session,
        egress=egress_field,
    )
    record_mod.write_record(config.records_dir, rec)


def _start_session(config: AgentHostConfig, project: str, sessions: SessionStore,
                   clock: lifetime.TurnClock) -> lifetime.Start:
    """Which session the turn resumes, if any, and why (agent_host.lifetime)."""
    stored_id, discarded = sessions.get(project)
    # Called on every turn, as before: the rotation fallback keeps its own day.
    rotated_id = _resolve_session_id(config, project, stored_id, now=datetime.now())
    return lifetime.choose(
        stored_id=stored_id, discarded=discarded, rotated_id=rotated_id,
        idle_seconds=clock.idle_seconds(project, datetime.now(timezone.utc)),
        idle_limit_minutes=config.session_idle_minutes,
        was_closed=clock.was_closed(project),
    )


def _after_turn(
    config: AgentHostConfig, project: str, turn_id: str, working_dir: Path,
    env: dict[str, str], confirmed: _Confirmed | None, *, changed: str | None, ok: bool,
) -> tuple[list[dict], dict | None, str | None, dict | None]:
    """What the gate left behind in this turn: (the gated actions for the
    record, the pending reply fields or None, the notice or None, the reply's
    `project` field or None)."""
    state = pending.read_turn_state(config.gate_dir, turn_id)
    actions = [dict(event) for event in state["events"] if isinstance(event, dict)]
    project_field = None
    # More than one refusal in a turn: the most important notice, by the
    # fixed order, never just the last one.
    notice = most_important_notice(
        event.get("code") for event in actions if event.get("code") in NOTICE_CODES)

    if confirmed is not None:
        # Whether the confirmed call ran is decided from what the wrapper
        # itself knows: the grant it issued, and the action log line with
        # that call's hash (written by the PostToolUse hook). Not from the
        # gate's state. "unknown" when the log cannot say.
        outcome = record_mod.ran(config.actions_dir, turn_id, confirmed.base_hash)
        action = {
            "outcome": "confirmed", "ran": outcome, "readback": confirmed.sentence,
            "notify": _notify(config, f"{confirmed.sentence} Outcome: {outcome}.",
                              working_dir, env),
        }
        if confirmed.creates is not None and outcome == "ran":
            # A new repository: the wrapper makes it a project (D53). Only on
            # exactly "ran"; never on the agent's word.
            name = confirmed.creates.partition("/")[2]
            try:
                made = clones.make_project(config, confirmed.creates, env)
            except Exception as exc:  # noqa: BLE001 - whatever it was, not ready
                made = clones.Outcome("needs_desk", f"failed: {type(exc).__name__}")
            action["project"] = made.recorded()
            if projects.project_name_ok(name):
                project_field = made.field(name)
            else:
                # A repository the forge accepts but no project can be named
                # (`.github`, over 64 characters): the field could not carry
                # it, so the record keeps it and the driver hears the desk.
                notice = most_important_notice([notice, "needs_desk"])
        actions.append(action)

    call = state["pending"]
    if changed is not None:
        # D36, check 3: a key appeared during the turn. Whatever was pending
        # is dropped, and the record says why.
        if call is not None:
            actions.append({"outcome": "dropped", "why": changed})
        return actions, None, "settings_changed", project_field
    if call is None or not ok:
        return actions, None, notice, project_field

    derived, ctx = _derive(call, config, env)
    try:
        readback = build_readback(derived, ctx, call, config, env) if derived is not None else None
    except Exception:  # noqa: BLE001 - a git lookup that timed out, anything: no read-back
        readback = None
    if readback is None:
        # No fallback sentence, ever: nothing is pending (D36, step 4).
        actions.append({"outcome": "refused", "code": "needs_desk",
                        "why": "the read-back could not be built"})
        return actions, None, "needs_desk", project_field
    pending_id = str(uuid.uuid4())
    pending.store_pending(config.pending_dir, project, pending_id, call,
                          sha256=derived.hash, base_hash=derived.base_hash)
    actions.append({"outcome": "read back", "readback": readback})
    fields = {
        "readback": readback,
        "confirm": {
            "id": pending_id, "sha256": derived.hash,
            "class": ", ".join(derived.verdict.classes), "expires_s": PENDING_SECONDS,
        },
    }
    return actions, fields, None, project_field


def _run_turn(
    project: str, prompt: str, working_dir: Path, config: AgentHostConfig, *,
    turn_id: str, env: dict[str, str], confirmed: _Confirmed | None = None,
    request_started: float | None = None,
) -> dict:
    if request_started is None:
        request_started = time.monotonic()
    # Pending clones first (D53): the turn lock is held here.
    try:
        clones.retry_pending(config, env)
    except Exception:  # noqa: BLE001 - a retry never stops a turn
        pass
    sessions = SessionStore(config.sessions_dir / "sessions.json")
    clock = lifetime.TurnClock(config.sessions_dir / "last_turn.json")
    start = _start_session(config, project, sessions, clock)
    stored_id = start.session_id
    session_field = start.recorded()

    # Egress (jarvis-credential-proxy, stage 0): with `mode = "observe"` the
    # `claude` process and everything it starts get the proxy variables
    # (empty when the proxy is down or full), and the same values are pinned
    # in the turn settings, passed inline, so a checkout's `env` cannot
    # change them mid-turn (task 0.13). Only the turn's own environment: the
    # wrapper's work (a notify command, a PR status, a clone) keeps `env`.
    egress_env, egress_field = egress.turn_environment(config.egress)
    settings_inline = None
    if egress_env:
        try:
            installed = json.loads(read_regular(config.turn_settings_path, MAX_SETTINGS_BYTES))
            settings_inline = egress.pinned_settings(installed, egress_env)
        except (OSError, ValueError):
            # Not pinned, so not pointed at the proxy either: the turn runs
            # as with "off", and `claude` reports the settings file itself.
            egress_env, egress_field = {}, {"mode": "observe", "proxy": "unpinned"}
    turn_env = {**env, **egress_env}

    inline = _uses_inline_prompt(config)
    argv = command.build_command(
        settings_path=config.turn_settings_path,
        settings_inline=settings_inline,
        prompt_path=config.prompt_path,
        session_id=stored_id,
        append_prompt_inline=inline,
        prompt_text=_prompt_text(config) if inline else "",
        model=config.voice_model,
        effort=config.voice_effort,
    )

    started = time.monotonic()
    # Resolved and created here, before the fork: the child changes into the
    # checkout before it runs `preexec_fn`, and must not have to make
    # directories or resolve anything there (the configuration's paths are
    # absolute, agent_host.config).
    gate_dir = Path(os.path.abspath(config.gate_dir))
    try:
        gate_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        pass  # the child's write fails then, and the turn does not start

    egress_started = time.time()

    def write_trusted_record() -> None:
        # In the child, between fork and exec: the record of the turn's
        # trusted values, keyed by the process that is about to become
        # `claude`, exists before any hook can run (agent_host.trusted).
        trusted.write_for_self(gate_dir, turn_env)

    # The prompt goes over stdin, never argv (as for the coding session's
    # own adapter, bridge/claude_adapter.py); start_new_session=True so a
    # timeout can kill the whole process group, not just `claude` itself.
    try:
        proc = subprocess.Popen(
            argv, cwd=working_dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=turn_env, start_new_session=True,
            preexec_fn=write_trusted_record,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # No `claude`, or no record of the turn's trusted values: the turn
        # does not start, because its gate could not know whom to trust. A
        # child that wrote its record and then failed to exec leaves it
        # behind: removed here.
        trusted.remove_stale(gate_dir)
        _write_record(
            config, turn_id=turn_id, project=project, session_id=stored_id, prompt=prompt,
            reply_text=f"the turn could not start: {exc}", duration_s=0.0,
            outcome="agent_failed", session=session_field, egress_field=egress_field,
        )
        return _reply(status="error", error="agent_failed", turn=turn_id)
    timed_out = False
    try:
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
    finally:
        trusted.remove_for(gate_dir, proc.pid)
    claude_ended = time.monotonic()
    duration = claude_ended - started
    if egress_field is not None and egress_field.get("proxy") == "up":
        # The proxy logs a connection when it is made, so everything of
        # this turn is in the log once `claude` has exited.
        egress_field.update(egress.summarise(config.egress, egress_started, time.time()))
    # The checkout settings rule, after the turn (D36, check 3): the files
    # were safe when the turn started, so a problem now appeared during it.
    changed = _checkout_settings_problem(working_dir, config)

    reply_text, new_session_id, outcome = "", stored_id, "ok"
    payload = None
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
    timing = timing_mod.turn_timing(pre_s=started - request_started, wall_s=duration,
                                    payload=payload, profile=config.voice_profile)

    actions, pending_fields, notice, project_field = _after_turn(
        config, project, turn_id, working_dir, env, confirmed,
        changed=changed, ok=outcome == "ok",
    )
    # The agent may end its reply with `SESSION: close` when the driver asked
    # to close the conversation. It is never spoken (it is cut off here,
    # before the marker line is taken) and it acts on this turn's own project
    # only: the marker has no argument, and `project` is the request's.
    spoken_text, closing = lifetime.split_close_marker(reply_text) if outcome == "ok" \
        else (reply_text, False)
    if outcome == "ok":
        if closing and pending_fields is not None:
            # A call waits for the driver's yes: the conversation is not
            # over, and the confirming turn resumes this session.
            session_field["end"] = "close_ignored"
            closing = False
        if closing:
            sessions.drop(project)
            clock.closed(project)
            session_field["end"] = "closed"
        else:
            sessions.set(project, new_session_id)
            clock.ended(project, datetime.now(timezone.utc))
    _write_record(
        config, turn_id=turn_id, project=project, session_id=new_session_id,
        prompt=prompt, reply_text=reply_text, duration_s=duration, outcome=outcome,
        settings_changed=changed, gated_actions=actions, timing=timing,
        claude_ended=claude_ended, session=session_field, egress_field=egress_field,
    )
    new_session = "new_session" if start.announced else None

    # What became of a new repository goes with every reply below (D53).
    if outcome != "ok":
        return _reply(status="error", error=outcome, turn=turn_id, notice=notice,
                      project=project_field)
    if pending_fields is not None:
        # While a confirmation is pending, the agent's own line is not sent.
        # A notice still is: that a new session started must not be lost.
        return _reply(status="ok", turn=turn_id, spoken="", pending=True,
                      notice=new_session, project=project_field, **pending_fields)
    spoken = spoken_line(spoken_text)
    notice = most_important_notice([notice, new_session])
    return _reply(status="ok", turn=turn_id, spoken=spoken, notice=notice,
                  project=project_field)
