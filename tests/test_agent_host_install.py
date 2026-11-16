"""agent_host/install.sh against a fake root (1.15): a DESTDIR-style prefix, so
nothing outside the test's own directory is written and no root is needed.

The source is a git checkout made from the real files of this repository
(the installer takes only what git tracks), so what is installed here is the
real wrapper, gate, confirm switch, action log and rules.
"""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_host.config import load_config
from tests.test_agent_host_turn_entrypoint import _write_setup

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = "usr/local/lib/jarvis-agent"
ETC = "etc/jarvis-agent"
ENTRY_POINTS = ("turn", "gate", "confirm", "action-log")
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJarvisBridgeTestKeyNotARealOne0000000000000"


@pytest.fixture
def source(tmp_path):
    """A git checkout holding what the installer reads: agent_host/ and bridge/."""
    src = tmp_path / "checkout"
    shutil.copytree(REPO_ROOT / "agent_host", src / "agent_host",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (src / "bridge").mkdir()
    for name in ("__init__.py", "summary.py"):
        shutil.copy(REPO_ROOT / "bridge" / name, src / "bridge" / name)
    for name in (*ENTRY_POINTS, "gate_rules.toml"):
        assert (src / "agent_host" / name).is_file(), f"agent_host/{name} is not in the repository"
    git(src, "init", "-q")
    commit(src)
    return src


def git(src, *args):
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
        cwd=src, check=True, capture_output=True,
    )


def commit(src):
    """The installer takes only what is committed (tracked files, a clean tree)."""
    git(src, "add", "-A")
    git(src, "commit", "-q", "--allow-empty", "-m", "state under test")


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


def honour_test_configuration(destdir):
    """Installed code reads one fixed configuration path and ignores
    JARVIS_AGENT_CONFIG, unless the test kit lies next to the package
    (agent_host.config.hook_config_path). The installer never puts it there;
    a test that wants to run the installed wrapper with its own configuration
    does, in its own fake root, after the install."""
    kit = destdir / LIB / "src" / "tests" / "gatekit.py"
    kit.parent.mkdir()
    kit.write_text("")


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

    assert set(settings) == {"disableAllHooks", "env", "hooks", "autoMemoryDirectory"}
    assert settings["disableAllHooks"] is False
    assert settings["autoMemoryDirectory"] == "/home/someone/.claude/memory"
    # The pins (D36): env in --settings outranks a checkout's env. PATH comes from
    # the environment file the installer has just written from its template.
    env = settings["env"]
    assert {k: env[k] for k in ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "GIT_CONFIG_COUNT")} \
        == {"LD_PRELOAD": "", "LD_LIBRARY_PATH": "", "LD_AUDIT": "", "GIT_CONFIG_COUNT": "0"}
    assert set(settings["hooks"]) == {"PreToolUse", "PostToolUse", "PostToolUseFailure"}
    (pre,), (post,), (failure,) = (settings["hooks"][event] for event in (
        "PreToolUse", "PostToolUse", "PostToolUseFailure"))
    assert pre["matcher"] == post["matcher"] == failure["matcher"] == "*"
    assert [h["command"] for h in pre["hooks"]] == ["/usr/local/lib/jarvis-agent/gate"]
    # A hook that runs past its timeout fails OPEN in Claude Code (host check 12,
    # 2.1.322). The gate keeps its own deadline far inside this one (agent_host/gate,
    # DEADLINE_S), so the margin is large.
    assert [h["timeout"] for h in pre["hooks"]] == [30]
    # The action log for a call that succeeded and for one that failed: Claude Code
    # runs PostToolUse only for the first, PostToolUseFailure for the second.
    assert [h["command"] for h in post["hooks"]] == ["/usr/local/lib/jarvis-agent/action-log"]
    assert [h["command"] for h in failure["hooks"]] == ["/usr/local/lib/jarvis-agent/action-log"]
    assert [h["timeout"] for h in post["hooks"] + failure["hooks"]] == [10, 10]
    assert str(destdir) not in json.dumps(settings)


def test_the_static_turn_settings_in_the_repository_hold_only_what_d34_allows():
    settings = json.loads((REPO_ROOT / "agent_host/turn-settings.json").read_text())
    assert set(settings) == {"disableAllHooks", "env", "hooks"}
    assert settings["disableAllHooks"] is False
    assert "permissions" not in settings
    # env holds only pins of what loads code or changes git's configuration; PATH and
    # HOME are added per host by the installer. No grant, no JARVIS_* (D34, D36).
    assert settings["env"] == {"LD_PRELOAD": "", "LD_LIBRARY_PATH": "", "LD_AUDIT": "",
                               "GIT_CONFIG_COUNT": "0"}


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
    settings_key = "usr/local/lib/jarvis-agent/turn-settings.json"
    after = snapshot(destdir)
    assert {k: v for k, v in after.items() if k != settings_key} \
        == {k: v for k, v in before.items() if k != settings_key}
    assert "enabled = true" in config.read_text()
    # The turn settings follow the environment file: PATH and HOME are pinned to it.
    settings = json.loads((destdir / LIB / "turn-settings.json").read_text())
    assert settings["autoMemoryDirectory"] == "/home/someone/mem"
    assert (settings["env"]["PATH"], settings["env"]["HOME"]) == ("/opt/bin", "/home/someone")
    # And a third run with nothing changed changes nothing at all.
    again = snapshot(destdir)
    assert install(source, destdir).returncode == 0
    assert snapshot(destdir) == again


