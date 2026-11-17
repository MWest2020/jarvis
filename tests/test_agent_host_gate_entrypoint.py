"""The real agent_host/gate script, run as a process with a hook input on
stdin, and the decision behind it (tasks 1.7, 1.7b, 1.7d, 1.8a, 1.14a; D36).

An ungated call is let through in silence; a gated one is denied unless its
hash is the grant in the environment. The flow around a confirmation is in
test_agent_host_gate_confirm.py; here it is the process: its input, its
output, its environment, and failing closed.
"""
import json
import os
import subprocess
import sys
import uuid

import pytest

from agent_host import decision
from tests.gatekit import REPO_ROOT, make_config, run_hook

GATE = REPO_ROOT / "agent_host" / "gate"


@pytest.fixture
def setup(tmp_path):
    config = make_config(tmp_path)
    etc = tmp_path / "etc"
    (etc / "config.toml").write_text(
        f'projects_root = "{tmp_path / "projects"}"\n'
        f'state_dir = "{tmp_path / "state"}"\n'
        f'environment_file = "{config.environment_file}"\n'
        f'turn_settings = "{config.turn_settings_path}"\n'
        f'prompt_file = "{config.prompt_path}"\n'
        'session_identity = "jarvis"\n'
        f'user_settings_path = "{config.user_settings_path}"\n'
        f'rules_dir = "{config.rules_dir}"\n'
        f'managed_settings_path = "{config.managed_settings_path}"\n'
        'own_owners = ["me"]\n'
    )
    checkout = tmp_path / "projects" / "demo"
    return tmp_path, etc / "config.toml", checkout


def _payload(tool="Bash", tool_input=None, cwd=None, **extra):
    data = {
        "session_id": "abc", "transcript_path": "/x.jsonl", "hook_event_name": "PreToolUse",
        "permission_mode": "auto", "tool_name": tool,
        "tool_input": tool_input if tool_input is not None else {"command": "ls"},
        "cwd": str(cwd),
    }
    data.update(extra)
    return data


