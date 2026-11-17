"""What the gate answers to one tool call (1.7 to 1.9, D36 "The flow").

The logic behind `agent_host/gate`, kept in a module so that it can be
tested without a process.

- A call that no rule gates is let through in silence, so the user's allow
  rules, the classifier and the other hooks decide as for the coding
  session.
- A gated call whose hash equals the grant in the environment is let
  through in silence too, once. The grant is the hash the bridge sent with a
  spoken yes; the wrapper put it in the environment of the `claude` process
  before the agent existed. For a push the hash includes the push address
  as git resolves it *now*: if that changed since the read-back, the call is
  denied with `target_changed`.
- Every other gated call is denied. What else happens depends on the call:
  - nameable (agent_host.naming): it becomes the turn's **pending** call,
    stored as tool name, input and real-path `cwd` and nothing derived, and
    the agent is told to stop and end the turn. One per turn: every further
    gated call is denied without becoming pending;
  - nameable but for explicit targets that are missing: the denial lists
    exactly those parts, nothing is pending, and the gate remembers the
    call. The next gated call may be its **one repair** (the same words plus
    only options from that list, the same `cwd`), which then becomes
    pending. Anything else gated after such a refusal needs the desk;
  - not nameable, or the class `self`: it needs the desk. Never pending.
  - a command the gate cannot read as one command: with something gated
    found in it, "run it alone", and the turn ends; with nothing gated found
    in it, "run each part as its own call", and the agent may go on. Never
    pending.
- The checkout settings rule comes before all of that: while a
  `.claude/settings*.json` of the turn's checkout or of the call's own
  worktree carries anything but the few harmless keys, every gated call is
  denied, **with or without a matching grant**. The same holds while the
  gate's own environment differs in any `JARVIS_*` value from the one the
  wrapper started the turn with (`env_changed`): project `env` settings
  reached it (host check 6). The turn's checkout, id and grant passed in
  here are the trusted ones (agent_host.trusted), never the gate's own
  environment's.
- `confirm = "off"`, or a call whose classes are all in `confirm_skip`:
  the call is let through and noted as not confirmed, when it is one call
  the gate fully understood. The class `self`, a compound command, inline
  code and a tool nobody listed are refused as always.
- A grant releases exactly once: an atomic "used" marker per grant, and
  the turn's state is read and written under a lock. Both live in the
  gate's state directory, which is in the class `self`.
- If anything here cannot be done (the rules or the state cannot be read,
  the call cannot be parsed, a crash), the caller denies: failing closed.

The reason of a denial is written for the agent and starts with a code in
square brackets. Where it is a notice code of D33, the wrapper passes it on
to the bridge when the turn ends with nothing pending.
"""
from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import os
import pwd
import re
import time
from dataclasses import dataclass
from pathlib import Path

from agent_host import checkout_settings, classify, naming, pending, rules as rules_mod
from agent_host.boundedio import Budget
from agent_host.config import AgentHostConfig

_STOP = (
    " Stop here and end your turn. Do not try another way to do the same thing, and"
    " tell the driver in your spoken line what was refused."
)

REASON_SETTINGS = (
    "[settings_changed] Project settings changed, needs the desk: a .claude/settings"
    " file of this checkout or worktree sets env, hooks or disableAllHooks, or cannot"
    " be read, or such a file has changed the turn's environment. No gated action"
    " runs while that is so." + _STOP
)
REASON_RUN_ALONE = (
    "[run_alone] Run it alone: this action needs a spoken yes, and it can only be"
    " read back as one command, without a separator, pipe, substitution, redirection"
    " or newline anywhere in it (inside quotes too), and without eval, xargs or"
    " source." + _STOP
)
REASON_NOT_UNDERSTOOD = (
    "[split] The gate reads one plain command at a time, and it could not read this"
    " call as one: a pipe, a separator, a substitution, a here-document, a redirection"
    " to a file, a wrapper it does not follow, or a program that runs other commands."
    " What it cannot fully understand it does not let through. Run each part as its own"
    " call, and put text that holds such characters (a commit message, a body) in a"
    " file and pass the file. Nothing was run."
)
REASON_SELF = (
    "[needs_desk] That one needs the desk: it writes to Jarvis's own rules or"
    " settings, which is never done by voice." + _STOP
)
REASON_DESK = (
    "[needs_desk] That one needs the desk: it is an action that needs a spoken yes,"
    " and it cannot be confirmed by voice." + _STOP
)
REASON_PENDING = (
    "[pending] This action needs a spoken yes. The driver will now hear exactly what"
    " it does and be asked. Stop here and end your turn: do not run it another way, do"
    " not ask the driver yourself, and do not start another action that needs a yes."
)
REASON_ONE_AT_A_TIME = (
    "[pending] One confirmation at a time: an action of this turn is already waiting"
    " for the driver's yes, so this one is refused and is not waiting. Stop here and"
    " end your turn; it can be asked for again after the first is done."
)
REASON_TARGET_CHANGED = (
    "[target_changed] The push target changed since it was read back to the driver,"
    " so the confirmed push is refused. That one needs the desk." + _STOP
)
REASON_GRANT_USED = (
    "[needs_desk] The driver's yes was for one run of this action, and it has been"
    " used. That one needs the desk." + _STOP
)


