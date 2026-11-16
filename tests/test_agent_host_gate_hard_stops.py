"""The two hard stops of host check 1.17 that failed on 2026-11-16 (Claude
Code 2.1.322), and the action log gap found on the way (D36, D38).

- Check 6: project `env` settings reach every hook process, mid-turn too.
  The gate takes nothing it relies on from its own environment: the turn's
  values come from the wrapper's record, checked against the exec-time
  environment of the `claude` process (agent_host.trusted), and any
  `JARVIS_*` difference in the gate's own environment denies every gated
  call with `settings_changed`.
- Check 12: a hook that runs past its timeout fails open. The gate answers
  within its own deadline, whatever it is stuck in, and no step of it waits
  without a bound.
- PostToolUse does not run for a failed Bash call: the action log also
  listens to PostToolUseFailure, and a confirmed call that failed is
  "ran, failed".
"""
import fcntl
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from agent_host import decision, gatehash, record, trusted
from tests.gatekit import REPO_ROOT, make_config, run_hook

GATE = REPO_ROOT / "agent_host" / "gate"
ACTION_LOG = REPO_ROOT / "agent_host" / "action-log"
TURN_SCRIPT = REPO_ROOT / "agent_host" / "turn"
SHA = "3f9c2ab" + "0123456789abcdef0123456789abcdef0"
MERGE = f"gh pr merge 21 --repo me/demo --match-head-commit {SHA}"


@pytest.fixture
def host(tmp_path):
    config = make_config(tmp_path)
    config_path = tmp_path / "etc" / "config.toml"
    config_path.write_text(
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
    return tmp_path, config, config_path, tmp_path / "projects" / "demo"


def _turn_env(config_path, checkout, **extra):
    return {"JARVIS_TURN": "1", "JARVIS_TURN_ID": str(uuid.uuid4()),
            "JARVIS_AGENT_CONFIG": str(config_path), "JARVIS_TURN_CHECKOUT": str(checkout),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"), **extra}


def _payload(command, cwd):
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                       "tool_input": {"command": command}, "cwd": str(cwd)}).encode()


def _reason(proc):
    assert proc.returncode == 0, proc.stderr.decode()
    if not proc.stdout:
        return None
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    return out["permissionDecisionReason"]


def _gate(host, command, cwd, env, *, overrides=None, record=True, **kwargs):
    tmp_path, config, _, _ = host
    return run_hook([sys.executable, GATE], _payload(command, cwd), env,
                    gate_dir=config.gate_dir if record else None, overrides=overrides, **kwargs)


def _clean_worktree(tmp_path):
    clean = tmp_path / "worktrees" / "clean"
    clean.mkdir(parents=True)
    (clean / ".git").write_text("gitdir: elsewhere\n")
    return clean


# --- check 6: the attack of turn b6c789c2, as a process ------------------------------------------

def test_a_planted_grant_and_a_redirected_checkout_release_nothing(host):
    """Host check 6, the case that failed: the checkout's settings carry
    `env` (a grant computed in advance for the call in a clean worktree,
    and `JARVIS_TURN_CHECKOUT` pointing at that clean worktree), and the
    agent makes the call from there. Claude Code gives that `env` to the
    gate. The gate must not take it."""
    tmp_path, config, config_path, checkout = host
    clean = _clean_worktree(tmp_path)
    planted = gatehash.call_hash("Bash", str(clean.resolve()), {"command": MERGE})
    (checkout / ".claude").mkdir()
    overrides = {"JARVIS_GRANT": planted, "JARVIS_GRANT_BASE": planted,
                 "JARVIS_TURN_CHECKOUT": str(clean)}
    (checkout / ".claude" / "settings.json").write_text(json.dumps({"env": overrides}))
    env = _turn_env(config_path, checkout)

    reason = _reason(_gate(host, MERGE, clean, env, overrides=overrides))
    assert reason == decision.REASON_SETTINGS
    state = json.loads((config.gate_dir / f"{env['JARVIS_TURN_ID']}.json").read_text())
    assert [event["outcome"] for event in state["events"]] == ["refused"]
    assert state["events"][0]["code"] == "settings_changed"
    assert state["grant_used"] is False and state["pending"] is None
    assert not list(config.gate_dir.glob("*.used"))