def _run(config_path, checkout, payload, *, env=None, raw=None, record=True, overrides=None):
    """The gate as Claude Code runs it in a turn the wrapper started with
    `env`: under a stand-in `claude` that wrote the trusted record (unless
    `record` is False). `overrides`: what project `env` settings add to the
    gate's own environment."""
    if env is None:
        # A new turn for every call, unless the test says otherwise: the
        # gate's state carries from call to call within one turn.
        env = {"JARVIS_TURN": "1", "JARVIS_TURN_ID": str(uuid.uuid4()),
               "JARVIS_AGENT_CONFIG": str(config_path), "JARVIS_TURN_CHECKOUT": str(checkout),
               "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    gate_dir = config_path.parent.parent / "state" / "gate" if record else None
    return run_hook(
        [sys.executable, GATE], raw if raw is not None else json.dumps(payload).encode(), env,
        gate_dir=gate_dir, overrides=overrides,
    )


def _denied(proc):
    """The reason of a denial, or None when the gate stayed silent."""
    assert proc.returncode == 0, proc.stderr.decode()
    if not proc.stdout:
        return None
    out = json.loads(proc.stdout)
    assert set(out) == {"hookSpecificOutput"}
    specific = out["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "deny"
    return specific["permissionDecisionReason"]


def _bash(setup, command, **kwargs):
    _, config_path, checkout = setup
    cwd = kwargs.pop("cwd", checkout)
    return _denied(_run(config_path, checkout, _payload(tool_input={"command": command}, cwd=cwd),
                        **kwargs))


# --- silence for what is not gated -------------------------------------------------------

@pytest.mark.parametrize("command", [
    "ls -la", "git status", "pytest -q", "gh pr view 5", "kubectl get pods", "bao read secret/x",
    "terraform version", "curl https://example.invalid", "git push origin feature",
    "gh pr create --repo me/demo --fill", "ls | wc -l",
])
def test_an_ungated_call_is_let_through_in_silence(setup, command):
    _, config_path, checkout = setup
    proc = _run(config_path, checkout, _payload(tool_input={"command": command}, cwd=checkout))
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""


def test_tools_that_are_not_gated_are_let_through(setup):
    _, config_path, checkout = setup
    for tool, tool_input in (
        ("Read", {"file_path": str(checkout / "x")}),
        ("Write", {"file_path": str(checkout / "src" / "x.py"), "content": "x"}),
        ("Edit", {"file_path": str(checkout / "x.py"), "old_string": "a", "new_string": "b"}),
        ("Grep", {"pattern": "x"}),
        ("mcp__x__get_thing", {"id": 1}),
        ("Agent", {"description": "d", "prompt": "x", "subagent_type": "Explore"}),
        ("SubagentHandback", {"message": "the report"}),
    ):
        assert _denied(_run(config_path, checkout, _payload(tool, tool_input, checkout))) is None


# --- a gated call without a grant is denied -----------------------------------------------------

@pytest.mark.parametrize("command", [
    "git push -f origin feature", "git push origin main", "gh api -X DELETE repos/me/demo",
    "gh secret set TOKEN", "gh gist create --public x", "argocd app sync web",
    "kubectl cordon node-1", "helm plugin install x", "bao kv put secret/x a=b",
    "terraform apply", "aws s3 rm s3://b/k", "curl -d a=b https://example.invalid",
    "wget --post-data a=b https://example.invalid", "sudo ls",
])
def test_a_gated_call_that_cannot_be_named_needs_the_desk(setup, command):
    reason = _bash(setup, command)
    assert reason == decision.REASON_DESK
    assert reason.startswith("[needs_desk]")
    assert "end your turn" in reason


@pytest.mark.parametrize("command", [
    "gh pr merge 21 --repo me/demo --match-head-commit 3f9c2ab3f9c2ab3f9c2ab3f9c2ab3f9c2ab3f9c2",
    "scp x web:/srv/", "bash -c 'gh repo delete me/demo'",
    "ssh jumpy kubectl delete pod web -n shop --context homelab",
])
def test_a_gated_call_that_can_be_named_is_denied_and_pending(setup, command):
    assert _bash(setup, command) == decision.REASON_PENDING


@pytest.mark.parametrize("command", ["gh issue delete 5", "ssh jumpy kubectl delete pod web"])
def test_a_gated_call_with_missing_explicit_targets_is_told_which(setup, command):
    reason = _bash(setup, command)
    assert reason.startswith("[missing]")
    assert "add -" in reason


def test_a_gated_connector_tool_is_denied(setup):
    _, config_path, checkout = setup
    for tool in ("mcp__x__send_thing", "mcp__x__post_comment"):
        assert _denied(_run(config_path, checkout, _payload(tool, {"to": "a"}, checkout))) \
            == decision.REASON_DESK


@pytest.mark.parametrize("command", [
    "bash -c 'gh pr merge 1; gh repo delete x'",
    "ssh jumpy 'gh pr merge 1; gh repo delete x'",
    "gh pr merge 1 --repo me/demo --subject 'a; b'",
    "gh pr merge 1 --repo me/demo && echo done",
    "xargs kubectl delete pod",
])
def test_a_gated_command_that_is_not_alone_is_refused_with_run_it_alone(setup, command):
    reason = _bash(setup, command)
    assert reason == decision.REASON_RUN_ALONE
    assert reason.startswith("[run_alone]")
    assert "Run it alone" in reason


def test_a_write_to_the_checkouts_own_settings_is_self_and_needs_the_desk(setup):
    _, config_path, checkout = setup
    target = checkout / ".claude" / "settings.local.json"
    for tool in ("Write", "Edit"):
        reason = _denied(_run(config_path, checkout,
                              _payload(tool, {"file_path": str(target), "content": "{}"}, checkout)))
        assert reason == decision.REASON_SELF
        assert reason.startswith("[needs_desk]")
    assert _bash(setup, "echo '{}' > .claude/settings.local.json") == decision.REASON_SELF


MERGE = "gh pr merge 21 --repo me/demo --match-head-commit 3f9c2ab3f9c2ab3f9c2ab3f9c2ab3f9c2ab3f9c2"


def _turn_env(config_path, checkout, **extra):
    return {"JARVIS_TURN": "1", "JARVIS_TURN_ID": str(uuid.uuid4()),
            "JARVIS_AGENT_CONFIG": str(config_path), "JARVIS_TURN_CHECKOUT": str(checkout),
            "PATH": os.environ.get("PATH", ""), **extra}


def test_the_grant_from_the_environment_releases_exactly_its_call_once(setup):
    from agent_host import gatehash
    _, config_path, checkout = setup
    grant = gatehash.call_hash("Bash", str(checkout.resolve()), {"command": MERGE})
    env = _turn_env(config_path, checkout, JARVIS_GRANT=grant, JARVIS_GRANT_BASE=grant)

    def run(command):
        return _run(config_path, checkout,
                    _payload(tool_input={"command": command}, cwd=checkout), env=env)

    # Another call first: the grant is not for it.
    assert _denied(run("gh repo delete me/demo")) is not None
    # Silence on a match: no output at all, so the other hooks still decide.
    proc = run(MERGE)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")
    # Used once.
    assert _denied(run(MERGE)) == decision.REASON_GRANT_USED


def test_a_wrong_grant_releases_nothing(setup):
    _, config_path, checkout = setup
    for grant in ("0" * 64, "yes", ""):
        env = _turn_env(config_path, checkout, JARVIS_GRANT=grant)
        proc = _run(config_path, checkout, _payload(tool_input={"command": MERGE}, cwd=checkout),
                    env=env)
        assert _denied(proc) == decision.REASON_PENDING


def test_one_pending_call_per_turn_through_the_real_process(setup):
    tmp_path, config_path, checkout = setup
    env = _turn_env(config_path, checkout)

    def run(command):
        return _denied(_run(config_path, checkout,
                            _payload(tool_input={"command": command}, cwd=checkout), env=env))

    assert run(MERGE) == decision.REASON_PENDING
    assert run("gh repo delete me/demo") == decision.REASON_ONE_AT_A_TIME
    state = json.loads((tmp_path / "state" / "gate" / f"{env['JARVIS_TURN_ID']}.json").read_text())
    assert state["pending"] == {"tool": "Bash", "input": {"command": MERGE},
                                "cwd": str(checkout.resolve())}


def test_a_turn_id_the_wrapper_did_not_issue_denies(setup):
    _, config_path, checkout = setup
    for turn_id in ("t1", "../x", ""):
        env = _turn_env(config_path, checkout)
        env["JARVIS_TURN_ID"] = turn_id
        proc = _run(config_path, checkout, _payload(tool_input={"command": MERGE}, cwd=checkout),
                    env=env)
        assert _denied(proc) == decision.REASON_ERROR
    env = _turn_env(config_path, checkout)
    del env["JARVIS_TURN_ID"]
    proc = _run(config_path, checkout, _payload(tool_input={"command": MERGE}, cwd=checkout), env=env)
    assert _denied(proc) == decision.REASON_ERROR


# --- the checkout settings rule, on every call ------------------------------------------------

@pytest.mark.parametrize("key,value", [("env", {"A": "1"}), ("hooks", {}), ("disableAllHooks", True)])
@pytest.mark.parametrize("where", ["turn checkout", "worktree"])
def test_settings_that_gain_a_key_mid_turn_deny_every_gated_call(setup, key, value, where):
    tmp_path, config_path, checkout = setup
    worktree = tmp_path / "worktrees" / "feature"
    (worktree / "src").mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: elsewhere\n")
    cwd = worktree / "src"

    gated = "terraform apply"
    assert _bash(setup, gated, cwd=cwd) == decision.REASON_DESK

    # As a `git pull` or `gh pr checkout` would bring it: no Edit call involved.
    target_dir = (checkout if where == "turn checkout" else worktree) / ".claude"
    target_dir.mkdir()
    planted = target_dir / "settings.json"
    planted.write_text(json.dumps({key: value}))

    reason = _bash(setup, gated, cwd=cwd)
    assert reason == decision.REASON_SETTINGS
    assert reason.startswith("[settings_changed]")
    assert "Project settings changed, needs the desk" in reason
    # Also a compound one and a write to Jarvis's own rules: the settings come first.
    assert _bash(setup, gated + " && echo x", cwd=cwd) == decision.REASON_SETTINGS
    assert _bash(setup, "echo x >> ~/.bashrc", cwd=cwd) == decision.REASON_SETTINGS
    # An ungated call still runs.
    assert _bash(setup, "ls -la", cwd=cwd) is None
    assert _bash(setup, "git status", cwd=cwd) is None

    # Removing the key lifts the denial.
    planted.write_text("{}")
    assert _bash(setup, gated, cwd=cwd) == decision.REASON_DESK
    assert _bash(setup, "ls", cwd=cwd) is None


def test_an_unreadable_settings_file_denies_every_gated_call(setup):
    _, _, checkout = setup
    (checkout / ".claude").mkdir()
    (checkout / ".claude" / "settings.local.json").write_text("{ not json")
    assert _bash(setup, "gh pr merge 21 --repo me/demo") == decision.REASON_SETTINGS
    assert _bash(setup, "ls") is None


# --- failing closed --------------------------------------------------------------------------

def test_a_broken_rules_file_denies_even_an_ungated_call(setup):
    tmp_path, _, _ = setup
    rules_dir = tmp_path / "etc" / "rules.d"
    rules_dir.mkdir()
    (rules_dir / "broken.toml").write_text("this is [ not toml")
    assert _bash(setup, "ls") == decision.REASON_ERROR
    assert _bash(setup, "gh pr merge 1") == decision.REASON_ERROR
    (rules_dir / "broken.toml").write_text('[[actions]]\nclass = "nonsense"\nprograms = ["x"]\n')
    assert _bash(setup, "ls") == decision.REASON_ERROR


@pytest.mark.parametrize("raw", [
    b"", b"not json", b"[]", b'"x"', b"\xff\xfe",
    json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}).encode(),  # no cwd
    json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "relative"}).encode(),
    json.dumps({"tool_name": "Bash", "tool_input": "ls", "cwd": "/"}).encode(),
    json.dumps({"tool_name": "Bash", "tool_input": {"cmd": "ls"}, "cwd": "/"}).encode(),
    json.dumps({"tool_name": "", "tool_input": {}, "cwd": "/"}).encode(),
    json.dumps({"tool_input": {"command": "ls"}, "cwd": "/"}).encode(),
    json.dumps({"tool_name": "Write", "tool_input": {"content": "x"}, "cwd": "/"}).encode(),
])
def test_a_call_that_cannot_be_parsed_is_denied(setup, raw):
    _, config_path, checkout = setup
    assert _denied(_run(config_path, checkout, None, raw=raw)) == decision.REASON_ERROR


