"""The action log (PostToolUse hook), retention, and the `confirm on|off`
program (tasks 1.7f, 1.11, 1.12b, 1.14; design D36, D38)."""
import json
import os
import subprocess
import sys
import time
import tomllib
import uuid

import pytest

from agent_host import record, switch
from agent_host.config import ConfigError, config_from_dict
from tests.gatekit import REPO_ROOT, make_config

ACTION_LOG = REPO_ROOT / "agent_host" / "action-log"
CONFIRM = REPO_ROOT / "agent_host" / "confirm"
GATE = REPO_ROOT / "agent_host" / "gate"
NOW = "2026-11-16T10:00:00+00:00"


# --- which calls are logged, and what of them --------------------------------------------------

def test_a_bash_call_is_logged_with_its_command_and_exit_status():
    line = record.action_line({
        "tool_name": "Bash", "tool_input": {"command": "git commit -m x", "description": "commit"},
        "cwd": "/srv/repo", "tool_response": {"stdout": "a lot of output", "exit_code": 0},
    }, NOW)
    from agent_host import gatehash
    assert line == {"time": NOW, "tool": "Bash", "what": "git commit -m x", "cwd": "/srv/repo",
                    "status": 0,
                    "hash": gatehash.call_hash("Bash", "/srv/repo", {"command": "git commit -m x"})}
    failed = record.action_line({"tool_name": "Bash", "tool_input": {"command": "false"},
                                 "tool_response": {"exit_code": 1}}, NOW)
    assert failed["status"] == 1 and failed["cwd"] is None


@pytest.mark.parametrize("tool,tool_input,what", [
    ("Write", {"file_path": "/srv/repo/a.py", "content": "secret body"}, "/srv/repo/a.py"),
    ("Edit", {"file_path": "/srv/repo/a.py", "old_string": "a", "new_string": "b"}, "/srv/repo/a.py"),
    ("NotebookEdit", {"notebook_path": "/srv/nb.ipynb"}, "/srv/nb.ipynb"),
    ("mcp__mail__send", {"to": "a@example.org", "body": "long text"}, "a@example.org"),
    ("mcp__x__post_comment", {"body": "text", "issue": 5}, '["body", "issue"]'),
    ("Task", {}, ""),
])
def test_a_tool_that_can_change_something_is_logged_by_its_target_not_its_content(tool, tool_input, what):
    line = record.action_line({"tool_name": tool, "tool_input": tool_input, "tool_response": {}}, NOW)
    assert line["tool"] == tool and line["what"] == what and line["status"] == "ok"
    assert "secret body" not in json.dumps(line) and "long text" not in json.dumps(line)


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch"])
def test_the_read_only_file_and_search_tools_are_not_logged(tool):
    assert record.action_line({"tool_name": tool, "tool_input": {"file_path": "/x"}}, NOW) is None


def test_a_malformed_hook_input_logs_nothing_and_does_not_raise():
    for payload in ({}, {"tool_name": 5}, {"tool_name": ""}, {"tool_input": {"command": "x"}}):
        assert record.action_line(payload, NOW) is None
    line = record.action_line({"tool_name": "Bash", "tool_input": "x", "tool_response": "y"}, NOW)
    assert line["what"] == "" and line["status"] == "ok"


def test_status_words_when_there_is_no_exit_code():
    def status(response):
        return record.action_line({"tool_name": "Bash", "tool_input": {"command": "x"},
                                   "tool_response": response}, NOW)["status"]
    assert status({"interrupted": True}) == "interrupted"
    assert status({"is_error": True}) == "error"
    assert status({"error": "boom"}) == "error"
    assert status({"stdout": ""}) == "ok"
    assert status({"exitCode": 3}) == 3
    assert status({"exit_code": True}) == 1  # a bool is an int to Python; still a status