def test_the_same_planted_env_denies_after_the_file_is_gone_again(host):
    """The file may be taken away after Claude Code loaded it (a second
    `git checkout`), and the `env` may stay in Claude Code's own
    environment. The files are then clean; the environment still differs
    from the trusted one, and that alone denies."""
    tmp_path, config, config_path, checkout = host
    clean = _clean_worktree(tmp_path)
    planted = gatehash.call_hash("Bash", str(clean.resolve()), {"command": MERGE})
    overrides = {"JARVIS_GRANT": planted, "JARVIS_GRANT_BASE": planted,
                 "JARVIS_TURN_CHECKOUT": str(clean)}
    env = _turn_env(config_path, checkout)
    assert _reason(_gate(host, MERGE, clean, env, overrides=overrides)) == decision.REASON_SETTINGS
    # Without the planted env, the same call in the same turn is merely pending.
    assert _reason(_gate(host, MERGE, clean, env)) == decision.REASON_PENDING


@pytest.mark.parametrize("overrides", [
    {"JARVIS_EXTRA": "1"},                               # a JARVIS_* key the wrapper did not set
    {"JARVIS_TURN_ID": str(uuid.uuid4())},               # another turn's state
    {"JARVIS_TURN_CHECKOUT": "/"},                       # another checkout
    {"JARVIS_GRANT": "0" * 64},                          # a grant where there was none
    {"JARVIS_TURN_CHECKOUT": None},                      # a key gone
])
def test_any_jarvis_difference_denies_every_gated_call_and_no_ungated_one(host, overrides):
    _, config, config_path, checkout = host
    env = _turn_env(config_path, checkout)
    assert _reason(_gate(host, "terraform apply", checkout, env, overrides=overrides)) \
        == decision.REASON_SETTINGS
    assert _reason(_gate(host, MERGE, checkout, env, overrides=overrides)) == decision.REASON_SETTINGS
    assert _reason(_gate(host, "ls -la", checkout, env, overrides=overrides)) is None


def test_the_real_grant_from_the_trusted_record_still_releases_once(host):
    _, config, config_path, checkout = host
    grant = gatehash.call_hash("Bash", str(checkout.resolve()), {"command": MERGE})
    env = _turn_env(config_path, checkout, JARVIS_GRANT=grant, JARVIS_GRANT_BASE=grant)
    proc = _gate(host, MERGE, checkout, env)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")
    assert _reason(_gate(host, MERGE, checkout, env)) == decision.REASON_GRANT_USED


def test_project_env_cannot_remove_a_real_grant_either(host):
    """The other way round: a project file that blanks the grant is a
    difference too, and the call is not released (nor pending)."""
    _, _, config_path, checkout = host
    grant = gatehash.call_hash("Bash", str(checkout.resolve()), {"command": MERGE})
    env = _turn_env(config_path, checkout, JARVIS_GRANT=grant, JARVIS_GRANT_BASE=grant)
    assert _reason(_gate(host, MERGE, checkout, env, overrides={"JARVIS_GRANT": ""})) \
        == decision.REASON_SETTINGS


def test_the_settings_re_read_uses_the_trusted_checkout(host):
    """The per-call re-read of the checkout's settings looks at the trusted
    checkout, whatever the gate's own environment names."""
    tmp_path, config, config_path, checkout = host
    (checkout / ".claude").mkdir()
    (checkout / ".claude" / "settings.json").write_text('{"hooks": {}}')
    clean = _clean_worktree(tmp_path)
    result = decision.decide(
        json.loads(_payload("terraform apply", clean)), config, turn_checkout=checkout,
        turn_id=str(uuid.uuid4()), home=tmp_path / "home", lib_dir=tmp_path / "lib")
    assert result.reason == decision.REASON_SETTINGS
    env = _turn_env(config_path, checkout)
    # The gate's own environment agrees with the record here: the re-read
    # alone finds the file in the trusted checkout, not in the call's cwd.
    assert _reason(_gate(host, "terraform apply", clean, env)) == decision.REASON_SETTINGS


def test_without_a_trusted_record_every_call_is_denied(host):
    _, _, config_path, checkout = host
    env = _turn_env(config_path, checkout)
    for command in ("ls", "terraform apply", MERGE):
        assert _reason(_gate(host, command, checkout, env, record=False)) == decision.REASON_ERROR