def test_a_missing_or_broken_configuration_denies(setup, tmp_path):
    _, config_path, checkout = setup
    payload = _payload(cwd=checkout)
    base = {"JARVIS_TURN": "1", "PATH": os.environ.get("PATH", "")}
    for env in (
        dict(base),  # neither variable
        dict(base, JARVIS_AGENT_CONFIG=str(config_path)),  # no turn checkout
        dict(base, JARVIS_TURN_CHECKOUT=str(checkout)),  # no configuration
        dict(base, JARVIS_AGENT_CONFIG=str(tmp_path / "nope.toml"), JARVIS_TURN_CHECKOUT=str(checkout)),
        dict(base, JARVIS_AGENT_CONFIG=str(config_path), JARVIS_TURN_CHECKOUT="relative/path"),
    ):
        reason = _denied(_run(config_path, checkout, payload, env=env))
        assert reason is not None and reason.startswith("[needs_desk]")
    config_path.write_text("not = [toml")
    assert _denied(_run(config_path, checkout, payload)) is not None


def test_an_empty_turn_marker_is_still_a_turn(setup):
    _, config_path, checkout = setup
    env = {"JARVIS_TURN": "", "JARVIS_TURN_ID": str(uuid.uuid4()),
           "JARVIS_AGENT_CONFIG": str(config_path),
           "JARVIS_TURN_CHECKOUT": str(checkout), "PATH": os.environ.get("PATH", "")}
    proc = _run(config_path, checkout,
                _payload(tool_input={"command": "terraform apply"}, cwd=checkout), env=env)
    assert _denied(proc) == decision.REASON_DESK


