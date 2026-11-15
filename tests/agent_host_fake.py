"""Test helpers for `full` mode: a fake agent host behind a fake `ssh`, and a
scripted adapter for the tests that need no process at all."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from bridge.agent import AgentTurn, ConfirmAnswer
from bridge.config import AgentHostConfig, ProjectConfig

FAKE_SSH = Path(__file__).with_name("fake_ssh.py")


class FakeAgentHost:
    """A directory with `ssh` in it; put it first on PATH with `install`."""

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        directory.mkdir(parents=True, exist_ok=True)
        ssh = directory / "ssh"
        # The bridge gives ssh a PATH and a HOME and nothing else, so the script
        # names its interpreter itself.
        ssh.write_text(f"#!{sys.executable}\n" + FAKE_SSH.read_text())
        ssh.chmod(0o755)
        self.key_file = directory / "id_ed25519"
        self.known_hosts_file = directory / "known_hosts"
        for path in (self.key_file, self.known_hosts_file):
            path.write_text("not a real key\n")
            path.chmod(0o400)
        self.scenario(mode="wrapper")

    def install(self, monkeypatch) -> None:
        monkeypatch.setenv("PATH", f"{self.dir}{os.pathsep}{os.environ['PATH']}")
        assert shutil.which("ssh") == str(self.dir / "ssh")

    def scenario(self, **fields) -> None:
        (self.dir / "scenario.json").write_text(json.dumps(fields))

    def config(self, *, timeout_s: int = 20) -> AgentHostConfig:
        return AgentHostConfig(host="agent.example", port=22, user="jarvis",
                               key_file=self.key_file,
                               known_hosts_file=self.known_hosts_file, timeout_s=timeout_s)

    def calls(self) -> list[dict]:
        path = self.dir / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def requests(self) -> list[dict]:
        return [json.loads(c["stdin"]) for c in self.calls() if "stdin" in c]

    def turns(self) -> list[dict]:
        """What the fake wrapper did: agent turns, confirmed calls, cancels."""
        path = self.dir / "state.json"
        return json.loads(path.read_text())["turns"] if path.exists() else []


class ScriptedAdapter:
    """An adapter that answers from a list and remembers what it was asked."""

    def __init__(self, name: str, *answers) -> None:
        self.name = name
        self.answers = list(answers)
        self.asked: list[tuple[str, str, ConfirmAnswer | None]] = []

    def run_turn(self, *, project: ProjectConfig, prompt: str,
                 confirm: ConfirmAnswer | None = None) -> AgentTurn:
        self.asked.append((project.name, prompt, confirm))
        answer = self.answers.pop(0) if self.answers else AgentTurn("Done.", "turn-x")
        if isinstance(answer, Exception):
            raise answer
        return answer