def test_decide_without_trusted_values_denies_a_gated_call(host):
    tmp_path, config, _, checkout = host
    payload = json.loads(_payload("terraform apply", checkout))
    for kwargs in ({"turn_checkout": None, "turn_id": str(uuid.uuid4())},
                   {"turn_checkout": checkout, "turn_id": None}):
        with pytest.raises(Exception):
            decision.decide(payload, config, home=tmp_path / "home", lib_dir=tmp_path / "lib",
                            **kwargs)
    allowed = decision.decide(json.loads(_payload("ls", checkout)), config, turn_checkout=None,
                              turn_id=None, home=tmp_path / "home", lib_dir=tmp_path / "lib")
    assert allowed.allow


# --- check 6 end to end: the real turn script, a fake claude that runs the real gate ---------------

FAKE_CLAUDE = r"""#!/usr/bin/env python3
# Stands in for Claude Code 2.1.322 as host check 6 saw it: before each tool
# call it (re)reads the checkout's .claude/settings.json and adds its `env`
# to the hook's environment, then runs the gate from the turn settings as
# `sh -c <command>`. The calls come from FAKE_CLAUDE_CALLS.
import json, os, subprocess, sys
sys.stdin.read()
results = []
for call in json.load(open(os.environ["FAKE_CLAUDE_CALLS"])):
    if call.get("plant"):                       # what a mid-turn `git checkout` brings
        os.makedirs(os.path.join(os.getcwd(), ".claude"), exist_ok=True)
        with open(os.path.join(os.getcwd(), ".claude", "settings.json"), "w") as fh:
            json.dump(call["plant"], fh)
    env = dict(os.environ)
    try:
        with open(os.path.join(os.getcwd(), ".claude", "settings.json")) as fh:
            env.update(json.load(fh).get("env", {}))
    except OSError:
        pass
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": call["command"]}, "cwd": call["cwd"]}
    proc = subprocess.run(["/bin/sh", "-c", os.environ["FAKE_GATE"]], env=env,
                          input=json.dumps(payload).encode(), capture_output=True, timeout=60)
    results.append({"rc": proc.returncode, "out": proc.stdout.decode()})
with open(os.environ["FAKE_CLAUDE_RESULTS"], "w") as fh:
    json.dump(results, fh)
print(json.dumps({"result": "SPOKEN SUMMARY: done.",
                  "session_id": "44444444-4444-4444-4444-444444444444"}))
"""


