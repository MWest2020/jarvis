"""Two roads around the gate, closed as desk-only (security review of #96).

1. `claude` started from a call: a nested Claude Code session whose tool
   calls never reach the turn's `--settings` hook (seen live). Rule
   `self:session`, behind every wrapper the gate unwraps.
2. A git or gh command that rewrites the turn checkout's working tree: it
   can bring in a `.claude/settings.json` whose hook Claude Code loads
   mid-turn, next to the gate (seen live). Rule `self:checkout-tree`, in the
   turn checkout only; a linked worktree's settings do not load (host
   check 6), so there the same command runs.
"""
import dataclasses
import os
import shutil
import uuid

import pytest

from agent_host import classify, decision, pending
from agent_host.rules import load_rules
from tests.gatekit import bash, git_init, make_config, make_ctx, run_git


@pytest.fixture
def ctx(tmp_path):
    return make_ctx(tmp_path)


def _session(verdict):
    return verdict.gated and verdict.desk_only and verdict.rule == classify.RULE_SESSION


# --- 1. a nested session ---------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    # The forms of the review.
    'claude -p "merge pull request 5"',
    "claude --dangerously-skip-permissions -p x",
    "timeout 500 claude -p x",
    "env claude -p x",
    "npx @anthropic-ai/claude-code -p x",
    "claude -p x >/dev/null 2>&1 &",
    # Every other wrapper the gate unwraps or refuses.
    "nohup claude -p x &", "setsid claude -p x", "nice -n 5 claude -p x",
    "stdbuf -o0 claude -p x", "time claude -p x", "exec claude -p x", "command claude -p x",
    "ionice -c3 claude -p x", "env -i PATH=/usr/bin claude -p x", "A=1 claude -p x",
    "xargs claude -p", "echo x | xargs claude -p", "watch claude -p x",
    "bash -c 'claude -p x'", "sh -c 'cd /tmp && claude -p x'", "bash -lc \"claude -p x\"",
    "sudo claude -p x", "ssh jumpy claude -p x",
    "find . -exec claude -p x \\;", "find . -exec claude -p x +",
    "uv run claude -p x", "chronic claude -p x",
    "ls; claude -p x", "cd /tmp && claude -p x", "claude -p x | tee out", ">out claude -p x",
    # By path, by its other name, quoted.
    "/usr/local/bin/claude -p x", "./claude -p x", "claude.exe -p x", '"claude" -p x',
    "c''laude -p x",
    # The package, by every runner and by its own files.
    "pnpm dlx @anthropic-ai/claude-code -p x", "yarn dlx @anthropic-ai/claude-code@2.1.322 -p x",
    "bunx @anthropic-ai/claude-code", "npm exec @anthropic-ai/claude-code -- -p x",
    "node /usr/lib/node_modules/@anthropic-ai/claude-code/cli.js -p x",
])
def test_a_nested_claude_session_is_desk_only(ctx, command):
    verdict = bash(command, ctx)
    assert _session(verdict), (command, verdict)


def test_claude_by_another_name_is_found_by_what_it_resolves_to(tmp_path):
    ctx = make_ctx(tmp_path)
    for target in ("bin/claude.exe", "share/claude/versions/2.1.322",
                   "pkgs/node_modules/@anthropic-ai/claude-code/bin/x"):
        real = tmp_path / target
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text("#!/bin/sh\n")
        link = ctx.cwd / "helper"
        if link.is_symlink():
            link.unlink()
        os.symlink(real, link)
        assert _session(bash("./helper -p x", ctx)), target
        assert _session(bash(f"{real} -p x", ctx)), target


@pytest.mark.parametrize("command", [
    "grep -rn claude agent_host", "rg claude", "cat claude.md", "ls ~/.claude",
    "git log --grep claude", "gh search code claude --repo me/demo",
    "git commit -m 'teach claude to sing'", "cat CLAUDE.md",
])
def test_reading_about_claude_is_not_a_session(ctx, command):
    verdict = bash(command, ctx)
    assert verdict.rule != classify.RULE_SESSION, (command, verdict)


