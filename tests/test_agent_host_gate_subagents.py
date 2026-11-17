"""Subagents by voice: read-only helpers only (#57, D58).

One test per rule of [subagents] in gate_rules.toml: a match and a near
miss. That the gate also judges every call a subagent makes is a property
of Claude Code, shown on the host (docs/how-to/agent-host.md, host check
13); here it is the call that starts one.
"""
import os
import uuid

import pytest

from agent_host import classify, decision
from agent_host.rules import RulesError, load_rules
from tests.gatekit import make_config, make_ctx


@pytest.fixture
def ctx(tmp_path):
    return make_ctx(tmp_path)


def _agent(kind, tool="Agent", **extra):
    data = {"description": "Read one page", "prompt": "Read https://example.org", **extra}
    if kind is not None:
        data["subagent_type"] = kind
    return tool, data


def _verdict(ctx, kind, tool="Agent", **extra):
    name, data = _agent(kind, tool, **extra)
    return classify.classify(name, data, ctx)


# --- the read-only types run ---------------------------------------------------------

@pytest.mark.parametrize("kind", ["web-fetch", "Explore", "Plan", "claude-code-guide"])
@pytest.mark.parametrize("tool", ["Agent", "Task"])
def test_a_read_only_type_is_not_gated(ctx, kind, tool):
    assert not _verdict(ctx, kind, tool).gated


def test_the_fields_a_turn_uses_are_allowed(ctx):
    # The call of turn 3e0b6159 had these four, run_in_background false.
    assert not _verdict(ctx, "web-fetch", run_in_background=False).gated
    assert not _verdict(ctx, "Explore", model="sonnet", effort="low",
                        run_in_background=None).gated


@pytest.mark.parametrize("kind", ["Web Fetch", "web_fetch", "WEB-FETCH", "explore",
                                  "web‐fetch"])
def test_a_type_is_compared_as_claude_code_compares_agent_names(ctx, kind):
    assert not _verdict(ctx, kind).gated


# --- every other type is refused -----------------------------------------------------

@pytest.mark.parametrize("kind", [
    "general-purpose", "claude", "fork", "statusline-setup", "dashboard-builder",
    "web-fetcher", "Explorer", "web-fetch2", "", "   ",
])
def test_any_other_type_is_gated(ctx, kind):
    verdict = _verdict(ctx, kind)
    assert verdict.gated and verdict.classes == () and verdict.rule == "subagent:type"


@pytest.mark.parametrize("value", [None, 5, ["Explore"], {"t": "Explore"}])
def test_no_type_or_a_type_that_is_not_a_string_is_gated(ctx, value):
    tool, data = _agent(None)
    if value is not None:
        data["subagent_type"] = value
    verdict = classify.classify(tool, data, ctx)
    assert verdict.gated and verdict.rule == "subagent:type"


# --- input the rules do not know is refused ------------------------------------------

@pytest.mark.parametrize("extra", [
    {"isolation": "worktree"}, {"isolation": "remote"}, {"isolation": None},
    {"name": "helper"}, {"team_name": "t"}, {"mode": "bypassPermissions"},
    {"something_new": 1},
])
def test_a_field_the_rules_do_not_know_is_gated(ctx, extra):
    verdict = _verdict(ctx, "Explore", **extra)
    assert verdict.gated and verdict.rule == "subagent:field"


@pytest.mark.parametrize("value", [True, "true", 1, "yes"])
def test_a_subagent_in_the_background_is_gated(ctx, value):
    verdict = _verdict(ctx, "web-fetch", run_in_background=value)
    assert verdict.gated and verdict.rule == "subagent:background"


# --- a type that an agent file defines is not the built-in ---------------------------

def _agent_file(directory, filename, name=None):
    directory.mkdir(parents=True, exist_ok=True)
    body = f"---\nname: {name}\ndescription: x\ntools: Write\n---\nDo it.\n" if name else "Do it.\n"
    (directory / filename).write_text(body)


def test_a_type_defined_in_the_user_agents_directory_is_gated(tmp_path):
    ctx = make_ctx(tmp_path)
    _agent_file(ctx.claude_dir / "agents", "helper.md", name="Explore")
    verdict = _verdict(ctx, "Explore")
    assert verdict.gated and verdict.rule == "subagent:shadowed"
    assert not _verdict(ctx, "web-fetch").gated