def test_a_value_shaped_like_a_secret_is_redacted_and_a_long_one_cut():
    line = record.action_line({"tool_name": "Bash", "tool_input": {
        "command": "curl -H 'x-api-key: sk-ant-abc123_DEF-456' https://example.invalid"}}, NOW)
    assert "sk-ant-" not in line["what"] and record.REDACTED in line["what"]
    long = record.action_line({"tool_name": "Bash", "tool_input": {"command": "x" * 5000}}, NOW)
    assert len(long["what"]) == 2000


def test_lines_are_appended_owner_only(tmp_path):
    directory = tmp_path / "state" / "actions"
    turn = str(uuid.uuid4())
    path = record.append_action(directory, turn, {"tool": "Bash", "what": "a"})
    record.append_action(directory, turn, {"tool": "Bash", "what": "b"})
    assert [json.loads(line)["what"] for line in path.read_text().splitlines()] == ["a", "b"]
    assert path.stat().st_mode & 0o777 == 0o600
    assert directory.stat().st_mode & 0o777 == 0o700


# --- the hook as a process -------------------------------------------------------------------------

def _config_file(tmp_path):
    config = make_config(tmp_path)
    path = tmp_path / "etc" / "config.toml"
    path.write_text(
        f'projects_root = "{tmp_path / "projects"}"\nstate_dir = "{tmp_path / "state"}"\n'
        f'environment_file = "{config.environment_file}"\nturn_settings = "{config.turn_settings_path}"\n'
        f'prompt_file = "{config.prompt_path}"\nsession_identity = "jarvis"\n'
        f'user_settings_path = "{config.user_settings_path}"\nrules_dir = "{config.rules_dir}"\n'
        'own_owners = ["me"]\n')
    return path, config


def _hook(script, payload, env, *, isolated=False, raw=None):
    argv = [sys.executable] + (["-I"] if isolated else []) + [str(script)]
    return subprocess.run(argv, input=raw if raw is not None else json.dumps(payload).encode(),
                          capture_output=True, env=env, timeout=30)


def test_the_hook_writes_one_line_per_changing_call_of_the_turn(tmp_path):
    config_path, config = _config_file(tmp_path)
    turn = str(uuid.uuid4())
    env = {"JARVIS_TURN": "1", "JARVIS_TURN_ID": turn, "JARVIS_AGENT_CONFIG": str(config_path),
           "PATH": os.environ.get("PATH", "")}
    for payload in (
        {"tool_name": "Bash", "tool_input": {"command": "git push origin feature"},
         "cwd": "/srv/repo", "tool_response": {"exit_code": 0}},
        {"tool_name": "Read", "tool_input": {"file_path": "/srv/repo/a"}},
        {"tool_name": "Write", "tool_input": {"file_path": "/srv/repo/a", "content": "x"}},
    ):
        proc = _hook(ACTION_LOG, payload, env)
        assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")
    lines = [json.loads(line) for line in
             (config.actions_dir / f"{turn}.jsonl").read_text().splitlines()]
    assert [(line["tool"], line["what"], line["status"]) for line in lines] == [
        ("Bash", "git push origin feature", 0), ("Write", "/srv/repo/a", "ok")]


def test_the_hook_never_fails_a_call_and_never_blocks(tmp_path):
    config_path, config = _config_file(tmp_path)
    good = {"JARVIS_TURN": "1", "JARVIS_TURN_ID": str(uuid.uuid4()),
            "JARVIS_AGENT_CONFIG": str(config_path), "PATH": os.environ.get("PATH", "")}
    payload = {"tool_name": "Bash", "tool_input": {"command": "x"}}
    for env, raw in (
        (good, b"not json"), (good, b"[]"), (good, b""),
        (dict(good, JARVIS_TURN_ID="../../x"), None),
        (dict(good, JARVIS_AGENT_CONFIG=str(tmp_path / "nope.toml")), None),
        ({"JARVIS_TURN": "1", "PATH": os.environ.get("PATH", "")}, None),
    ):
        proc = _hook(ACTION_LOG, payload, env, raw=raw)
        assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")
    assert not config.actions_dir.exists()


