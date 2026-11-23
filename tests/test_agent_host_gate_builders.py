"""A voice turn starts its own builder (D59, Mark 2026-11-23).

A `general-purpose` subagent starts by voice without a yes, in the turn.
What keeps that safe is that every call the builder makes reaches the same
gate as the turn's own (host check 13): Claude Code runs the turn's
PreToolUse hooks inside the subagent, under the same `claude` process, so
the gate finds the same trusted record and the same turn state. Here the
builder's calls carry what Claude Code adds to the hook input of a call made
inside a subagent (its own `cwd`, `agent_id`, `agent_type`, a transcript of
its own), and the gate and the wrapper are the real ones.

Covered: the start; what still refuses; a gated call inside a builder is read
back exactly like the turn's own, and its yes releases it once; a call
that needs the desk inside a builder reaches the desk and the driver hears
so; nothing a builder starts gets further than the turn could.
"""
import uuid

import pytest

from agent_host import classify, decision, pending
from agent_host.rules import load_rules
from tests.gatekit import make_config, make_ctx, run_git
from tests.test_agent_host_wrapper_confirm import MERGE, MERGE_SENTENCE, Host

BUILDER = ("Agent", {"description": "Fix the bug", "subagent_type": "general-purpose",
                     "prompt": "In a worktree: fix the bug, run the tests, open a pull request."})


def _sub(cwd, agent_id="a1b2c3d4"):
    """The fields of a hook input for a call made inside a subagent."""
    return {"cwd": str(cwd), "agent_id": agent_id, "agent_type": "general-purpose",
            "transcript_path": f"/x/subagents/agent-{agent_id}.jsonl"}


def _builder(**extra):
    tool, data = BUILDER
    return tool, {**data, **extra}


# --- the start -------------------------------------------------------------------------------

@pytest.mark.parametrize("tool", ["Agent", "Task"])
@pytest.mark.parametrize("kind", ["general-purpose", "General Purpose", "general_purpose",
                                  "GENERAL-PURPOSE"])
def test_a_general_purpose_builder_is_not_gated(tmp_path, tool, kind):
    ctx = make_ctx(tmp_path)
    data = {**BUILDER[1], "subagent_type": kind}
    assert not classify.classify(tool, data, ctx).gated


@pytest.mark.parametrize("extra", [{}, {"run_in_background": False},
                                   {"model": "sonnet", "effort": "high"}])
@pytest.mark.parametrize("confirm", ["on", "off"])
def test_a_voice_turn_starts_a_builder_without_a_yes(tmp_path, extra, confirm):
    cfg = make_config(tmp_path, confirm=confirm)
    checkout = tmp_path / "projects" / "demo"
    turn = str(uuid.uuid4())
    tool, data = _builder(**extra)
    result = decision.decide({"tool_name": tool, "tool_input": data, "cwd": str(checkout)},
                             cfg, turn_checkout=checkout, turn_id=turn,
                             home=tmp_path / "home", lib_dir=tmp_path / "lib")
    assert result.allow and result.reason == ""
    # Ungated: nothing pending, nothing refused, nothing for the record.
    state = pending.TurnState.load(cfg.gate_dir, turn).data
    assert state["pending"] is None and state["events"] == []


# --- what still refuses ------------------------------------------------------------------------

@pytest.mark.parametrize("extra,rule", [
    ({"isolation": "remote"}, "subagent:field"),
    ({"isolation": "worktree"}, "subagent:field"),
    ({"name": "builder"}, "subagent:field"),
    ({"team_name": "t"}, "subagent:field"),
    ({"mode": "bypassPermissions"}, "subagent:field"),
    ({"mode": "acceptEdits"}, "subagent:field"),
    ({"run_in_background": True}, "subagent:background"),
    ({"run_in_background": "false"}, "subagent:background"),
])
def test_a_builder_that_could_leave_the_gate_or_the_turn_is_still_refused(tmp_path, extra, rule):
    ctx = make_ctx(tmp_path)
    verdict = classify.classify(*_builder(**extra), ctx)
    assert verdict.gated and verdict.classes == () and verdict.rule == rule


@pytest.mark.parametrize("kind", ["claude", "fork", "statusline-setup", None])
def test_the_other_types_that_can_edit_and_no_type_are_still_refused(tmp_path, kind):
    ctx = make_ctx(tmp_path)
    tool, data = _builder()
    if kind is None:
        del data["subagent_type"]
    else:
        data["subagent_type"] = kind
    verdict = classify.classify(tool, data, ctx)
    assert verdict.gated and verdict.rule == "subagent:type"


