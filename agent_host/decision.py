"""What the gate answers to one tool call (1.7, 1.7b, 1.7d, 1.8a, D36).

The logic behind `agent_host/gate`, kept in a module so that it can be
tested without a process. Phase 1b1: the gate decides and refuses.

- A call that no rule gates is let through in silence, so the user's allow
  rules, the classifier and the other hooks decide as for the coding
  session.
- **Every gated call is denied.** There is no hash, no grant and no pending
  call in this build; they are phase 1b2. Until then nothing is confirmable
  by voice and every gated action is desk-only, which is the safe side.
- The order of the refusals, for a gated call: first the checkout settings
  rule (while a `.claude/settings*.json` of the turn's checkout or of the
  call's own worktree carries `env`, `hooks` or `disableAllHooks`, every
  gated call is denied, whatever else is true of it), then "run it alone"
  for a compound command, then "needs the desk".
- If anything here cannot be done (the rules cannot be read, the call
  cannot be parsed, a crash), the caller denies: failing closed.

The reason of a denial is written for the agent and starts with a code in
square brackets, one of the notice codes of D33, which 1b2's wrapper and
the record will use.
"""
from __future__ import annotations

import os
import pwd
from dataclasses import dataclass
from pathlib import Path

from agent_host import checkout_settings, classify, rules as rules_mod
from agent_host.config import AgentHostConfig

_STOP = (
    " Stop here and end your turn. Do not try another way to do the same thing, and"
    " tell the driver in your spoken line what was refused."
)

REASON_SETTINGS = (
    "[settings_changed] Project settings changed, needs the desk: a .claude/settings"
    " file of this checkout or worktree sets env, hooks or disableAllHooks, or cannot"
    " be read. No gated action runs while that is so." + _STOP
)
REASON_RUN_ALONE = (
    "[run_alone] Run it alone: this action needs a spoken yes, and it can only be"
    " read back as one command, without a separator, pipe, substitution, redirection"
    " or newline anywhere in it (inside quotes too), and without eval, xargs or"
    " source." + _STOP
)
REASON_SELF = (
    "[needs_desk] That one needs the desk: it writes to Jarvis's own rules or"
    " settings, which is never done by voice." + _STOP
)
REASON_DESK = (
    "[needs_desk] That one needs the desk: it is an action that needs a spoken yes,"
    " and it cannot be confirmed by voice." + _STOP
)
REASON_ERROR = (
    "[needs_desk] The gate could not decide about this call, so it is refused. That"
    " one needs the desk." + _STOP
)


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str = ""
    verdict: classify.Verdict | None = None


ALLOW = Decision(allow=True)


def user_home() -> Path:
    """The home directory of the user the gate runs as, from the password
    database and not from `HOME`: the environment is the one thing project
    settings could have touched."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def decide(
    payload: object, config: AgentHostConfig, *, turn_checkout: Path,
    home: Path | None = None, lib_dir: Path | None = None,
    git: classify.GitRunner = classify.run_git,
) -> Decision:
    """Decide about one PreToolUse hook input. Raises on anything it cannot
    read; the caller turns that into a denial."""
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

    rules = rules_mod.load_rules(config.rules_dir)
    ctx = classify.context_for(
        config, rules, cwd=real_cwd,
        home=home if home is not None else user_home(),
        lib_dir=lib_dir if lib_dir is not None else Path(__file__).resolve().parent,
        extra_temp_roots=(os.environ.get("TMPDIR", ""),),
        git=git,
    )
    verdict = classify.classify(tool_name, tool_input, ctx)
    if not verdict.gated:
        return ALLOW

    if checkout_settings.problem(
        turn_checkout, real_cwd, user_settings_path=config.user_settings_path,
    ) is not None:
        return Decision(False, REASON_SETTINGS, verdict)
    if verdict.desk_only:
        return Decision(False, REASON_SELF, verdict)
    if verdict.run_alone:
        return Decision(False, REASON_RUN_ALONE, verdict)
    return Decision(False, REASON_DESK, verdict)