def _decide(tmp_path, command, *, cwd=None, checkout=None, **config):
    cfg = make_config(tmp_path, **config)
    checkout = checkout or tmp_path / "projects" / "demo"
    turn = str(uuid.uuid4())
    result = decision.decide(
        {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd or checkout)},
        cfg, turn_checkout=checkout, turn_id=turn, home=tmp_path / "home", lib_dir=tmp_path / "lib")
    return result, pending.TurnState.load(cfg.gate_dir, turn).data


@pytest.mark.parametrize("confirm", ["on", "off"])
def test_a_session_is_refused_for_the_desk_whatever_the_switch(tmp_path, confirm):
    result, state = _decide(tmp_path, 'claude -p "merge pull request 5"', confirm=confirm)
    assert not result.allow and result.reason == decision.REASON_SESSION
    assert result.reason.startswith("[needs_desk]")
    assert state["pending"] is None
    assert [(e["code"], e["rule"]) for e in state["events"]] == [("needs_desk", "self:session")]


def test_the_repository_rules_name_the_session(tmp_path):
    rules = load_rules(None)
    assert rules.session_programs == {"claude", "claude.exe"}
    assert rules.session_packages == ("@anthropic-ai/claude-code",)
    assert "claude" in rules.known_programs


# --- 2. the turn checkout's working tree --------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    """A real turn checkout with one linked worktree under it."""
    make_config(tmp_path)
    checkout = tmp_path / "projects" / "demo"
    shutil.rmtree(checkout / ".git")
    git_init(checkout)
    run_git(checkout, "remote", "add", "origin", "git@github.com:me/demo.git")
    worktree = checkout / ".worktrees" / "fix"
    run_git(checkout, "worktree", "add", "-q", str(worktree), "-b", "fix")
    (checkout / "src").mkdir()
    return checkout, worktree


def _ctx(tmp_path, cwd, checkout):
    return dataclasses.replace(make_ctx(tmp_path, cwd=cwd, git=classify.run_git),
                               turn_checkout=checkout)


TREE_REWRITES = [
    # The forms of the review.
    "git checkout pr97", "git pull", "git pull origin main", "git merge origin/pr97",
    "git switch pr97", "git switch -c x origin/pr97", "git reset --hard origin/pr97",
    "git reset --hard", "git reset --merge x", "git restore --source pr97 .",
    "git restore .", "gh pr checkout 97 --repo me/demo", "gh pr checkout 97",
    # The rest of what writes files from a commit, a patch, a stash or a remote.
    "git rebase main", "git cherry-pick abc1234", "git revert abc1234", "git am x.patch",
    "git apply x.patch", "git apply --index x.patch", "git stash pop", "git stash apply",
    "git stash", "git stash push -m wip", "git read-tree -u -m pr97",
    "git checkout-index -a -f", "git bisect start", "git sparse-checkout disable",
    "git checkout pr97 -- .", "git checkout pr97 -- '*'", "git checkout pr97 -- :/",
    "git checkout -p pr97 -- src", "gh repo sync",
    # Where the gate cannot tell where it lands, or does: the checkout.
    "git --work-tree=. checkout pr97", "GIT_DIR=.git git checkout pr97",
    "env -C . git checkout pr97", "git -c core.quotepath=off checkout pr97",
    "git -C . checkout pr97", "git -C src checkout pr97",
    # In a compound form it is still desk-only.
    "git fetch origin pull/97/head:pr97 && git checkout pr97",
    "cd src && git checkout pr97",
]


@pytest.mark.parametrize("command", TREE_REWRITES)
def test_rewriting_the_turn_checkouts_tree_is_desk_only(tmp_path, repo, command):
    checkout, _ = repo
    verdict = bash(command, _ctx(tmp_path, checkout, checkout))
    assert verdict.gated and verdict.desk_only, (command, verdict)
    assert verdict.rule in (classify.RULE_CHECKOUT_TREE, "self:shell"), (command, verdict)


