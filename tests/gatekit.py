"""Shared set-up for the gate's tests (phase 1b1): a configuration in a
temporary directory, and a classification context that never touches the
real home directory, the real rules directory or a real remote."""
from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

from agent_host import classify, rules as rules_mod
from agent_host.config import config_from_dict

REPO_ROOT = Path(__file__).resolve().parent.parent
OWN = ("me",)


def make_config(tmp_path: Path, **overrides):
    """A complete agent host configuration under tmp_path, with one
    checkout `projects/demo` and a fake home directory `home`."""
    root = tmp_path / "projects"
    (root / "demo" / ".git").mkdir(parents=True, exist_ok=True)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    etc = tmp_path / "etc"
    etc.mkdir(exist_ok=True)
    files = {
        "environment_file": etc / "environment",
        "turn_settings": etc / "turn-settings.json",
        "prompt_file": etc / "prompt.md",
        "user_settings_path": home / ".claude" / "settings.json",
    }
    defaults = {
        "environment_file": "PATH=/usr/bin:/bin\n", "turn_settings": "{}",
        "prompt_file": "be jarvis", "user_settings_path": "{}",
    }
    for key, path in files.items():
        if not path.exists():
            path.write_text(defaults[key])
    data = {
        "projects_root": str(root),
        "state_dir": str(tmp_path / "state"),
        "session_identity": "jarvis",
        "rules_dir": str(etc / "rules.d"),
        "managed_settings_path": str(etc / "managed-settings.json"),
        "own_owners": list(OWN),
        **{key: str(path) for key, path in files.items()},
    }
    data.update(overrides)
    return config_from_dict(data, config_path=etc / "config.toml")


def no_git(args, cwd):
    """A `git` that knows nothing: every lookup fails."""
    return None


def make_ctx(tmp_path: Path, *, cwd: Path | None = None, git=no_git, temp_roots=(),
             config=None) -> classify.Context:
    config = config or make_config(tmp_path)
    rules = rules_mod.load_rules(config.rules_dir)
    ctx = classify.context_for(
        config, rules, cwd=cwd or tmp_path / "projects" / "demo", home=tmp_path / "home",
        lib_dir=tmp_path / "lib", git=git,
    )
    # The repository's temp roots include /tmp, where pytest keeps tmp_path:
    # a test names its own, or none.
    return dataclasses.replace(ctx, temp_roots=tuple(str(root) for root in temp_roots))


def bash(command: str, ctx: classify.Context) -> classify.Verdict:
    return classify.classify("Bash", {"command": command}, ctx)


def git_init(path: Path, *, branch: str = "main") -> None:
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init", "-q", "-b", branch)
    run_git(path, "config", "user.email", "test@example.invalid")
    run_git(path, "config", "user.name", "Test")
    run_git(path, "commit", "-q", "--allow-empty", "-m", "first")


def run_git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=path, check=True, capture_output=True, text=True,
    ).stdout.strip()


SHA = "3f9c2ab" + "0123456789abcdef0123456789abcdef0"


def derive(command: str, ctx: classify.Context, config, tool: str = "Bash", tool_input=None):
    """naming.derive for one Bash command (or another tool's input)."""
    from agent_host import naming
    return naming.derive(tool, tool_input if tool_input is not None else {"command": command},
                         ctx, config)


class Turn:
    """One turn as the gate sees it: the same turn id for every call, so
    the gate's state (the pending call, the one repair, the used grant)
    carries from call to call."""

    def __init__(self, tmp_path: Path, config=None, *, cwd: Path | None = None, git=no_git,
                 grant: str | None = None, grant_base: str | None = None):
        import uuid
        self.tmp_path = tmp_path
        self.config = config or make_config(tmp_path)
        self.cwd = cwd or tmp_path / "projects" / "demo"
        self.git = git
        self.turn_id = str(uuid.uuid4())
        self.grant = grant
        self.grant_base = grant_base

    def call(self, command: str = "", *, tool: str = "Bash", tool_input=None, cwd=None):
        from agent_host import decision
        payload = {
            "tool_name": tool,
            "tool_input": tool_input if tool_input is not None else {"command": command},
            "cwd": str(cwd or self.cwd),
        }
        return decision.decide(
            payload, self.config, turn_checkout=self.tmp_path / "projects" / "demo",
            turn_id=self.turn_id, grant=self.grant, grant_base=self.grant_base,
            home=self.tmp_path / "home", lib_dir=self.tmp_path / "lib", git=self.git,
        )

    def state(self) -> dict:
        from agent_host import pending
        return pending.read_turn_state(self.config.gate_dir, self.turn_id)

    def derive(self, command: str, cwd=None):
        """What the wrapper would derive for this call, with the gate's rules."""
        from agent_host import decision, naming
        ctx = decision.build_context(
            self.config, Path(cwd or self.cwd).resolve(), home=self.tmp_path / "home",
            lib_dir=self.tmp_path / "lib", git=self.git)
        return naming.derive("Bash", {"command": command}, ctx, self.config)