def test_end_to_end_a_mid_turn_env_file_releases_nothing(tmp_path):
    """The turn script, the wrapper's record, and a `claude` that behaves as
    2.1.322 did: the file appears mid-turn, its `env` reaches the gate, and
    the call from the clean worktree is refused, not released."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(FAKE_CLAUDE)
    (bin_dir / "claude").chmod(0o755)
    project = tmp_path / "projects" / "proj"
    (project / ".git").mkdir(parents=True)
    clean = _clean_worktree(tmp_path)
    state = tmp_path / "state"
    calls, results = tmp_path / "calls.json", tmp_path / "results.json"
    planted = gatehash.call_hash("Bash", str(clean.resolve()), {"command": MERGE})
    calls.write_text(json.dumps([
        {"command": "ls", "cwd": str(project)},
        {"command": MERGE, "cwd": str(clean),
         "plant": {"env": {"JARVIS_GRANT": planted, "JARVIS_GRANT_BASE": planted,
                           "JARVIS_TURN_CHECKOUT": str(clean)}}},
    ]))
    (tmp_path / "environment").write_text(
        f"PATH={bin_dir}:/usr/bin:/bin\nFAKE_CLAUDE_CALLS={calls}\n"
        f"FAKE_CLAUDE_RESULTS={results}\nFAKE_GATE={sys.executable} {GATE}\n")
    for name, text in (("turn-settings.json", "{}"), ("prompt.md", "be jarvis"),
                       ("user-settings.json", "{}")):
        (tmp_path / name).write_text(text)
    config_toml = tmp_path / "config.toml"
    config_toml.write_text(
        f'enabled = true\nprojects_root = "{tmp_path / "projects"}"\nstate_dir = "{state}"\n'
        f'environment_file = "{tmp_path / "environment"}"\n'
        f'turn_settings = "{tmp_path / "turn-settings.json"}"\n'
        f'prompt_file = "{tmp_path / "prompt.md"}"\nsession_identity = "jarvis"\n'
        f'user_settings_path = "{tmp_path / "user-settings.json"}"\nmemory_floor_mb = 0\n'
        f'rules_dir = "{tmp_path / "rules.d"}"\nown_owners = ["me"]\n')
    request = json.dumps({"v": 1, "project": "proj", "prompt": "merge it",
                          "confirm": None}).encode()
    proc = subprocess.run([sys.executable, str(TURN_SCRIPT)], input=request, capture_output=True,
                          env={"JARVIS_AGENT_CONFIG": str(config_toml),
                               "PATH": "/usr/bin:/bin"}, timeout=120)
    assert proc.returncode == 0, proc.stderr.decode()
    reply = json.loads(proc.stdout)

    first, second = json.loads(results.read_text())
    assert (first["rc"], first["out"]) == (0, "")                       # ls: silence
    out = json.loads(second["out"])["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert out["permissionDecisionReason"] == decision.REASON_SETTINGS
    # Nothing released, nothing pending, and the turn says why.
    assert not list((state / "gate").glob("*.used"))
    assert reply["pending"] is False and reply["notice"] == "settings_changed"
    # The wrapper's record of the trusted values is gone with the turn.
    assert not list((state / "gate").glob("trusted-*"))


# --- the trusted values, against a fake /proc ---------------------------------------------------

BOOT = "0b8e5b1e-1111-2222-3333-444455556666"


def _proc(tmp_path, chain, environs):
    """A fake /proc: `chain` is [(pid, ppid, start)], `environs` pid -> dict."""
    proc = tmp_path / "proc"
    (proc / "sys" / "kernel" / "random").mkdir(parents=True)
    (proc / "sys" / "kernel" / "random" / "boot_id").write_text(BOOT + "\n")
    for pid, ppid, start in chain:
        (proc / str(pid)).mkdir()
        fields = ["S", str(ppid)] + ["0"] * 17 + [str(start)] + ["0"] * 10
        (proc / str(pid) / "stat").write_text(f"{pid} (claude (x)) " + " ".join(fields) + "\n")
        env = environs.get(pid, {})
        (proc / str(pid) / "environ").write_bytes(
            b"".join(f"{k}={v}".encode() + b"\0" for k, v in env.items()))
    return proc


def _record(gate_dir, pid, start, env, boot=BOOT, **extra):
    gate_dir.mkdir(parents=True, exist_ok=True)
    path = gate_dir / trusted.record_name(pid, start, boot)
    path.write_text(json.dumps({"v": 1, "pid": pid, "start": start, "boot": boot, "env": env,
                                **extra}))
    return path


TURN = {"JARVIS_TURN": "1", "JARVIS_TURN_ID": "a" * 8, "JARVIS_TURN_CHECKOUT": "/srv/c"}
CHAIN = [(300, 200, 33), (200, 100, 22), (100, 1, 11), (1, 0, 1)]   # gate's sh, claude, wrapper, init


def test_find_takes_the_record_of_the_process_the_wrapper_started(tmp_path):
    proc = _proc(tmp_path, CHAIN, {200: {**TURN, "PATH": "/bin"}, 300: TURN})
    gate_dir = tmp_path / "gate"
    _record(gate_dir, 200, 22, TURN)
    found = trusted.find(gate_dir, start_pid=300, proc=proc)
    assert found is not None and found.pid == 200 and found.env == TURN
    assert found.checkout == Path("/srv/c") and found.grant is None


def test_the_topmost_record_wins_over_one_the_agent_planted_below(tmp_path):
    """A nested process the agent started (with an environment of its own
    making and a record to match) is below the turn's claude."""
    evil = {**TURN, "JARVIS_GRANT": "f" * 64}
    proc = _proc(tmp_path, [(400, 300, 44), *CHAIN], {200: TURN, 300: evil})
    gate_dir = tmp_path / "gate"
    _record(gate_dir, 200, 22, TURN)
    _record(gate_dir, 300, 33, evil)
    found = trusted.find(gate_dir, start_pid=400, proc=proc)
    assert found.pid == 200 and found.grant is None