def test_a_type_defined_in_the_checkout_by_file_name_is_gated(tmp_path):
    ctx = make_ctx(tmp_path)
    _agent_file(ctx.cwd / ".claude" / "agents" / "sub", "web-fetch.md")
    assert _verdict(ctx, "web-fetch").rule == "subagent:shadowed"


def test_a_type_defined_above_the_checkout_is_gated(tmp_path):
    ctx = make_ctx(tmp_path)
    _agent_file(ctx.cwd.parent / ".claude" / "agents", "x.md", name='"web fetch"')
    assert _verdict(ctx, "web-fetch").rule == "subagent:shadowed"


def test_an_agent_file_of_another_name_changes_nothing(tmp_path):
    ctx = make_ctx(tmp_path)
    _agent_file(ctx.claude_dir / "agents", "dashboard-builder.md", name="dashboard-builder")
    _agent_file(ctx.cwd / ".claude" / "agents", "reviewer.md", name="reviewer")
    assert not _verdict(ctx, "Explore").gated


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any directory")
def test_an_agents_directory_that_cannot_be_read_gates(tmp_path):
    ctx = make_ctx(tmp_path)
    agents = ctx.cwd / ".claude" / "agents"
    _agent_file(agents / "locked", "a.md", name="a")
    (agents / "locked").chmod(0)
    try:
        assert _verdict(ctx, "Explore").rule == "subagent:shadowed"
    finally:
        (agents / "locked").chmod(0o700)


def test_too_many_agent_files_gate(tmp_path):
    ctx = make_ctx(tmp_path)
    agents = ctx.claude_dir / "agents"
    agents.mkdir(parents=True)
    for i in range(classify._AGENT_FILES_MAX + 1):
        (agents / f"a{i}.md").write_text("x")
    assert _verdict(ctx, "Explore").rule == "subagent:shadowed"


# --- what the gate answers ---------------------------------------------------------------

def _decide(tmp_path, tool, data, **config):
    cfg = make_config(tmp_path, **config)
    checkout = tmp_path / "projects" / "demo"
    return decision.decide(
        {"tool_name": tool, "tool_input": data, "cwd": str(checkout)},
        cfg, turn_checkout=checkout, turn_id=str(uuid.uuid4()),
        home=tmp_path / "home", lib_dir=tmp_path / "lib")


@pytest.mark.parametrize("confirm", ["on", "off"])
def test_a_refused_subagent_needs_the_desk_whatever_the_switch(tmp_path, confirm):
    result = _decide(tmp_path, *_agent("general-purpose"), confirm=confirm)
    assert not result.allow and result.reason == decision.REASON_DESK


def test_a_read_only_subagent_is_let_through_and_so_is_its_report(tmp_path):
    assert _decide(tmp_path, *_agent("web-fetch")).allow
    assert _decide(tmp_path, "SubagentHandback", {"message": "the report"}).allow


# --- the rules as data -------------------------------------------------------------------

def test_the_repository_rules_name_the_subagent_tools_and_types():
    rules = load_rules(None)
    assert rules.subagent_tools == {"Agent", "Task"}
    assert set(rules.subagent_types) == {"web-fetch", "Explore", "Plan", "claude-code-guide"}
    assert not rules.subagent_tools & rules.tools_ungated
    assert "SubagentHandback" in rules.tools_ungated


@pytest.mark.parametrize("table", ["ungated", "write"])
def test_a_host_local_file_that_lists_agent_under_tools_is_an_error(tmp_path, table):
    rules_dir = tmp_path / "rules.d"
    rules_dir.mkdir()
    (rules_dir / "local.toml").write_text(f'[tools]\n{table} = ["Agent"]\n')
    with pytest.raises(RulesError, match="judged by \\[subagents\\]"):
        load_rules(rules_dir)


def test_a_host_local_file_can_add_a_type(tmp_path):
    rules_dir = tmp_path / "etc" / "rules.d"
    rules_dir.mkdir(parents=True)
    (rules_dir / "local.toml").write_text('[subagents]\nread_only_types = ["my-reader"]\n')
    ctx = make_ctx(tmp_path)
    assert not _verdict(ctx, "my-reader").gated
    assert not _verdict(ctx, "Explore").gated