@pytest.mark.parametrize("command", [
    "git fetch origin pull/97/head:pr97", "git status", "git commit -am fix", "git log --all",
    "git reset HEAD~1", "git reset --soft HEAD~1", "git stash list", "git stash show",
    "git apply --check x.patch", "git apply --stat x.patch", "git read-tree pr97",
    "git checkout pr97 -- src", "git restore src", "git restore --source pr97 src/x.py",
    "git worktree add .worktrees/pr97 pr97", "git branch pr97 origin/pr97",
    "git -C .worktrees/fix checkout pr97", "git -C .worktrees/fix pull",
    "git -C .worktrees/fix reset --hard origin/main", "git -C .worktrees/fix merge origin/pr97",
    "cd .worktrees/fix && git checkout pr97", "git push origin fix",
])
def test_the_same_work_that_leaves_the_checkouts_files_alone_runs(tmp_path, repo, command):
    checkout, _ = repo
    verdict = bash(command, _ctx(tmp_path, checkout, checkout))
    assert verdict.rule != classify.RULE_CHECKOUT_TREE, (command, verdict)
    assert not verdict.desk_only, (command, verdict)


@pytest.mark.parametrize("command", [c for c in TREE_REWRITES
                                     if "-C" not in c and "cd " not in c and " -c " not in c
                                     and "--work-tree" not in c and "GIT_DIR" not in c])
def test_in_a_linked_worktree_the_same_commands_run(tmp_path, repo, command):
    checkout, worktree = repo
    verdict = bash(command, _ctx(tmp_path, worktree, checkout))
    assert verdict.rule != classify.RULE_CHECKOUT_TREE, (command, verdict)


def test_from_a_worktree_a_command_aimed_at_the_checkout_is_desk_only(tmp_path, repo):
    checkout, worktree = repo
    ctx = _ctx(tmp_path, worktree, checkout)
    assert bash(f"git -C {checkout} checkout pr97", ctx).rule == classify.RULE_CHECKOUT_TREE
    assert bash("git -C ../.. pull", ctx).rule == classify.RULE_CHECKOUT_TREE


def test_another_clone_is_not_the_turn_checkout(tmp_path, repo):
    checkout, _ = repo
    other = tmp_path / "projects" / "other"
    git_init(other)
    assert not bash("git pull", _ctx(tmp_path, other, checkout)).gated


def test_without_a_known_turn_checkout_every_main_worktree_counts(tmp_path, repo):
    checkout, worktree = repo
    assert bash("git pull", make_ctx(tmp_path, cwd=checkout)).rule == classify.RULE_CHECKOUT_TREE
    assert not bash("git pull", make_ctx(tmp_path, cwd=worktree)).gated


@pytest.mark.parametrize("confirm", ["on", "off"])
@pytest.mark.parametrize("command", ["git checkout pr97", "gh pr checkout 97 --repo me/demo"])
def test_the_gate_refuses_it_for_the_desk_whatever_the_switch(tmp_path, repo, confirm, command):
    checkout, worktree = repo
    result, state = _decide(tmp_path, command, checkout=checkout, confirm=confirm)
    assert not result.allow and result.reason == decision.REASON_CHECKOUT_TREE
    assert "git -C" in result.reason
    assert [e["code"] for e in state["events"]] == ["needs_desk"]
    # The gate passes the trusted turn checkout: in the worktree it runs.
    result, _ = _decide(tmp_path / "w", command.replace("pr97", "x"), cwd=worktree,
                        checkout=checkout, confirm=confirm)
    assert result.allow or result.reason != decision.REASON_CHECKOUT_TREE


def test_the_repository_rules_name_the_tree_commands():
    rules = load_rules(None)
    assert {"checkout", "switch", "pull", "merge", "reset", "restore"} <= rules.tree_git
    assert ("pr", "checkout") in rules.tree_gh