def reason_missing(parts) -> str:
    return (
        "[missing] This action needs a spoken yes, and it can only be read back when"
        " its targets stand in the call itself. Missing: " + "; ".join(parts) + "."
        " Repeat the same command once, unchanged, with only that added, written"
        " explicitly and never guessed from the surroundings. Anything else will need"
        " the desk."
    )


def reason_subagent(rules) -> str:
    """A subagent that is not a read-only helper (#57, D58). By voice that is
    a build or a long job (an editing or background helper): it goes to the
    desk, where the coding session takes the request over from the turn's
    record and pings the driver (Mark 2026-11-17: a request by voice means
    "build it", and a refused builder must not be lost)."""
    return (
        "[needs_desk] By voice only a read-only helper subagent can be started: subagent_type "
        + ", ".join(rules.subagent_types) + ", in the foreground, with no isolation, name,"
        " team or mode, and not one an agent file redefines. A helper that builds or"
        " edits is passed to the coding session at the desk, which takes this request"
        " over and pings the driver. Nothing was run." + _STOP
    )


REASON_ERROR = (
    "[needs_desk] The gate could not decide about this call, so it is refused. That"
    " one needs the desk." + _STOP
)


_GRANT_RE = re.compile(r"[0-9a-f]{64}")
# How long a gate waits for another gate of the same turn to finish with the
# turn's state. Far inside the gate's own deadline (agent_host/gate).
LOCK_WAIT_S = 3.0
# Everything one decision waits on outside itself (git lookups, the state
# lock) shares this budget, so that together they end inside the gate's 5 s
# deadline with room to answer (host check 12).
WAIT_BUDGET_S = 3.5


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str = ""
    verdict: classify.Verdict | None = None
    # With `defer_grant`: the "used" marker the caller must create (O_EXCL)
    # before it lets the call through. Creating it is what uses the grant.
    grant_marker: Path | None = None


ALLOW = Decision(allow=True)