def test_an_update_replaces_the_code_and_drops_files_that_are_gone(source, destdir):
    (source / "agent_host" / "old_module.py").write_text("x = 1\n")
    commit(source)
    assert install(source, destdir).returncode == 0
    assert (destdir / LIB / "src/agent_host/old_module.py").exists()

    (source / "agent_host" / "old_module.py").unlink()
    (source / "agent_host" / "prompt.md").write_text("a new prompt\n")
    commit(source)
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
    commit(source)
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
    commit(source)
    proc = install(source, destdir)
    assert proc.returncode != 0
    assert "agent_host/gate" in proc.stderr
    assert snapshot(destdir) == {}


def test_only_committed_files_are_installed(source, destdir):
    # An untracked file (left by anything that can write the checkout) is not installed,
    # not even when it has the name of a module the wrapper would import.
    (source / "agent_host" / "sitecustomize.py").write_text("raise SystemExit('planted')\n")
    (source / "bridge" / "planted.py").write_text("x = 1\n")
    assert install(source, destdir).returncode == 0
    assert not (destdir / LIB / "src/agent_host/sitecustomize.py").exists()
    assert sorted(p.name for p in (destdir / LIB / "src/bridge").iterdir()) == ["__init__.py", "summary.py"]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, capture_output=True, text=True)
    assert (destdir / LIB / "VERSION").read_text() == head.stdout


def test_an_uncommitted_edit_is_refused_so_version_names_what_runs(source, destdir):
    (source / "agent_host" / "wrapper.py").write_text("raise SystemExit('edited')\n")
    proc = install(source, destdir)
    assert proc.returncode != 0
    assert "uncommitted" in proc.stderr
    assert snapshot(destdir) == {}