@pytest.mark.parametrize("where", ["user", "checkout"])
def test_an_agent_file_named_general_purpose_is_not_the_built_in(tmp_path, where):
    ctx = make_ctx(tmp_path)
    agents = (ctx.claude_dir / "agents" if where == "user"
              else ctx.cwd / ".claude" / "agents")
    agents.mkdir(parents=True)
    (agents / "x.md").write_text("---\nname: general-purpose\ndescription: x\n---\nDo it.\n")
    assert classify.classify(*_builder(), ctx).rule == "subagent:shadowed"


def test_a_host_local_file_can_add_a_builder_type_and_an_agent_file_still_shadows_it(tmp_path):
    rules_dir = tmp_path / "etc" / "rules.d"
    rules_dir.mkdir(parents=True)
    (rules_dir / "local.toml").write_text('[subagents]\nbuilder_types = ["my-builder"]\n')
    rules = load_rules(rules_dir)
    assert rules.subagent_builder_types == ("general-purpose", "my-builder")
    ctx = make_ctx(tmp_path)
    assert not classify.classify(*_builder(subagent_type="my-builder"), ctx).gated
    agents = ctx.cwd / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "my-builder.md").write_text("---\nname: my-builder\n---\n")
    assert classify.classify(*_builder(subagent_type="my-builder"), ctx).rule == \
        "subagent:shadowed"


# --- inside a builder: the wrapper and the gate, end to end ------------------------------------

@pytest.fixture
def host(tmp_path):
    host = Host(tmp_path)
    # The builder works in a worktree of its own inside the checkout (prompt.md).
    host.worktree = host.checkout / ".worktrees" / "fix"
    run_git(host.checkout, "worktree", "add", "-q", str(host.worktree), "-b", "fix")
    return host


def test_a_builder_works_and_opens_a_pull_request_without_a_yes(host, monkeypatch):
    sub = _sub(host.worktree)
    host.agent(monkeypatch, [
        BUILDER,
        ("Write", {"file_path": str(host.worktree / "fix.py"), "content": "x = 1\n"}, sub),
        ("Bash", {"command": "git commit -am fix"}, sub),
        ("Bash", {"command": "git push origin fix"}, sub),
        ("Bash", {"command": "gh pr create --repo me/jarvis --fill"}, sub),
        ("SubagentHandback", {"message": "PR opened"}, sub),
    ], reply="done.\nSPOKEN SUMMARY: The fix is in a pull request.")
    reply = host.ask("fix the bug")
    assert [d.allow for d in host.popens[-1].decisions] == [True] * 6
    assert reply["pending"] is False and reply["notice"] is None
    assert reply["spoken"] == "SPOKEN SUMMARY: The fix is in a pull request."
    assert host.records()[-1]["gated_actions"] == []


def test_a_gated_call_inside_a_builder_is_read_back_like_the_turns_own(host, monkeypatch):
    host.agent(monkeypatch, [BUILDER, ("Bash", {"command": MERGE}, _sub(host.worktree))],
               reply="merged?\nSPOKEN SUMMARY: I merged it.")
    reply = host.ask("fix the bug and merge it")

    refused = host.popens[-1].decisions[1]
    assert not refused.allow and refused.reason == decision.REASON_PENDING
    assert "If you are a subagent, hand back now" in refused.reason
    # The driver hears the read-back built from the call, never the agent's line.
    assert reply["pending"] is True and reply["spoken"] == ""
    assert reply["readback"] == f"{MERGE_SENTENCE} Checks green, review approved. Say yes to go ahead."
    # The call is stored with the builder's own directory, so that it is
    # released only there.
    assert pending.take_pending(host.config.pending_dir, "demo").call == {
        "tool": "Bash", "input": {"command": MERGE}, "cwd": str(host.worktree.resolve())}


def test_the_yes_to_a_builders_call_releases_exactly_that_call_once(host, monkeypatch):
    sub = _sub(host.worktree)
    host.agent(monkeypatch, [BUILDER, ("Bash", {"command": MERGE}, sub)])
    asked = host.ask("fix the bug and merge it")
    # The confirming turn names the builder's directory; the agent runs the
    # call there, once.
    host.agent(monkeypatch, [
        ("Bash", {"command": MERGE}, {"cwd": str(host.worktree)}),
        ("Bash", {"command": MERGE}, {"cwd": str(host.worktree)}),
    ], reply="merged.\nSPOKEN SUMMARY: It is merged.")
    reply = host.answer(asked)
    popen = host.popens[-1]
    assert popen.prompt.startswith(
        f"The driver confirmed: {MERGE_SENTENCE} Run exactly this now,"
        f" in {host.worktree.resolve()}: {MERGE}\n")
    assert [d.allow for d in popen.decisions] == [True, False]
    assert popen.decisions[1].reason == decision.REASON_GRANT_USED
    assert reply["spoken"] == "SPOKEN SUMMARY: It is merged."
    assert host.records()[-1]["gated_actions"][-1]["ran"] == "ran"