def user_home() -> Path:
    """The home directory of the user the gate runs as, from the password
    database and not from `HOME`: the environment is the one thing project
    settings could have touched."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def build_context(config: AgentHostConfig, cwd: Path, *, home: Path | None = None,
                  lib_dir: Path | None = None, git: classify.GitRunner = classify.run_git,
                  ) -> classify.Context:
    """The classification context of one call. The gate and the wrapper both
    build it here, so that they cannot judge by different surroundings."""
    rules = rules_mod.load_rules(config.rules_dir)
    return classify.context_for(
        config, rules, cwd=cwd,
        home=home if home is not None else user_home(),
        lib_dir=lib_dir if lib_dir is not None else Path(__file__).resolve().parent,
        git=git,
    )


def confirmation_off_for(verdict: classify.Verdict, config: AgentHostConfig) -> bool:
    """Is confirmation switched off for this call?

    Only for a single call the gate fully understood. Never for the class
    `self`, never for a compound command or one the gate could not read,
    never for inline code of an interpreter or a shell fed from stdin, never
    for a write to git's configuration, never for a push whose destination
    the configuration or the environment decides or the gate could not
    place, never for a call reached by unwrapping a shell (`bash -c`),
    never for a tool nobody listed, never for a subagent that is not a
    read-only helper (#57): "off" means "do not ask me", not "do not
    look". With `confirm_skip`, every class of the call must be skipped,
    and a call in no class is in no skipped class.
    """
    if "self" in verdict.classes or verdict.run_alone:
        return False
    if verdict.rule == classify.RULE_NOT_UNDERSTOOD:
        return False
    if verdict.rule.startswith(("interpreter:", "shell:", "tool:", "subagent:", "git:config")):
        # Inline code, a shell on stdin, a tool nobody listed, a subagent
        # that is not a read-only helper, and a change to git's configuration
        # (which changes what every later git call does): desk-only, whatever
        # the switch says.
        return False
    if verdict.rule in classify.PUSH_UNRESOLVED:
        return False  # a push that lands where the gate could not see
    if verdict.command is not None and any(kind == "shell" for kind, _ in verdict.command.wrappers):
        return False  # reached by unwrapping a shell (`bash -c '…'`): always asked
    if config.confirm == "off":
        return True
    return bool(verdict.classes) and set(verdict.classes) <= config.confirm_skip


def grant_marker(config: AgentHostConfig, turn_id: str, grant: str) -> Path:
    return config.gate_dir / f"{turn_id}.{grant}.used"


def consume_grant_marker(path: Path) -> bool:
    """Create the "used" marker of a grant: True exactly once."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600)
    except OSError:
        return False
    os.close(fd)
    return True


def _use_grant_once(config: AgentHostConfig, turn_id: str, grant: str) -> bool:
    """Create the "used" marker of this grant. True exactly once.

    An atomic create (O_EXCL): two calls with the granted hash cannot both
    get through, whatever is in the state file. The marker lives in the
    gate's state directory, which is in the class `self`.
    """
    if not _GRANT_RE.fullmatch(grant):
        return False
    config.gate_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    return consume_grant_marker(grant_marker(config, turn_id, grant))


@contextlib.contextmanager
def _turn_state_lock(config: AgentHostConfig, turn_id: str, budget=None):
    """One gate at a time per turn: the state file is read, changed and
    written under an exclusive lock, so two tool calls judged at the same
    moment cannot both become "the one pending call"."""
    config.gate_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(config.gate_dir / f"{turn_id}.lock",
                 os.O_RDWR | os.O_CREAT | os.O_NONBLOCK | os.O_NOFOLLOW, 0o600)
    try:
        # Never an unbounded wait (host check 12): poll, and give up in time.
        wait = budget.take(LOCK_WAIT_S) if budget is not None else LOCK_WAIT_S
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise pending.StateError("the turn's state stayed locked") from None
                time.sleep(0.02)
        yield
    finally:
        os.close(fd)


def decide(
    payload: object, config: AgentHostConfig, *, turn_checkout: Path | None,
    turn_id: str | None, grant: str | None = None, grant_base: str | None = None,
    env_changed=(), defer_grant: bool = False,
    home: Path | None = None, lib_dir: Path | None = None,
    git: classify.GitRunner = classify.run_git,
) -> Decision:
    """Decide about one PreToolUse hook input. Raises on anything it cannot
    read; the caller turns that into a denial.

    `turn_checkout`, `turn_id`, `grant` and `grant_base` are the turn's
    trusted values; None for the first two when there are none, and then
    every gated call is denied. `env_changed`: the gate's own environment
    differs from the trusted one, and every gated call is denied with
    `settings_changed` (a sequence of the names that differ, or a bool).
    `defer_grant`: a matching, unused grant is not used here; the decision
    names the marker the caller creates before it lets the call through
    (the gate does that in its parent process, after the worker's answer,
    so that a deadline never uses up a grant).

    Every git lookup and the lock wait share one budget (`WAIT_BUDGET_S`).
    A lookup that runs out of time raises (classify.GitTimeout), and the
    caller denies."""
    budget = Budget(WAIT_BUDGET_S)
    token = classify.git_budget.set(budget)
    try:
        return _decide(payload, config, turn_checkout=turn_checkout, turn_id=turn_id,
                       grant=grant, grant_base=grant_base, home=home, lib_dir=lib_dir,
                       git=git, env_changed=env_changed, defer_grant=defer_grant,
                       budget=budget)
    finally:
        classify.git_budget.reset(token)


def _decide(payload, config, *, turn_checkout, turn_id, grant, grant_base, home, lib_dir,
            git, env_changed, defer_grant, budget) -> Decision:
    if not isinstance(payload, dict):
        raise ValueError("the hook input is not an object")
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    cwd = payload.get("cwd")
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("the hook input has no tool name")
    if not isinstance(tool_input, dict):
        raise ValueError("the hook input has no tool input")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        raise ValueError("the hook input has no absolute cwd")
    real_cwd = Path(os.path.realpath(cwd))

    ctx = build_context(config, real_cwd, home=home, lib_dir=lib_dir, git=git)
    ctx = dataclasses.replace(ctx, logical_cwd=Path(os.path.normpath(cwd)))
    verdict = classify.classify(tool_name, tool_input, ctx)
    if not verdict.gated:
        return ALLOW

    if turn_id is None or not pending.is_canonical_uuid(turn_id):
        raise pending.StateError("no trusted turn id, or not one the wrapper issues")
    if turn_checkout is None or not turn_checkout.is_absolute():
        raise pending.StateError("no trusted turn checkout")
    with _turn_state_lock(config, turn_id, budget):
        state = pending.TurnState.load(config.gate_dir, turn_id)

        def deny(reason: str, code: str | None, sentence: str | None = None,
                 why: str | None = None) -> Decision:
            state.note("pending" if reason == REASON_PENDING else "refused",
                       code=code, verdict=verdict, sentence=sentence, why=why)
            state.save()
            return Decision(False, reason, verdict)

        if env_changed:
            names = [] if isinstance(env_changed, bool) else sorted(env_changed)
            return deny(REASON_SETTINGS, "settings_changed",
                        why="environment differs: " + (", ".join(names) or "yes"))
        settings = checkout_settings.problem(
            turn_checkout, real_cwd, user_settings_path=config.user_settings_path)
        if settings is not None:
            return deny(REASON_SETTINGS, "settings_changed", why=settings)
        if verdict.desk_only:
            return deny(REASON_SELF, "needs_desk")
        if verdict.rule == classify.RULE_NOT_UNDERSTOOD:
            return deny(REASON_NOT_UNDERSTOOD, None)
        if verdict.run_alone:
            return deny(REASON_RUN_ALONE, "run_alone")
        if verdict.rule.startswith("subagent:"):
            # Refused and passed to the desk: the coding session builds it.
            return deny(reason_subagent(ctx.rules), "needs_desk")
        if confirmation_off_for(verdict, config):
            state.note("not_confirmed", code=None, verdict=verdict)
            state.save()
            return Decision(True, "", verdict)

        derived = naming.derive(tool_name, tool_input, ctx, config)
        outcome = derived.outcome
        sentence = outcome.sentence if isinstance(outcome, naming.Named) else None

        if grant:
            if derived.hash is not None and derived.hash == grant:
                if defer_grant:
                    marker = grant_marker(config, turn_id, grant)
                    if state.data["grant_used"] or os.path.lexists(marker) \
                            or not _GRANT_RE.fullmatch(grant):
                        return deny(REASON_GRANT_USED, "needs_desk", sentence)
                    state.note("released", code=None, verdict=verdict, sentence=sentence)
                    state.save()
                    return Decision(True, "", verdict, grant_marker=marker)
                if state.data["grant_used"] or not _use_grant_once(config, turn_id, grant):
                    return deny(REASON_GRANT_USED, "needs_desk", sentence)
                state.data["grant_used"] = True
                state.note("released", code=None, verdict=verdict, sentence=sentence)
                state.save()
                return Decision(True, "", verdict)
            if grant_base and derived.base_hash == grant_base:
                # The confirmed bytes in the confirmed directory, and yet another
                # hash: the push address is no longer what was read back.
                return deny(REASON_TARGET_CHANGED, "target_changed", sentence)

        call = pending.make_call(tool_name, tool_input, str(real_cwd))
        if state.data["pending"] is not None:
            return deny(REASON_ONE_AT_A_TIME, None, sentence)
        if state.data["desk"]:
            return deny(REASON_DESK, "needs_desk")

        refused = state.data["refused"]
        if refused is not None:
            is_the_repair = (
                not state.data["repair_used"]
                and isinstance(outcome, naming.Named)
                and tool_name == "Bash" and refused["call"]["tool"] == "Bash"
                and refused["call"]["cwd"] == str(real_cwd)
                and naming.is_repair(refused["call"]["input"].get("command", ""),
                                     tool_input.get("command", ""),
                                     refused["options"], refused["words"])
            )
            state.data["repair_used"] = True
            if not is_the_repair:
                state.data["desk"] = True
                return deny(REASON_DESK, "needs_desk")
            state.data["pending"] = call
            return deny(REASON_PENDING, None, sentence)

        if isinstance(outcome, naming.Named):
            state.data["pending"] = call
            return deny(REASON_PENDING, None, sentence)
        if isinstance(outcome, naming.Missing):
            state.data["refused"] = {"call": call, "options": list(outcome.options),
                                     "words": outcome.words}
            return deny(reason_missing(outcome.parts), None)
        hint = naming.desk_hint(verdict)
        if hint is not None:
            # Refused, with how it can be asked for so that it can be read back.
            # No notice: the agent may ask again in that form.
            return deny(hint, None)
        return deny(REASON_DESK, "needs_desk")