# --- the coding session is never gated ------------------------------------------------------

def test_without_the_turn_marker_the_gate_exits_0_and_reads_nothing(setup, tmp_path):
    _, config_path, checkout = setup
    payload = _payload(tool_input={"command": "gh repo delete me/demo"}, cwd=checkout)
    env = {"PATH": os.environ.get("PATH", ""), "JARVIS_AGENT_CONFIG": str(config_path),
           "JARVIS_TURN_CHECKOUT": str(checkout)}
    proc = _run(config_path, checkout, payload, env=env)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")

    # It reads nothing: not stdin (a pipe that never delivers a byte and is
    # never closed would hang a reader) ...
    read_end, write_end = os.pipe()
    try:
        proc = subprocess.run([sys.executable, str(GATE)], stdin=read_end, capture_output=True,
                              env=env, timeout=20)
    finally:
        os.close(read_end)
        os.close(write_end)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")

    # ... and not its configuration or rules: with both broken it still exits 0.
    config_path.write_text("not = [toml")
    proc = _run(config_path, checkout, payload, env={"PATH": os.environ.get("PATH", "")})
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")


def test_without_the_turn_marker_a_broken_installation_cannot_block(tmp_path):
    # A copy of the script alone, with no package next to it: every import fails.
    lonely = tmp_path / "broken" / "bin" / "gate"
    lonely.parent.mkdir(parents=True)
    lonely.write_text(GATE.read_text())
    payload = json.dumps(_payload(cwd=tmp_path)).encode()
    proc = subprocess.run([sys.executable, str(lonely)], input=payload, capture_output=True,
                          env={"PATH": os.environ.get("PATH", "")}, timeout=20)
    assert (proc.returncode, proc.stdout) == (0, b"")
    # The same broken installation inside a turn fails closed.
    proc = subprocess.run([sys.executable, str(lonely)], input=payload, capture_output=True,
                          env={"PATH": os.environ.get("PATH", ""), "JARVIS_TURN": "1"}, timeout=20)
    assert _denied(proc).startswith("[needs_desk]")


