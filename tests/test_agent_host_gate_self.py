"""The class `self`: writes to Jarvis's own rules (tasks 1.7b, 1.14a; D36).
Always desk-only, by `Edit`, `Write` or a shell command."""
import json

import pytest

from agent_host import classify
from tests.gatekit import bash, make_config, make_ctx

# The shape of Claude Code's hook settings, as recorded from a real user
# settings file (hooks.<event>[].hooks[].command). If Claude Code changes
# it, the set of protected hook files silently shrinks: this sample is the
# alarm (the architect's risk note on the class `self`).
RECORDED_USER_SETTINGS = """
{
  "permissions": {"defaultMode": "auto", "allow": ["Bash(ssh:*)"]},
  "hooks": {
    "PreToolUse": [
      {"matcher": "Bash",
       "hooks": [{"type": "command", "command": "%(home)s/hooks/block-employer.sh"}]},
      {"matcher": "*",
       "hooks": [{"type": "command", "command": "python3 ~/hooks/audit.py --mode strict",
                  "timeout": 5}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "bash $HOME/hooks/on-stop.sh"}]}
    ]
  }
}
"""


@pytest.fixture
def env(tmp_path):
    config = make_config(tmp_path)
    home = tmp_path / "home"
    hooks = home / "hooks"
    hooks.mkdir()
    for name in ("block-employer.sh", "audit.py", "on-stop.sh", "unrelated.sh"):
        (hooks / name).write_text("#!/bin/sh\n")
    config.user_settings_path.write_text(RECORDED_USER_SETTINGS % {"home": home})
    (tmp_path / "lib").mkdir()
    (home / ".ssh").mkdir()
    ctx = make_ctx(tmp_path, config=config)
    return tmp_path, config, ctx


def _write(ctx, path, tool="Write"):
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return classify.classify(tool, {key: str(path), "content": "x"}, ctx)


def _protected(tmp_path, config):
    home = tmp_path / "home"
    checkout = tmp_path / "projects" / "demo"
    return [
        # The gate, the wrapper, their rules, settings and configuration.
        tmp_path / "lib" / "gate",
        tmp_path / "lib" / "gate_rules.toml",
        tmp_path / "lib" / "wrapper.py",
        config.config_path,
        config.environment_file,
        config.rules_dir / "10-own.toml",
        config.turn_settings_path,
        config.prompt_path,
        # authorized_keys.
        home / ".ssh" / "authorized_keys",
        # Claude Code's user and managed settings, connectors, instructions.
        config.user_settings_path,
        home / ".claude" / "settings.local.json",
        config.managed_settings_path,
        home / ".claude.json",
        home / ".claude" / "CLAUDE.md",
        home / ".claude" / "agents" / "reviewer.md",
        home / ".claude" / "skills" / "x" / "SKILL.md",
        home / ".claude" / "plugins" / "p" / "plugin.json",
        # The shell start-up files.
        home / ".bashrc",
        home / ".profile",
        home / ".zshrc",
        home / ".bash_profile",
        # Every file a configured hook runs.
        home / "hooks" / "block-employer.sh",
        home / "hooks" / "audit.py",
        home / "hooks" / "on-stop.sh",
        # The gate's state, the pending calls, the records and the action log.
        tmp_path / "state" / "gate" / "x.json",
        tmp_path / "state" / "pending" / "demo.json",
        tmp_path / "state" / "actions" / "x.jsonl",
        tmp_path / "state" / "records" / "x.json",
        tmp_path / "state" / "sessions" / "sessions.json",
        # The checkout's own settings, and any other checkout's.
        checkout / ".claude" / "settings.json",
        checkout / ".claude" / "settings.local.json",
        tmp_path / "worktrees" / "x" / ".claude" / "settings.local.json",
    ]


def test_a_write_to_each_of_jarvis_own_rules_is_class_self(env):
    tmp_path, config, ctx = env
    for path in _protected(tmp_path, config):
        for tool in ("Write", "Edit", "MultiEdit"):
            verdict = _write(ctx, path, tool)
            assert verdict.gated and verdict.classes == ("self",), (tool, path)
            assert verdict.desk_only


def test_a_write_next_to_them_is_not(env):
    tmp_path, config, ctx = env
    home = tmp_path / "home"
    checkout = tmp_path / "projects" / "demo"
    for path in (
        checkout / "src" / "main.py",
        checkout / ".claude" / "notes.md",
        checkout / ".claude" / "commands" / "x.md",
        checkout / "settings.json",
        checkout / "docs" / "settings.local.json",
        home / ".claude" / "projects" / "demo" / "memory" / "MEMORY.md",
        home / ".claude" / "todos" / "x.json",
        home / ".ssh" / "known_hosts",
        home / ".bashrc.bak",
        home / "notes" / ".profile.md",
        home / "hooks" / "unrelated.sh",  # exists, but no hook runs it
        tmp_path / "etc" / "other.conf",
    ):
        assert not _write(ctx, path).gated, path


