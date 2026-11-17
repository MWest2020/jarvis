"""End to end: the real agent_host/turn script, run as a subprocess, talking
to a fake `claude` executable on PATH (as the section intro of tasks.md
phase 1 asks for), not just a monkeypatched subprocess.Popen.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TURN_SCRIPT = REPO_ROOT / "agent_host" / "turn"

FAKE_CLAUDE = """#!/usr/bin/env python3
import json
import os
import sys

record_path = os.environ["FAKE_CLAUDE_RECORD"]
stdin_text = sys.stdin.read()
with open(record_path, "w") as fh:
    json.dump({"argv": sys.argv[1:], "stdin": stdin_text,
               "turn_id": os.environ.get("JARVIS_TURN_ID", ""),
               "ident": os.environ.get("IDENT", "")}, fh)
# A test may script the reply and the session id: one JSON file next to the record.
try:
    with open(record_path + ".reply") as fh:
        scripted = json.load(fh)
except OSError:
    scripted = {}
print(json.dumps({
    "result": scripted.get("result", "work done.\\nSPOKEN SUMMARY: all set."),
    "session_id": scripted.get("session_id", "44444444-4444-4444-4444-444444444444"),
}))
"""


def _write_setup(tmp_path, *, enabled=True, extra=""):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_claude = bin_dir / "claude"
    fake_claude.write_text(FAKE_CLAUDE)
    fake_claude.chmod(0o755)

    record_path = tmp_path / "record.json"

    root = tmp_path / "projects"
    project = root / "proj"
    (project / ".git").mkdir(parents=True)

    env_file = tmp_path / "environment"
    env_file.write_text(f"PATH={bin_dir}:/usr/bin:/bin\nFAKE_CLAUDE_RECORD={record_path}\n")

    turn_settings = tmp_path / "turn-settings.json"
    turn_settings.write_text("{}")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("be jarvis")
    user_settings = tmp_path / "user-settings.json"
    user_settings.write_text("{}")

    config_toml = tmp_path / "config.toml"
    config_toml.write_text(
        f"""
enabled = {"true" if enabled else "false"}
projects_root = "{root}"
state_dir = "{tmp_path / 'state'}"
environment_file = "{env_file}"
turn_settings = "{turn_settings}"
prompt_file = "{prompt_file}"
session_identity = "jarvis"
user_settings_path = "{user_settings}"
{extra}
"""
    )
    return config_toml, record_path


def _run(config_toml, request_bytes):
    return subprocess.run(
        [sys.executable, str(TURN_SCRIPT)],
        input=request_bytes,
        capture_output=True,
        env={"JARVIS_AGENT_CONFIG": str(config_toml)},
        timeout=30,
    )


def test_a_turn_runs_through_the_real_entrypoint_and_the_fake_claude(tmp_path):
    config_toml, record_path = _write_setup(tmp_path)
    request = json.dumps({"v": 1, "project": "proj", "prompt": "hello", "confirm": None}).encode()

    proc = _run(config_toml, request)

    assert proc.returncode == 0, proc.stderr.decode()
    reply = json.loads(proc.stdout)
    assert reply["status"] == "ok"
    assert reply["spoken"] == "SPOKEN SUMMARY: all set."
    assert set(reply) == {"v", "status", "error", "turn", "spoken", "pending",
                           "readback", "confirm", "notice"}

    recorded = json.loads(record_path.read_text())
    assert recorded["argv"] == [
        "-p", "--output-format", "json",
        "--permission-mode", "auto", "--permission-prompts", "none",
        "--system-prompt-snapshot", "off",
        "--append-system-prompt-file", str(tmp_path / "prompt.md"),
        "--settings", str(tmp_path / "turn-settings.json"),
    ]
    assert recorded["stdin"] == "hello"
    assert recorded["ident"] == "jarvis"
    assert recorded["turn_id"]


def test_the_kill_switch_answers_disabled_without_running_claude(tmp_path):
    config_toml, record_path = _write_setup(tmp_path, enabled=False)
    request = json.dumps({"v": 1, "project": "proj", "prompt": "hello", "confirm": None}).encode()

    proc = _run(config_toml, request)

    reply = json.loads(proc.stdout)
    assert reply == {
        "v": 1, "status": "error", "error": "disabled", "turn": None, "spoken": "",
        "pending": False, "readback": None, "confirm": None, "notice": None,
    }
    assert not record_path.exists()