def test_the_gate_works_through_a_symlink_as_the_installer_places_it(setup, tmp_path):
    _, config_path, checkout = setup
    link = tmp_path / "installed" / "gate"
    link.parent.mkdir()
    link.symlink_to(GATE)
    env = {"JARVIS_TURN": "1", "JARVIS_TURN_ID": str(uuid.uuid4()),
           "JARVIS_AGENT_CONFIG": str(config_path),
           "JARVIS_TURN_CHECKOUT": str(checkout), "PATH": os.environ.get("PATH", "")}
    payload = json.dumps(_payload(tool_input={"command": "terraform apply"}, cwd=checkout))
    proc = run_hook([sys.executable, link], payload.encode(), env,
                    gate_dir=tmp_path / "state" / "gate")
    assert _denied(proc) == decision.REASON_DESK


def test_the_gate_runs_isolated_behind_a_symlink_as_installed(setup, tmp_path):
    """The installer (1.15) gives the entry points the shebang
    `#!/usr/bin/python3 -I` and places them as symlinks: the script's own
    directory is then not on sys.path and PYTHONPATH is ignored, so the
    script must find its package itself."""
    _, config_path, checkout = setup
    link = tmp_path / "isolated" / "gate"
    link.parent.mkdir()
    link.symlink_to(GATE)
    env = {"JARVIS_TURN": "1", "JARVIS_TURN_ID": str(uuid.uuid4()),
           "JARVIS_AGENT_CONFIG": str(config_path),
           "JARVIS_TURN_CHECKOUT": str(checkout), "PATH": os.environ.get("PATH", ""),
           "PYTHONPATH": str(tmp_path / "nowhere")}

    def run(command, run_env=env):
        payload = json.dumps(_payload(tool_input={"command": command}, cwd=checkout)).encode()
        return run_hook([sys.executable, "-I", link], payload, run_env,
                        gate_dir=tmp_path / "state" / "gate", cwd=tmp_path)

    assert _denied(run("terraform apply")) == decision.REASON_DESK
    proc = run("ls -la")
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")
    proc = run("gh repo delete me/demo", {"PATH": os.environ.get("PATH", "")})
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")


def test_the_script_is_executable_and_checks_the_marker_before_any_other_import():
    assert os.access(GATE, os.X_OK)
    assert GATE.read_text().splitlines()[0] == "#!/usr/bin/env python3"  # the installer rewrites it
    lines = [line for line in GATE.read_text().splitlines()
             if line.startswith(("import ", "from "))]
    assert lines[:3] == ["import os", "import signal", "import sys"]
    source = GATE.read_text()
    assert source.index('if "JARVIS_TURN" not in os.environ:') < source.index("import json")
    # The deadline comes first of all (host check 12): before the marker
    # check, before any other import.
    assert source.index("signal.setitimer(signal.ITIMER_REAL, DEADLINE_S)") \
        < source.index('if "JARVIS_TURN" not in os.environ:')


# --- the decision, without a process ----------------------------------------------------------

def test_decide_returns_the_verdict_it_refused_on(tmp_path):
    config = make_config(tmp_path)
    checkout = tmp_path / "projects" / "demo"
    result = decision.decide(
        _payload(tool_input={"command": "sudo kubectl delete pod web"}, cwd=checkout), config,
        turn_checkout=checkout, turn_id=str(uuid.uuid4()),
        home=tmp_path / "home", lib_dir=tmp_path / "lib",
    )
    assert not result.allow
    assert result.verdict.classes == ("root", "delete")
    allowed = decision.decide(_payload(cwd=checkout), config, turn_checkout=checkout,
                              turn_id=str(uuid.uuid4()),
                              home=tmp_path / "home", lib_dir=tmp_path / "lib")
    assert allowed.allow and allowed.reason == ""


def test_every_denial_names_a_notice_code_of_d33():
    from agent_host.wrapper import NOTICE_CODES
    for reason in (decision.REASON_SETTINGS, decision.REASON_RUN_ALONE, decision.REASON_SELF,
                   decision.REASON_DESK, decision.REASON_ERROR):
        code = reason[1:reason.index("]")]
        assert code in NOTICE_CODES
        assert "end your turn" in reason