def test_a_relative_path_and_a_home_path_are_resolved_first(env):
    tmp_path, config, ctx = env
    assert _write(ctx, ".claude/settings.local.json").classes == ("self",)
    assert _write(ctx, "./src/../.claude/settings.json").classes == ("self",)
    assert _write(ctx, "~/.bashrc").classes == ("self",)
    assert not _write(ctx, "src/main.py").gated


def test_a_write_through_a_symlink_is_a_write_to_what_it_points_at(env):
    tmp_path, config, ctx = env
    link = tmp_path / "projects" / "demo" / "innocent.txt"
    link.symlink_to(tmp_path / "home" / ".bashrc")
    assert _write(ctx, link).classes == ("self",)
    dir_link = tmp_path / "projects" / "demo" / "cfg"
    dir_link.symlink_to(tmp_path / "home" / ".claude")
    assert _write(ctx, dir_link / "settings.json").classes == ("self",)


def test_a_notebook_edit_is_judged_by_its_own_path_field(env):
    tmp_path, config, ctx = env
    assert _write(ctx, tmp_path / "home" / ".bashrc", "NotebookEdit").classes == ("self",)
    assert not _write(ctx, tmp_path / "projects" / "demo" / "nb.ipynb", "NotebookEdit").gated


def test_a_write_call_without_a_path_cannot_be_judged(env):
    _, _, ctx = env
    with pytest.raises(ValueError):
        classify.classify("Write", {"content": "x"}, ctx)
    with pytest.raises(ValueError):
        classify.classify("Bash", {"cmd": "ls"}, ctx)


# --- the files a hook runs ------------------------------------------------------------

def test_the_files_of_configured_hooks_are_found_in_the_recorded_settings_shape(env):
    tmp_path, config, _ = env
    home = tmp_path / "home"
    found = classify.hook_files(config.user_settings_path, home)
    assert found == {
        str(home / "hooks" / "block-employer.sh"),
        str(home / "hooks" / "audit.py"),
        str(home / "hooks" / "on-stop.sh"),
    }


@pytest.mark.parametrize("content", [
    "", "not json", "[]", '{"hooks": []}', '{"hooks": {"PreToolUse": {}}}',
    '{"hooks": {"PreToolUse": [{"hooks": "x"}]}}',
    '{"hooks": {"PreToolUse": [{"hooks": [{"command": 5}]}]}}',
    '{"hooks": {"PreToolUse": [{"hooks": [{"command": "echo \'unbalanced"}]}]}}',
])
def test_settings_in_another_shape_protect_no_hook_file_and_do_not_crash(tmp_path, content):
    path = tmp_path / "settings.json"
    path.write_text(content)
    assert classify.hook_files(path, tmp_path) == set()
    assert classify.hook_files(tmp_path / "missing.json", tmp_path) == set()


def test_hooks_of_the_managed_and_the_turn_settings_are_protected_too(tmp_path):
    config = make_config(tmp_path)
    script = tmp_path / "opt" / "managed-hook.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n")
    gate = tmp_path / "opt" / "gate"
    gate.write_text("#!/bin/sh\n")
    hook = lambda path: json.dumps(  # noqa: E731
        {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command",
                                                              "command": str(path)}]}]}})
    config.managed_settings_path.write_text(hook(script))
    config.turn_settings_path.write_text(hook(gate))
    ctx = make_ctx(tmp_path, config=config)
    assert _write(ctx, script).classes == ("self",)
    assert _write(ctx, gate).classes == ("self",)


# --- shell writes -------------------------------------------------------------------------