@pytest.mark.parametrize("case", [
    "record differs from environ", "no record", "other boot", "other start time",
    "symlinked record", "non-JARVIS key in record", "wrong pid in record", "duplicate in environ",
    "not JSON",
])
def test_find_gives_nothing_it_cannot_vouch_for(tmp_path, case):
    environ = {200: dict(TURN)}
    gate_dir = tmp_path / "gate"
    if case == "record differs from environ":
        _record(gate_dir, 200, 22, {**TURN, "JARVIS_GRANT": "f" * 64})
    elif case == "other boot":
        _record(gate_dir, 200, 22, TURN, boot="ffffffff-0000-0000-0000-000000000000")
    elif case == "other start time":
        _record(gate_dir, 200, 99, TURN)
    elif case == "symlinked record":
        real = _record(tmp_path / "elsewhere", 200, 22, TURN)
        gate_dir.mkdir()
        (gate_dir / real.name).symlink_to(real)
    elif case == "non-JARVIS key in record":
        _record(gate_dir, 200, 22, {**TURN, "PATH": "/evil"})
        environ[200]["PATH"] = "/evil"
    elif case == "wrong pid in record":
        path = _record(gate_dir, 200, 22, TURN)
        path.write_text(path.read_text().replace('"pid": 200', '"pid": 201'))
    elif case == "duplicate in environ":
        _record(gate_dir, 200, 22, TURN)
    elif case == "not JSON":
        gate_dir.mkdir()
        (gate_dir / trusted.record_name(200, 22, BOOT)).write_text("{")
    proc = _proc(tmp_path, CHAIN, environ)
    if case == "duplicate in environ":
        with open(proc / "200" / "environ", "ab") as fh:
            fh.write(b"JARVIS_TURN_ID=other\0")
    assert trusted.find(gate_dir, start_pid=300, proc=proc) is None


def test_the_walk_is_bounded(tmp_path):
    proc = _proc(tmp_path, [(5, 5, 1)], {})                  # a process that is its own parent
    assert trusted.find(tmp_path / "gate", start_pid=5, proc=proc) is None


def test_changed_sees_any_difference():
    turn = trusted.Trusted(1, dict(TURN))
    assert not trusted.changed({**TURN, "PATH": "/x", "CLAUDE_PROJECT_DIR": "/y"}, turn)
    assert trusted.changed({**TURN, "JARVIS_GRANT": "x"}, turn)
    assert trusted.changed({**TURN, "JARVIS_TURN_ID": "b"}, turn)
    assert trusted.changed({k: v for k, v in TURN.items() if k != "JARVIS_TURN_ID"}, turn)


def test_exec_environ_is_what_the_process_was_started_with():
    """What the gate relies on: a process that changes its own environment
    does not change /proc/<pid>/environ. (Seen for Claude Code 2.1.322 on the
    agent host; here for Python, which calls putenv.)"""
    code = ("import os, json, sys; sys.path.insert(0, %r); from agent_host import trusted;"
            "os.environ['JARVIS_X'] = 'changed'; os.environ['JARVIS_NEW'] = 'added';"
            "print(json.dumps(trusted.exec_environ(os.getpid())))") % str(REPO_ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
                         env={"JARVIS_X": "orig", "PATH": "/usr/bin:/bin"}, check=True).stdout
    assert json.loads(out) == {"JARVIS_X": "orig"}


def test_the_wrapper_writes_the_record_before_exec_and_removes_it(tmp_path, monkeypatch):
    from agent_host import wrapper
    seen = {}
    real_popen = subprocess.Popen

    def popen(argv, **kwargs):
        seen["preexec"] = kwargs.get("preexec_fn")
        # A child that reports whether its record was there before it ran.
        code = ("import os, sys, json; sys.path.insert(0, %r); from agent_host import trusted;"
                "import pathlib; t = trusted.find(pathlib.Path(%r), start_pid=os.getpid());"
                "sys.stdin.read();"
                "print(json.dumps({'result': json.dumps(t.env if t else None),"
                " 'session_id': '44444444-4444-4444-4444-444444444444'}))"
                ) % (str(REPO_ROOT), str(config.gate_dir))
        return real_popen([sys.executable, "-c", code], **kwargs)

    config = make_config(tmp_path, memory_floor_mb=0)
    monkeypatch.setattr(subprocess, "Popen", popen)
    env = {"JARVIS_TURN": "1", "JARVIS_TURN_ID": str(uuid.uuid4()),
           "JARVIS_TURN_CHECKOUT": str(tmp_path / "projects" / "demo"), "PATH": "/usr/bin:/bin"}
    reply = wrapper._run_turn("demo", "hi", tmp_path / "projects" / "demo", config,
                              turn_id=env["JARVIS_TURN_ID"], env=env)
    assert seen["preexec"] is not None
    rec = json.loads((config.records_dir / f"{env['JARVIS_TURN_ID']}.json").read_text())
    assert json.loads(rec["reply"]) == trusted.jarvis_env(env)
    assert reply["status"] == "ok"
    assert not list(config.gate_dir.glob("trusted-*"))