def test_a_builder_in_the_confirming_turn_gets_no_more_than_the_one_call(host, monkeypatch):
    sub = _sub(host.worktree)
    host.agent(monkeypatch, [BUILDER, ("Bash", {"command": MERGE}, sub)])
    asked = host.ask("merge it")
    other = MERGE.replace("pr merge 21", "pr merge 22")
    host.agent(monkeypatch, [BUILDER, ("Bash", {"command": other}, sub),
                             ("Bash", {"command": MERGE}, sub),
                             ("Bash", {"command": MERGE}, sub)])
    host.answer(asked)
    decisions = host.popens[-1].decisions
    assert [d.allow for d in decisions] == [True, False, True, False]
    assert decisions[1].reason == decision.REASON_PENDING      # the yes was not for 22
    assert decisions[3].reason == decision.REASON_GRANT_USED


def test_a_builders_gated_call_after_the_turns_own_is_not_read_back(host, monkeypatch):
    host.agent(monkeypatch, [MERGE, BUILDER,
                             ("Bash", {"command": "gh repo delete me/jarvis --yes"},
                              _sub(host.worktree))])
    reply = host.ask()
    assert host.popens[-1].decisions[2].reason == decision.REASON_ONE_AT_A_TIME
    assert reply["readback"].startswith(MERGE_SENTENCE)


@pytest.mark.parametrize("call", [
    ("Bash", {"command": "terraform apply"}),
    ("Bash", {"command": "echo x >> ~/.bashrc"}),
    ("Write", {"file_path": "{checkout}/.claude/settings.json", "content": "{}"}),
    ("Write", {"file_path": "{checkout}/.claude/agents/x.md", "content": "---\nname: x\n---\n"}),
    ("mcp__claude_ai_Gmail__send_message", {"to": ["someone@example.org"], "subject": "s",
                                            "htmlBody": "b"}),
])
def test_a_desk_call_inside_a_builder_goes_to_the_desk_and_the_driver_hears_it(host, monkeypatch,
                                                                             call):
    tool, data = call
    data = {k: v.replace("{checkout}", str(host.checkout)) if isinstance(v, str) else v
            for k, v in data.items()}
    host.agent(monkeypatch, [BUILDER, (tool, data, _sub(host.worktree))],
               reply="x\nSPOKEN SUMMARY: that step was refused.")
    reply = host.ask("fix the bug")
    refused = host.popens[-1].decisions[1]
    assert not refused.allow and refused.reason.startswith("[needs_desk]")
    assert "If you are a subagent, hand back now" in refused.reason
    # Nothing pending, and the notice that makes the bridge say "I passed that
    # on to the coding session", which picks it up from the record.
    assert reply["pending"] is False and reply["notice"] == "needs_desk"
    assert [a["code"] for a in host.records()[-1]["gated_actions"]] == ["needs_desk"]


@pytest.mark.parametrize("nested", [
    {"subagent_type": "fork"},
    {"subagent_type": "general-purpose", "isolation": "remote"},
    {"subagent_type": "general-purpose", "run_in_background": True},
    {"subagent_type": "general-purpose", "name": "helper"},
])
def test_a_builder_cannot_start_what_the_turn_cannot(host, monkeypatch, nested):
    data = {"description": "d", "prompt": "p", **nested}
    host.agent(monkeypatch, [BUILDER, ("Agent", data, _sub(host.worktree))])
    reply = host.ask("fix the bug")
    assert not host.popens[-1].decisions[1].allow
    assert reply["notice"] == "needs_desk"


def test_with_confirmation_off_a_builder_still_cannot_reach_the_desk_only_calls(tmp_path,
                                                                              monkeypatch):
    host = Host(tmp_path, confirm="off")
    sub = _sub(host.checkout)
    host.agent(monkeypatch, [BUILDER,
                             ("Write", {"file_path": str(host.checkout / ".claude" / "settings.json"),
                                        "content": "{}"}, sub),
                             ("Agent", {"description": "d", "prompt": "p",
                                        "subagent_type": "fork"}, sub)])
    host.ask("fix the bug")
    assert [d.allow for d in host.popens[-1].decisions] == [True, False, False]