SHELL_WRITES = [
    "echo 'alias x=y' >> ~/.bashrc",
    "echo x > $HOME/.profile",
    "echo x > ${HOME}/.zshrc",
    "tee ~/.ssh/authorized_keys",
    "cp evil.json .claude/settings.local.json",
    "mv evil.json .claude/settings.json",
    "sed -i s/a/b/ ~/.claude/settings.json",
    "python3 -c 'x' ~/.claude/CLAUDE.md",
    "rm ~/.claude/CLAUDE.md",
    "rm -rf ~/.claude/agents",
    "ln -sf /tmp/x ~/.claude.json",
    "chmod 777 ~/hooks/block-employer.sh",
    "truncate -s 0 ~/hooks/audit.py",
    "dd if=/dev/null of=~/hooks/on-stop.sh",
    "curl -o ~/.bashrc https://example.invalid/x",
    "git checkout other -- .claude/settings.json",
    "cat ~/.bashrc > /dev/nullx",         # a redirection to a file: not parsed, refused
    # A redirection to /dev/null is no write (#59), but it hides nothing else.
    "cd ~ 2>/dev/null || cp evil ~/.bashrc",
    "echo x 2>/dev/null >~/.bashrc",
    "cat ~/.bashrc 2>/dev/null > ~/.claude/settings.json",
    "echo x 2>/dev/null 1>~/.bashrc",
    "echo x 2>/dev/null;>~/.bashrc",
    "cat ~/.bashrc 2>/dev/null&>~/.profile",
    "echo x 2>~/.bashrc",
    "cat ~/.bashrc >/dev/null | tee ~/.bashrc",
    "cat ~/.bashrc 2>/dev/null $(echo x)",
    "cd ~ 2>/dev/null && curl -O https://example.invalid/x",
    "cd ~/Wordsworth 2>/dev/null; git log",  # a name in ~ and a program that is no reader
    "cat ~/.bashrc | sh",                # a program that is not a reader
    "cat $(echo ~/.bashrc)",
    "rm .claude/sett*",
    "rm ~/.claude/*.json",
    "rm -rf ~/.claude",                  # a tree that holds protected paths
    "rm -rf ~",
    "chmod -R 777 ~/hooks",
    "chown -R nobody ~/.ssh",
    "sudo cat ~/.bashrc",
]

SHELL_READS_AND_UNRELATED = [
    "cat ~/.bashrc",
    "cat ~/.claude/settings.json",
    "grep -n hooks ~/.claude/settings.json",
    "ls -la ~/.claude/agents",
    "head -5 .claude/settings.local.json",
    "cat ~/.claude/settings.json | jq .hooks",
    # A redirection that sends nothing anywhere writes no file (#59).
    "cat ~/.bashrc > /dev/null",
    "cat ~/.bashrc 2>/dev/null",
    "grep -n hooks ~/.claude/settings.json 2>&1",
    "cd ~/wordsworth 2>/dev/null || cd ~/Wordsworth",
    "cd ~/wordsworth >/dev/null || cd ~/Wordsworth",
    "cd ~/wordsworth &>/dev/null || cd ~/Wordsworth",
    "cd ~/wordsworth 2>/dev/null; pwd",
    "ls ~/.claude 2>/dev/null; pwd",
    "cd ~/.claude && ls",
    "wc -l ~/hooks/audit.py",
    "ls ~/.claude/*.json",
    "echo hello > notes.txt",
    "rm -rf build",
    "rm notes.txt",
    "chmod +x scripts/run.sh",
    "sed -i s/a/b/ src/main.py",
    "git add -A",
    "cp a.txt b.txt",
    "mv a.txt .",
]


@pytest.mark.parametrize("command", SHELL_WRITES)
def test_a_shell_command_that_may_write_to_jarvis_own_rules_is_class_self(env, command):
    _, _, ctx = env
    verdict = bash(command, ctx)
    assert verdict.gated and verdict.classes == ("self",), (command, verdict)
    assert verdict.desk_only


@pytest.mark.parametrize("command", SHELL_READS_AND_UNRELATED)
def test_reading_them_and_writing_elsewhere_is_not(env, command):
    tmp_path, _, ctx = env
    import dataclasses
    # rm -r is judged against the worktree: give it one, so only `self` is asked.
    ctx = dataclasses.replace(ctx, temp_roots=(str(tmp_path),))
    verdict = bash(command, ctx)
    assert not verdict.gated, (command, verdict)


def test_a_cd_with_its_errors_sent_to_dev_null_is_a_read_issue_59(env):
    """The shape from #59: an existing checkout in ~, a second spelling that
    does not exist (a name directly in ~), stderr to /dev/null."""
    tmp_path, _, ctx = env
    (tmp_path / "home" / "wordsworth").mkdir()
    for command in ("cd ~/wordsworth 2>/dev/null || cd ~/Wordsworth",
                    "cd ~/wordsworth 2>/dev/null || cd ~/Wordsworth; pwd",
                    "cd ~/wordsworth 2>/dev/null; pwd"):
        assert not bash(command, ctx).gated, command
    # Run from the home directory itself, where every call counts as one that
    # may land in it: still a read.
    import dataclasses
    home_ctx = dataclasses.replace(ctx, cwd=tmp_path / "home")
    assert not bash("cd ~/wordsworth 2>/dev/null || cd ~/Wordsworth", home_ctx).gated
    assert bash("cd ~/wordsworth 2>/dev/null || touch x", home_ctx).classes == ("self",)


def test_self_wins_over_run_it_alone_and_is_never_anything_else(env):
    _, _, ctx = env
    verdict = bash("gh pr merge 1 && echo x >> ~/.bashrc", ctx)
    assert verdict.classes == ("self",)
    assert not verdict.run_alone