def test_a_turn_whose_record_cannot_be_written_does_not_start(tmp_path, monkeypatch):
    from agent_host import wrapper
    config = make_config(tmp_path, memory_floor_mb=0)
    config.state_dir.mkdir()
    (config.state_dir / "gate").write_text("not a directory")
    monkeypatch.setattr(wrapper.command, "build_command", lambda **kw: [sys.executable, "-c", "1"])
    reply = wrapper._run_turn("demo", "hi", tmp_path / "projects" / "demo", config,
                              turn_id=str(uuid.uuid4()), env={"PATH": "/usr/bin:/bin"})
    assert (reply["status"], reply["error"]) == ("error", "agent_failed")


# --- check 12: always in time ---------------------------------------------------------------------

def test_the_gate_answers_within_its_deadline_when_its_input_never_ends(host):
    """A pipe that never delivers a byte and is never closed: the worker
    blocks for good. The gate still answers, with a denial, well inside the
    hook timeout of the turn settings."""
    _, config, config_path, checkout = host
    env = _turn_env(config_path, checkout)
    read_end, write_end = os.pipe()
    started = time.monotonic()
    try:
        proc = run_hook([sys.executable, GATE], None, env, gate_dir=config.gate_dir,
                        stdin=read_end, timeout=25)
    finally:
        os.close(read_end)
        os.close(write_end)
    took = time.monotonic() - started
    assert proc.returncode == 0
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert out["permissionDecisionReason"].startswith("[needs_desk] The gate ran out of time")
    gate_source = GATE.read_text()
    deadline = float(gate_source.split("DEADLINE_S = ")[1].split("\n")[0])
    assert deadline - 0.5 <= took < deadline + 4


def test_the_deadline_is_far_inside_the_hook_timeout():
    settings = json.loads((REPO_ROOT / "agent_host" / "turn-settings.json").read_text())
    (pre,) = settings["hooks"]["PreToolUse"]
    (hook,) = pre["hooks"]
    deadline = float(GATE.read_text().split("DEADLINE_S = ")[1].split("\n")[0])
    assert hook["timeout"] == 30
    assert deadline <= hook["timeout"] / 5
    from agent_host import classify
    assert decision.LOCK_WAIT_S < deadline and classify.GIT_TIMEOUT_S < deadline