def test_a_tracked_symlink_is_refused(source, destdir, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n")
    (source / "agent_host" / "linked.py").symlink_to(outside)
    commit(source)
    proc = install(source, destdir)
    assert proc.returncode != 0 and "linked.py" in proc.stderr
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


@pytest.mark.parametrize("from_addr, printed", [
    ("192.0.2.7", "192.0.2.7"),
    ("192.0.2.7/32", "192.0.2.7"),
    ("2001:db8::7", "2001:db8::7"),
    ("2001:0db8:0000::0007/128", "2001:db8::7"),
])
def test_one_address_is_accepted_and_printed_in_canonical_form(source, destdir, tmp_path, from_addr, printed):
    proc = print_line(source, destdir, KEY + "\n", from_addr, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith(f'restrict,from="{printed}",command=')


@pytest.mark.parametrize("from_addr", [
    # networks, however small or large
    "0.0.0.0/0", "0::/0", "::/0", "10.0.0.0/8", "192.0.2.0/24", "192.0.2.7/31", "2001:db8::/64",
    "2001:db8::7/127", "192.0.2.7/128", "2001:db8::7/32",
    # not a prefix at all
    "1.2.3.4/999", "192.0.2.7/", "192.0.2.7/032", "192.0.2.7/32/32", "192.0.2.7/-1",
    # the unspecified address
    "0.0.0.0", "::",
    # host names
    "bridge.example", "localhost", "node-03", "bridge",
    # not an address
    "192.0.2", "192.0.2.256", "192.0.2.07", "1.2.3.4.5", "fe80::1%eth0", " 192.0.2.7", "192.0.2.7 ",
    # patterns, lists, quotes
    "*", "192.0.2.*", "192.0.2.?", "192.0.2.7,198.51.100.1", '192.0.2.7",command="/bin/sh',
    "!10.0.0.1", "a b", "",
])
def test_anything_but_one_address_is_refused_as_source(source, destdir, tmp_path, from_addr):
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
    honour_test_configuration(destdir)
    proc = subprocess.run(
        [str(destdir / LIB / "turn")], input=b"", capture_output=True,
        env={"JARVIS_AGENT_CONFIG": str(destdir / ETC / "config.toml")}, cwd="/", timeout=30,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert json.loads(proc.stdout)["error"] == "disabled"


def test_a_turn_runs_through_the_installed_wrapper(source, destdir, tmp_path):
    assert install(source, destdir).returncode == 0
    honour_test_configuration(destdir)
    work = tmp_path / "work"
    work.mkdir()
    config_toml, record_path = _write_setup(work)
    request = json.dumps({"v": 1, "project": "proj", "prompt": "hello", "confirm": None}).encode()

    proc = subprocess.run(
        [str(destdir / LIB / "turn")], input=request, capture_output=True,
        env={"JARVIS_AGENT_CONFIG": str(config_toml)}, cwd="/", timeout=30,
    )

    assert proc.returncode == 0, proc.stderr.decode()
    assert json.loads(proc.stdout)["spoken"] == "SPOKEN SUMMARY: all set."
    assert json.loads(record_path.read_text())["stdin"] == "hello"


def test_installed_entry_points_run_under_the_fixed_interpreter_in_isolated_mode(source, destdir, tmp_path):
    assert install(source, destdir).returncode == 0
    turn = destdir / LIB / "turn"
    for name in ENTRY_POINTS:
        entry = destdir / LIB / name
        assert entry.read_text().splitlines()[0] == "#!/usr/bin/python3 -I", name
        assert entry.is_symlink() and entry.resolve().stat().st_mode & 0o777 == 0o755
    # In the repository the script keeps its portable shebang; only the installed copy is pinned.
    assert (source / "agent_host" / "turn").read_text().splitlines()[0] == "#!/usr/bin/env python3"

    honour_test_configuration(destdir)
    # A PATH with another `python3` in front, and a PYTHONPATH that shadows both the
    # wrapper's own package and a standard library module: neither is used.
    marker = tmp_path / "hijacked"
    evil_bin = tmp_path / "evil-bin"
    evil_bin.mkdir()
    fake_python = evil_bin / "python3"
    fake_python.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n")
    fake_python.chmod(0o755)
    evil_lib = tmp_path / "evil-lib"
    (evil_lib / "agent_host").mkdir(parents=True)
    for shadow in (evil_lib / "agent_host" / "__init__.py", evil_lib / "tomllib.py", evil_lib / "json.py"):
        shadow.write_text(f"open({str(marker)!r}, 'w').close()\nraise SystemExit(3)\n")

    proc = subprocess.run(
        [str(turn)], input=b"", capture_output=True, cwd=str(evil_lib), timeout=30,
        env={
            "PATH": f"{evil_bin}:/usr/bin:/bin",
            "PYTHONPATH": str(evil_lib),
            "PYTHONSTARTUP": str(evil_lib / "json.py"),
            "PYTHONHOME": str(evil_lib),
            "JARVIS_AGENT_CONFIG": str(destdir / ETC / "config.toml"),
        },
    )

    assert proc.returncode == 0, proc.stderr.decode()
    assert json.loads(proc.stdout)["error"] == "disabled"
    assert not marker.exists()


def test_every_module_of_the_package_is_installed_and_nothing_of_the_tests(source, destdir):
    assert install(source, destdir).returncode == 0
    installed = {p.name for p in (destdir / LIB / "src/agent_host").iterdir()}
    in_repo = {p.name for p in (REPO_ROOT / "agent_host").iterdir() if p.is_file()}
    assert installed == in_repo - {"install.sh", "config.example.toml", "turn-settings.json"}
    # The hooks take their configuration path from the environment only next to the
    # test kit (agent_host.config.hook_config_path): it must never be installed.
    assert not (destdir / LIB / "src/tests").exists()
    assert not list((destdir / LIB).rglob("gatekit.py"))


def test_the_installed_gate_denies_when_it_cannot_read_its_configuration(source, destdir, tmp_path):
    """The installed gate reads one fixed configuration path and ignores what the
    environment says. Here that file is not the one of this fake root, so whatever
    the gate finds or does not find there, a call is denied: never let through."""
    assert install(source, destdir).returncode == 0
    planted = tmp_path / "planted.toml"
    planted.write_text((destdir / ETC / "config.toml").read_text())
    call = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(tmp_path),
            "tool_input": {"command": "gh repo delete someone/something --yes"}}

    proc = subprocess.run(
        [str(destdir / LIB / "gate")], input=json.dumps(call).encode(), capture_output=True,
        cwd="/", timeout=30,
        env={"JARVIS_TURN": "1", "JARVIS_AGENT_CONFIG": str(planted),
             "JARVIS_TURN_ID": "11111111-1111-4111-8111-111111111111"},
    )

    assert proc.returncode in (0, 2), proc.stderr.decode()
    if proc.returncode == 0:
        decision = json.loads(proc.stdout)["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"


def test_the_installed_wrapper_ignores_a_configuration_named_in_the_environment(source, destdir, tmp_path):
    assert install(source, destdir).returncode == 0
    work = tmp_path / "work"
    work.mkdir()
    planted, record_path = _write_setup(work)
    request = json.dumps({"v": 1, "project": "proj", "prompt": "hello", "confirm": None}).encode()

    subprocess.run(
        [str(destdir / LIB / "turn")], input=request, capture_output=True,
        env={"JARVIS_AGENT_CONFIG": str(planted)}, cwd="/", timeout=30,
    )

    # Whatever the fixed path holds on this machine, the planted configuration (with its
    # own fake `claude` and state directory) was not the one used.
    assert not record_path.exists()
    assert not (work / "state").exists()