def test_without_the_turn_marker_the_hook_logs_nothing(tmp_path):
    config_path, config = _config_file(tmp_path)
    env = {"JARVIS_TURN_ID": str(uuid.uuid4()), "JARVIS_AGENT_CONFIG": str(config_path),
           "PATH": os.environ.get("PATH", "")}
    proc = _hook(ACTION_LOG, {"tool_name": "Bash", "tool_input": {"command": "x"}}, env)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")
    assert not config.actions_dir.exists()
    source = ACTION_LOG.read_text()
    assert source.index('if "JARVIS_TURN" not in os.environ:') < source.index("import json")


# --- the three entry points as the installer places them ---------------------------------------------

def test_each_entry_point_runs_isolated_behind_a_symlink(tmp_path):
    """The installer (1.15) rewrites the shebang to `#!/usr/bin/python3 -I`
    and installs the entry points as symlinks: the script's directory is
    then not on sys.path and PYTHONPATH is ignored, so each one must find
    its package itself, through the symlink."""
    config_path, config = _config_file(tmp_path)
    bin_dir = tmp_path / "installed"
    bin_dir.mkdir()
    for script in (GATE, ACTION_LOG, CONFIRM):
        (bin_dir / script.name).symlink_to(script)
        # The hooks get their isolated shebang from the installer; the root
        # program has it in the repository already.
        assert script.read_text().splitlines()[0] == (
            "#!/usr/bin/python3 -I" if script == CONFIRM else "#!/usr/bin/env python3")
        assert os.access(script, os.X_OK)
    turn = str(uuid.uuid4())
    env = {"JARVIS_TURN": "1", "JARVIS_TURN_ID": turn, "JARVIS_AGENT_CONFIG": str(config_path),
           "JARVIS_TURN_CHECKOUT": str(tmp_path / "projects" / "demo"),
           "PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(tmp_path / "nowhere")}
    payload = {"tool_name": "Bash", "tool_input": {"command": "terraform apply"},
               "cwd": str(tmp_path / "projects" / "demo"), "tool_response": {"exit_code": 0}}

    gate = _hook(bin_dir / "gate", payload, env, isolated=True)
    assert gate.returncode == 0
    assert json.loads(gate.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "[needs_desk]" in gate.stdout.decode()

    log = _hook(bin_dir / "action-log", payload, env, isolated=True)
    assert (log.returncode, log.stdout, log.stderr) == (0, b"", b"")
    assert json.loads((config.actions_dir / f"{turn}.jsonl").read_text())["what"] == "terraform apply"

    # `confirm` with no argument: it found its package (a usage error is its
    # own message, not an import error) and it changed nothing.
    confirm = subprocess.run([sys.executable, "-I", str(bin_dir / "confirm")],
                             capture_output=True, env=env, timeout=30)
    assert confirm.returncode == 2
    assert confirm.stderr.decode().startswith("confirm: usage: confirm on|off [class]")


# --- retention ------------------------------------------------------------------------------------------

def test_prune_removes_what_is_older_than_the_retention_and_nothing_else(tmp_path):
    state = tmp_path / "state"
    now = time.time()
    kept, removed = [], []
    for name in ("records", "actions", "gate"):
        (state / name).mkdir(parents=True)
        for label, age_days in (("old", 91), ("edge", 89.9), ("new", 0)):
            path = state / name / f"{label}.json"
            path.write_text("{}")
            os.utime(path, (now - age_days * 86400, now - age_days * 86400))
            (removed if label == "old" else kept).append(path)
    # Session ids, the lock and a pending call are not records.
    (state / "sessions").mkdir()
    others = [state / "sessions" / "sessions.json", state / "turn.lock", state / "rotation.json"]
    (state / "pending").mkdir()
    others.append(state / "pending" / "demo.json")
    for path in others:
        path.write_text("{}")
        os.utime(path, (now - 400 * 86400, now - 400 * 86400))
    # A directory and a symlink inside records are left alone.
    (state / "records" / "sub").mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    os.utime(outside, (now - 400 * 86400, now - 400 * 86400))
    (state / "records" / "link.json").symlink_to(outside)

    result = record.prune(state, 90, now=now)

    assert sorted(result) == sorted(removed)
    assert all(not path.exists() for path in removed)
    assert all(path.exists() for path in kept + others)
    assert outside.exists() and (state / "records" / "sub").is_dir()
    assert record.prune(state, 90, now=now) == []


def test_prune_on_a_state_directory_that_is_not_there_does_nothing(tmp_path):
    assert record.prune(tmp_path / "nope", 90) == []
    with pytest.raises(ValueError):
        record.prune(tmp_path, 0)


def test_retention_is_configuration_with_a_default_of_90_days(tmp_path):
    assert make_config(tmp_path).retention_days == 90
    assert make_config(tmp_path, retention_days=30).retention_days == 30
    for bad in (0, -1, "90", 1.5, True):
        with pytest.raises(ConfigError):
            make_config(tmp_path, retention_days=bad)


# --- the configuration keys of this build -------------------------------------------------------------

def test_the_confirmation_keys_have_defaults_and_are_checked(tmp_path):
    config = make_config(tmp_path)
    assert config.confirm == "on" and config.confirm_skip == frozenset()
    assert config.notify_command == ()
    assert config.pr_status_command == ("gh", "pr", "view")
    assert str(config.confirm_program) == "/usr/local/lib/jarvis-agent/confirm"
    assert config.gate_dir == config.state_dir / "gate"
    assert config.pending_dir == config.state_dir / "pending"
    assert config.actions_dir == config.state_dir / "actions"
    set_ = make_config(tmp_path, confirm="off", confirm_skip=["message", "merge"],
                       notify_command=["ping.sh", "--to", "phone"],
                       pr_status_command=["/opt/gh", "pr", "view"], confirm_program="/opt/confirm")
    assert set_.confirm == "off" and set_.confirm_skip == {"message", "merge"}
    assert set_.notify_command == ("ping.sh", "--to", "phone")
    for bad in (
        {"confirm": "maybe"}, {"confirm": True}, {"confirm_skip": "message"},
        {"confirm_skip": ["self"]}, {"confirm_skip": ["nonsense"]},
        {"notify_command": "ping.sh hello"}, {"notify_command": [""]},
        {"pr_status_command": []}, {"pr_status_command": "gh pr view"},
    ):
        with pytest.raises(ConfigError):
            make_config(tmp_path, **bad)


# --- the confirm program ---------------------------------------------------------------------------------

CONFIG_TEXT = '''# the agent host
enabled = true
projects_root = "/srv/projects"   # where the checkouts are
state_dir = "/var/lib/jarvis"
confirm = "on"
confirm_skip = []

[projects_extra]
confirm = "this is another table's key"
'''


@pytest.mark.parametrize("argv,confirm,skip", [
    (["off"], "off", []),
    (["on"], "on", []),
    (["off", "message"], "on", ["message"]),
    (["on", "message"], "on", []),
])
def test_the_switch_changes_only_the_two_confirm_keys(tmp_path, argv, confirm, skip):
    path = tmp_path / "config.toml"
    path.write_text(CONFIG_TEXT)
    path.chmod(0o644)
    before = tomllib.loads(CONFIG_TEXT)

    switch.switch(argv, path)

    after = tomllib.loads(path.read_text())
    assert after["confirm"] == confirm and after["confirm_skip"] == skip
    assert {k: v for k, v in after.items() if k not in ("confirm", "confirm_skip")} \
        == {k: v for k, v in before.items() if k not in ("confirm", "confirm_skip")}
    assert after["projects_extra"] == {"confirm": "this is another table's key"}
    assert "# the agent host" in path.read_text() and "# where the checkouts are" in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o644
    assert not list(tmp_path.glob("*tmp*"))


def test_the_switch_per_class_adds_up_and_on_for_everything_clears_it(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('state_dir = "/x"\n')                   # no confirm keys yet
    switch.switch(["off", "message"], path)
    switch.switch(["off", "merge"], path)
    switch.switch(["off", "merge"], path)
    assert tomllib.loads(path.read_text()) == {
        "state_dir": "/x", "confirm": "on", "confirm_skip": ["merge", "message"]}
    switch.switch(["on", "merge"], path)
    assert tomllib.loads(path.read_text())["confirm_skip"] == ["message"]
    switch.switch(["off"], path)
    assert tomllib.loads(path.read_text()) == {
        "state_dir": "/x", "confirm": "off", "confirm_skip": ["message"]}
    switch.switch(["on"], path)
    assert tomllib.loads(path.read_text()) == {
        "state_dir": "/x", "confirm": "on", "confirm_skip": []}
    # The result is a configuration the wrapper accepts.
    assert tomllib.loads(path.read_text())["confirm"] in ("on", "off")


@pytest.mark.parametrize("argv", [
    [], ["maybe"], ["OFF"], ["off", "self"], ["off", "nonsense"], ["off", "merge", "deploy"],
    ["--off"], ["off", "--config", "/tmp/x"], ["off", ""], ["on", "off"], ["-h"],
])
def test_no_other_argument_form_exists(tmp_path, argv):
    path = tmp_path / "config.toml"
    path.write_text(CONFIG_TEXT)
    with pytest.raises(switch.SwitchError):
        switch.switch(argv, path)
    assert path.read_text() == CONFIG_TEXT


@pytest.mark.parametrize("text", [
    "this is [ not toml",
    'confirm = "sometimes"\n',
    'confirm_skip = "message"\n',
    'confirm_skip = [\n  "message",\n]\n',                  # spread over lines: a desk job
])
def test_a_configuration_the_switch_cannot_rewrite_safely_is_left_untouched(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    with pytest.raises(switch.SwitchError):
        switch.switch(["off"], path)
    assert path.read_text() == text


def test_a_missing_configuration_is_an_error(tmp_path):
    with pytest.raises(switch.SwitchError):
        switch.switch(["off"], tmp_path / "nope.toml")


def test_the_switched_configuration_is_what_the_gate_then_reads(tmp_path):
    from agent_host.config import load_config
    config = make_config(tmp_path)
    path = tmp_path / "etc" / "config.toml"
    path.write_text(
        f'projects_root = "{tmp_path / "projects"}"\nstate_dir = "{tmp_path / "state"}"\n'
        f'environment_file = "{config.environment_file}"\nturn_settings = "{config.turn_settings_path}"\n'
        f'prompt_file = "{config.prompt_path}"\nsession_identity = "jarvis"\n'
        f'user_settings_path = "{config.user_settings_path}"\n')
    assert load_config(path).confirm == "on"
    switch.switch(["off"], path)
    assert load_config(path).confirm == "off"
    switch.switch(["on"], path)
    switch.switch(["off", "message"], path)
    loaded = load_config(path)
    assert loaded.confirm == "on" and loaded.confirm_skip == {"message"}


def test_the_program_takes_its_file_from_nowhere_but_the_installation():
    source = CONFIRM.read_text()
    assert "os.environ" not in source and "JARVIS_AGENT_CONFIG" not in source
    assert "switch.switch(sys.argv[1:])" in source
    proc = subprocess.run([sys.executable, str(CONFIRM), "off", "--config", "/tmp/x"],
                          capture_output=True, timeout=30,
                          env={"PATH": os.environ.get("PATH", ""), "JARVIS_AGENT_CONFIG": "/tmp/x"})
    assert proc.returncode == 2 and proc.stdout == b""


# --- the second review of #44 ------------------------------------------------------------------

@pytest.mark.parametrize("secret", [
    "sk-ant-api03-abcDEF_123-xyz",
    "ghp_" + "a1B2c3D4e5" * 4,
    "github_pat_" + "11ABCDEFG0" * 4,
    "gho_" + "a1B2c3D4e5" * 4,
    "ghs_" + "a1B2c3D4e5" * 4,
    "xoxb-123456789012-abcdefABCDEF",
    "xoxp-1234-5678-abcd",
    "AKIAIOSFODNN7EXAMPLE",
    "ASIAIOSFODNN7EXAMPLE",
])
def test_tokens_of_the_usual_kinds_are_redacted(secret):
    line = record.action_line({"tool_name": "Bash", "tool_input": {
        "command": f"curl -H 'x-token: {secret}' https://example.invalid"}}, NOW)
    assert secret not in line["what"] and record.REDACTED in line["what"]
    assert secret not in record.redact(f"export TOKEN={secret}")


def test_bearer_tokens_and_url_passwords_are_redacted_and_ordinary_text_is_not():
    assert record.redact("curl -H 'Authorization: Bearer abc.DEF-123_xyz~+/=' x") \
        == "curl -H 'Authorization: Bearer [REDACTED]' x"
    assert record.redact("Authorization: token 0123456789abcdef") == "Authorization: token [REDACTED]"
    assert record.redact("git clone https://user:hunter2secret@example.invalid/x.git") \
        == "git clone https://user:[REDACTED]@example.invalid/x.git"
    for plain in ("git push origin main", "the bearer of news", "AKIA is a prefix",
                  "ghp_short", "https://example.invalid:8443/path@x"):
        assert record.redact(plain) == plain


def test_ran_reads_the_action_log_for_the_hash_of_one_call(tmp_path):
    from agent_host import gatehash
    directory = tmp_path / "actions"
    turn = str(uuid.uuid4())
    wanted = gatehash.call_hash("Bash", "/srv/repo", {"command": "gh pr merge 21"})
    assert record.ran(directory, turn, wanted) == "unknown"            # no log at all
    record.append_action(directory, turn, record.action_line(
        {"tool_name": "Bash", "tool_input": {"command": "git status -s"}, "cwd": "/srv/repo"}, NOW))
    assert record.ran(directory, turn, wanted) == "not run"
    record.append_action(directory, turn, record.action_line(
        {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 21"}, "cwd": "/srv/other"}, NOW))
    assert record.ran(directory, turn, wanted) == "not run"           # the same bytes, another directory
    record.append_action(directory, turn, record.action_line(
        {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 21", "description": "x"},
         "cwd": "/srv/repo", "tool_response": {"exit_code": 1}}, NOW))
    assert record.ran(directory, turn, wanted) == "ran"
    (directory / f"{turn}.jsonl").write_text("not json\n")
    assert record.ran(directory, turn, wanted) == "unknown"


def test_on_for_one_class_while_everything_is_off_switches_on_with_the_rest_skipped(tmp_path):
    from agent_host.config import CONFIRM_CLASSES
    path = tmp_path / "config.toml"
    path.write_text('state_dir = "/x"\nconfirm = "off"\nconfirm_skip = ["message"]\n')
    switch.switch(["on", "merge"], path)
    after = tomllib.loads(path.read_text())
    assert after["confirm"] == "on"
    assert set(after["confirm_skip"]) == CONFIRM_CLASSES - {"merge"}
    # From there the ordinary per-class switches apply.
    switch.switch(["on", "deploy"], path)
    assert set(tomllib.loads(path.read_text())["confirm_skip"]) == CONFIRM_CLASSES - {"merge", "deploy"}
    switch.switch(["on"], path)
    assert tomllib.loads(path.read_text())["confirm_skip"] == []
    assert "tool" in CONFIRM_CLASSES and "self" not in CONFIRM_CLASSES


def test_the_switch_keeps_the_owner_and_the_mode_of_the_file(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('state_dir = "/x"\n')
    path.chmod(0o640)
    chowned = []
    monkeypatch.setattr(switch.os, "chown", lambda p, uid, gid: chowned.append((uid, gid)))
    # As root under sudo: the file belongs to somebody else than the caller.
    monkeypatch.setattr(switch.os, "geteuid", lambda: 0)
    monkeypatch.setattr(switch.os, "getegid", lambda: 0)
    before = path.stat()
    switch.switch(["off"], path)
    assert chowned == [(before.st_uid, before.st_gid)]
    assert path.stat().st_mode & 0o777 == 0o640


def test_the_root_program_is_isolated_in_the_repository_already():
    assert CONFIRM.read_text().splitlines()[0] == "#!/usr/bin/python3 -I"