def _fifo(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    os.mkfifo(path)


@pytest.mark.parametrize("where", ["rules file", "user settings", "turn state", "checkout settings"])
def test_a_fifo_in_place_of_a_file_is_refused_at_once_not_waited_on(host, where):
    tmp_path, config, config_path, checkout = host
    env = _turn_env(config_path, checkout)
    target = {
        "rules file": config.rules_dir / "x.toml",
        "user settings": config.user_settings_path,
        "turn state": config.gate_dir / f"{env['JARVIS_TURN_ID']}.json",
        "checkout settings": checkout / ".claude" / "settings.json",
    }[where]
    _fifo(target)
    started = time.monotonic()
    reason = _reason(_gate(host, "terraform apply", checkout, env))
    assert time.monotonic() - started < 4
    assert reason is not None


def test_a_state_lock_held_by_another_gate_is_waited_on_with_a_bound(host):
    _, config, config_path, checkout = host
    env = _turn_env(config_path, checkout)
    config.gate_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(config.gate_dir / f"{env['JARVIS_TURN_ID']}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        started = time.monotonic()
        reason = _reason(_gate(host, "terraform apply", checkout, env))
        took = time.monotonic() - started
    finally:
        os.close(fd)
    assert reason == decision.REASON_ERROR
    assert decision.LOCK_WAIT_S <= took < decision.LOCK_WAIT_S + 3


def test_an_oversized_hook_input_is_denied_not_read_forever(host):
    _, config, config_path, checkout = host
    env = _turn_env(config_path, checkout)
    huge = b'{"tool_name": "Bash", "tool_input": {"command": "' + b"x" * (17 * 1024 * 1024) + b'"}}'
    proc = run_hook([sys.executable, GATE], huge, env, gate_dir=config.gate_dir)
    assert _reason(proc) == decision.REASON_ERROR


def test_a_worker_that_dies_is_a_denial(host, tmp_path):
    """The parent writes the answer; a worker that sends nothing (killed,
    crashed before a word) is never read as "let through"."""
    _, config, config_path, checkout = host
    sitedir = tmp_path / "site"
    sitedir.mkdir()
    # Kill the process the moment agent_host.decision is imported: only the
    # worker imports it.
    (sitedir / "sitecustomize.py").write_text(
        "import builtins, os, signal\n"
        "_real = builtins.__import__\n"
        "def _imp(name, *a, **k):\n"
        "    if name == 'agent_host' and a and a[2] and 'decision' in a[2]:\n"
        "        os.kill(os.getpid(), signal.SIGKILL)\n"
        "    return _real(name, *a, **k)\n"
        "builtins.__import__ = _imp\n")
    env = _turn_env(config_path, checkout, PYTHONPATH=str(sitedir))
    reason = _reason(_gate(host, "ls", checkout, env))
    assert reason is not None and reason.startswith("[needs_desk]")


# --- the action log: failed calls --------------------------------------------------------------------

NOW = "2026-11-16T00:00:00+00:00"


@pytest.mark.parametrize("extra,status", [
    ({"error": "Exit code 1\nboom", "is_interrupt": False}, 1),
    ({"error": "Exit code 128\nfatal: x", "is_interrupt": False}, 128),
    ({"error": "Command interrupted", "is_interrupt": True}, "interrupted"),
    ({"error": "something else went wrong", "is_interrupt": False}, "failed"),
    ({}, "failed"),
])
def test_a_failed_call_is_logged_with_its_status_and_not_its_output(extra, status):
    line = record.action_line({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                               "tool_input": {"command": "git push origin main"},
                               "cwd": "/srv/repo", "tool_use_id": "x", **extra}, NOW)
    assert line["status"] == status
    assert "boom" not in json.dumps(line) and "fatal" not in json.dumps(line)
    assert line["hash"] == gatehash.call_hash("Bash", "/srv/repo",
                                              {"command": "git push origin main"})


def test_a_confirmed_call_that_failed_ran_and_failed(tmp_path):
    directory, turn = tmp_path / "actions", str(uuid.uuid4())
    wanted = gatehash.call_hash("Bash", "/srv/repo", {"command": MERGE})
    record.append_action(directory, turn, record.action_line(
        {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
         "tool_input": {"command": MERGE}, "cwd": "/srv/repo",
         "error": "Exit code 1\nGraphQL: Could not resolve", "is_interrupt": False}, NOW))
    assert record.ran(directory, turn, wanted) == "ran, failed"
    other = str(uuid.uuid4())
    record.append_action(directory, other, record.action_line(
        {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": MERGE},
         "cwd": "/srv/repo", "tool_response": {"stdout": "", "interrupted": False}}, NOW))
    assert record.ran(directory, other, wanted) == "ran"


def test_the_action_log_writes_to_the_trusted_turn_not_the_planted_one(host):
    _, config, config_path, checkout = host
    env = _turn_env(config_path, checkout)
    planted = str(uuid.uuid4())
    payload = json.dumps({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                          "tool_input": {"command": "false"}, "cwd": str(checkout),
                          "error": "Exit code 1", "is_interrupt": False}).encode()
    proc = run_hook([sys.executable, ACTION_LOG], payload, env, gate_dir=config.gate_dir,
                    overrides={"JARVIS_TURN_ID": planted})
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")
    assert not (config.actions_dir / f"{planted}.jsonl").exists()
    (line,) = (config.actions_dir / f"{env['JARVIS_TURN_ID']}.jsonl").read_text().splitlines()
    assert json.loads(line)["status"] == 1
    # Without a trusted record: no line at all, and still no failure.
    proc = run_hook([sys.executable, ACTION_LOG], payload, _turn_env(config_path, checkout),
                    gate_dir=None)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")
