"""agent_host/install.sh against a fake root (1.15): a DESTDIR-style prefix, so
nothing outside the test's own directory is written and no root is needed.

The installer refuses a checkout without the gate (phase 1b). Until that
build is merged the source tree used here gets stand-ins for the files it
adds; once they exist the real ones are installed and the stand-ins are not
written.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_host.config import load_config
from tests.test_agent_host_turn_entrypoint import _write_setup

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = "usr/local/lib/jarvis-agent"
ETC = "etc/jarvis-agent"
PHASE_1B = ("gate", "confirm", "action-log", "gate_rules.toml")
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJarvisBridgeTestKeyNotARealOne0000000000000"


@pytest.fixture
def source(tmp_path):
    """A copy of what the installer reads: agent_host/ and bridge/."""
    src = tmp_path / "checkout"
    shutil.copytree(REPO_ROOT / "agent_host", src / "agent_host",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (src / "bridge").mkdir()
    for name in ("__init__.py", "summary.py"):
        shutil.copy(REPO_ROOT / "bridge" / name, src / "bridge" / name)
    for name in PHASE_1B:
        path = src / "agent_host" / name
        if not path.exists():
            path.write_text("#!/bin/sh\nexit 2\n" if "." not in name else "# stand-in\n")
            if "." not in name:
                path.chmod(0o755)
    return src


@pytest.fixture
def destdir(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    return root


def install(source, destdir, *args, env_destdir=True):
    env = {"PATH": os.environ["PATH"]}
    if env_destdir:
        env["DESTDIR"] = str(destdir)
    return subprocess.run(
        ["bash", str(source / "agent_host" / "install.sh"), *args],
        capture_output=True, text=True, env=env, timeout=60,
    )


def snapshot(root: Path) -> dict[str, str]:
    out = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            out[rel] = f"link:{os.readlink(path)}"
        elif path.is_dir():
            out[rel] = f"dir:{path.stat().st_mode & 0o777:o}"
        else:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            out[rel] = f"file:{path.stat().st_mode & 0o777:o}:{digest}"
    return out


def test_installs_the_documented_layout(source, destdir):
    proc = install(source, destdir, "--memory-dir", "/home/someone/.claude/memory")
    assert proc.returncode == 0, proc.stderr

    lib, etc = destdir / LIB, destdir / ETC
    for name in ("turn", "gate", "confirm", "action-log"):
        entry = lib / name
        assert os.readlink(entry) == f"src/agent_host/{name}"
        assert entry.resolve().stat().st_mode & 0o777 == 0o755
    for name in ("gate_rules.toml", "prompt.md"):
        assert os.readlink(lib / name) == f"src/agent_host/{name}"
        assert (lib / name).resolve().stat().st_mode & 0o777 == 0o644
    assert (lib / "src/agent_host/wrapper.py").is_file()
    assert (lib / "src/bridge/summary.py").is_file()
    # The installer and its templates are not part of what runs on the host.
    assert not (lib / "src/agent_host/install.sh").exists()
    assert not (lib / "src/agent_host/turn-settings.json").exists()
    assert not list(lib.rglob("__pycache__"))
    assert not (lib / "src.new").exists() and not (lib / "src.old").exists()

    assert (etc / "rules.d").is_dir() and not list((etc / "rules.d").iterdir())
    assert (etc / "config.toml").read_text() == (source / "agent_host/config.example.toml").read_text()
    assert (etc / "config.toml.example").is_file()
    assert "PATH=" in (etc / "environment").read_text()
    for path in (etc / "config.toml", etc / "environment", lib / "turn-settings.json"):
        assert path.stat().st_mode & 0o777 == 0o644
    # Nothing a group or anyone else could write.
    for path in list(lib.rglob("*")) + list(etc.rglob("*")):
        if not path.is_symlink():
            assert path.stat().st_mode & 0o022 == 0, path


def test_the_turn_settings_name_the_real_paths_not_the_fake_root(source, destdir):
    assert install(source, destdir, "--memory-dir", "/home/someone/.claude/memory").returncode == 0
    settings = json.loads((destdir / LIB / "turn-settings.json").read_text())

    assert set(settings) == {"disableAllHooks", "hooks", "autoMemoryDirectory"}
    assert settings["disableAllHooks"] is False
    assert settings["autoMemoryDirectory"] == "/home/someone/.claude/memory"
    assert set(settings["hooks"]) == {"PreToolUse", "PostToolUse"}
    (pre,), (post,) = settings["hooks"]["PreToolUse"], settings["hooks"]["PostToolUse"]
    assert pre["matcher"] == "*" and post["matcher"] == "*"
    assert [h["command"] for h in pre["hooks"]] == ["/usr/local/lib/jarvis-agent/gate"]
    assert [h["command"] for h in post["hooks"]] == ["/usr/local/lib/jarvis-agent/action-log"]
    assert str(destdir) not in json.dumps(settings)


def test_the_static_turn_settings_in_the_repository_hold_only_what_d34_allows():
    settings = json.loads((REPO_ROOT / "agent_host/turn-settings.json").read_text())
    assert set(settings) == {"disableAllHooks", "hooks"}
    assert settings["disableAllHooks"] is False
    assert "env" not in settings and "permissions" not in settings


def test_a_second_run_changes_nothing_and_keeps_what_was_filled_in(source, destdir):
    assert install(source, destdir, "--memory-dir", "/home/someone/mem").returncode == 0
    config = destdir / ETC / "config.toml"
    config.write_text(config.read_text().replace("enabled = false", "enabled = true"))
    environment = destdir / ETC / "environment"
    environment.write_text("PATH=/opt/bin\nHOME=/home/someone\n")
    (destdir / ETC / "rules.d" / "local.toml").write_text("# host-local\n")
    before = snapshot(destdir)

    # No --memory-dir this time: an update keeps the installed value.
    proc = install(source, destdir)
    assert proc.returncode == 0, proc.stderr
    assert snapshot(destdir) == before
    assert "enabled = true" in config.read_text()


def test_an_update_replaces_the_code_and_drops_files_that_are_gone(source, destdir):
    (source / "agent_host" / "old_module.py").write_text("x = 1\n")
    assert install(source, destdir).returncode == 0
    assert (destdir / LIB / "src/agent_host/old_module.py").exists()

    (source / "agent_host" / "old_module.py").unlink()
    (source / "agent_host" / "prompt.md").write_text("a new prompt\n")
    assert install(source, destdir).returncode == 0
    assert not (destdir / LIB / "src/agent_host/old_module.py").exists()
    assert (destdir / LIB / "prompt.md").read_text() == "a new prompt\n"


def test_dry_run_writes_nothing(source, destdir):
    proc = install(source, destdir, "--dry-run", "--memory-dir", "/home/someone/mem")
    assert proc.returncode == 0, proc.stderr
    assert "nothing was written" in proc.stdout
    assert snapshot(destdir) == {}

    # Also over an existing install.
    assert install(source, destdir).returncode == 0
    before = snapshot(destdir)
    (source / "agent_host" / "prompt.md").write_text("changed\n")
    assert install(source, destdir, "--dry-run").returncode == 0
    assert snapshot(destdir) == before


def test_authorized_keys_and_claude_settings_are_never_touched(source, destdir, tmp_path):
    untouchable = {
        "home/someone/.ssh/authorized_keys": "ssh-ed25519 AAAA existing\n",
        "root/.ssh/authorized_keys": "ssh-ed25519 AAAA root\n",
        "home/someone/.claude/settings.json": '{"hooks": {}}\n',
        "home/someone/.claude.json": "{}\n",
        "etc/claude-code/managed-settings.json": '{"permissions": {}}\n',
    }
    for rel, text in untouchable.items():
        path = destdir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    pub = tmp_path / "bridge.pub"
    pub.write_text(KEY + " jarvis-bridge\n")
    before = snapshot(destdir)

    proc = install(source, destdir, "--pubkey", str(pub), "--from", "192.0.2.7")
    assert proc.returncode == 0, proc.stderr

    after = snapshot(destdir)
    for rel in before:
        assert after[rel] == before[rel], rel
    new = {rel.split("/")[0] + "/" + rel.split("/")[1] for rel in after if rel not in before and "/" in rel}
    assert new <= {"usr/local", "etc/jarvis-agent"}
    # The line is printed for a person to add, not written anywhere.
    line = f'restrict,from="192.0.2.7",command="/usr/local/lib/jarvis-agent/turn" {KEY} jarvis-bridge'
    assert line in proc.stdout.splitlines()
    for rel, value in after.items():
        if value.startswith("file:") and rel not in before:
            assert "restrict,from=" not in (destdir / rel).read_text(errors="replace")


def test_the_installer_refuses_a_checkout_without_the_gate(source, destdir):
    (source / "agent_host" / "gate").unlink()
    proc = install(source, destdir)
    assert proc.returncode != 0
    assert "agent_host/gate" in proc.stderr
    assert snapshot(destdir) == {}


@pytest.mark.skipif(os.geteuid() == 0, reason="root may install for real")
def test_without_root_and_without_a_prefix_nothing_is_installed(source, destdir):
    proc = install(source, destdir, env_destdir=False)
    assert proc.returncode != 0
    assert "run as root" in proc.stderr


def print_line(source, destdir, pub_text, from_addr, tmp_path):
    pub = tmp_path / "key.pub"
    pub.write_text(pub_text)
    return install(source, destdir, "--print-authorized-keys", "--pubkey", str(pub),
                   "--from", from_addr, env_destdir=False)


def test_the_authorized_keys_line_has_the_form_of_d32(source, destdir, tmp_path):
    proc = print_line(source, destdir, KEY + " bridge@pod\n", "192.0.2.7", tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == (
        f'restrict,from="192.0.2.7",command="/usr/local/lib/jarvis-agent/turn" {KEY} bridge@pod\n'
    )
    assert snapshot(destdir) == {}

    # No comment, or one that is not a plain label: the fixed label.
    proc = print_line(source, destdir, KEY + ' evil",command="/bin/sh\n', "100.64.0.9", tmp_path)
    assert proc.stdout == (
        f'restrict,from="100.64.0.9",command="/usr/local/lib/jarvis-agent/turn" {KEY} jarvis-bridge\n'
    )


@pytest.mark.parametrize("pub_text", [
    f'command="/bin/sh" {KEY} x\n',            # options in front of the key
    f"{KEY} a\n{KEY} b\n",                     # two keys
    "ssh-ed25519 not*base64 x\n",
    "-----BEGIN OPENSSH PRIVATE KEY-----\n",
    "",
])
def test_a_key_file_that_is_not_one_bare_public_key_is_refused(source, destdir, tmp_path, pub_text):
    proc = print_line(source, destdir, pub_text, "192.0.2.7", tmp_path)
    assert proc.returncode != 0 and proc.stdout == ""


@pytest.mark.parametrize("from_addr", [
    "*", "192.0.2.*", "192.0.2.7,198.51.100.1", '192.0.2.7",command="/bin/sh', "!10.0.0.1", "a b", "",
])
def test_a_source_address_that_is_a_pattern_or_a_list_is_refused(source, destdir, tmp_path, from_addr):
    proc = print_line(source, destdir, KEY + "\n", from_addr, tmp_path)
    assert proc.returncode != 0 and proc.stdout == ""


def test_the_installed_template_is_a_valid_configuration_with_the_kill_switch_set(source, destdir):
    assert install(source, destdir).returncode == 0
    config = load_config(destdir / ETC / "config.toml")
    assert config.enabled is False
    assert str(config.turn_settings_path) == "/usr/local/lib/jarvis-agent/turn-settings.json"
    assert str(config.prompt_path) == "/usr/local/lib/jarvis-agent/prompt.md"
    assert str(config.environment_file) == "/etc/jarvis-agent/environment"

    # The installed wrapper, started the way sshd starts it (by its fixed path, with
    # nothing useful in the environment), finds its own code and answers `disabled`.
    proc = subprocess.run(
        [sys.executable, str(destdir / LIB / "turn")], input=b"", capture_output=True,
        env={"JARVIS_AGENT_CONFIG": str(destdir / ETC / "config.toml")}, cwd="/", timeout=30,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert json.loads(proc.stdout)["error"] == "disabled"


def test_a_turn_runs_through_the_installed_wrapper(source, destdir, tmp_path):
    assert install(source, destdir).returncode == 0
    work = tmp_path / "work"
    work.mkdir()
    config_toml, record_path = _write_setup(work)
    request = json.dumps({"v": 1, "project": "proj", "prompt": "hello", "confirm": None}).encode()

    proc = subprocess.run(
        [sys.executable, str(destdir / LIB / "turn")], input=request, capture_output=True,
        env={"JARVIS_AGENT_CONFIG": str(config_toml)}, cwd="/", timeout=30,
    )

    assert proc.returncode == 0, proc.stderr.decode()
    assert json.loads(proc.stdout)["spoken"] == "SPOKEN SUMMARY: all set."
    assert json.loads(record_path.read_text())["stdin"] == "hello"
