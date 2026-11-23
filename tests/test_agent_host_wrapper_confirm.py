"""The wrapper's side of a confirmation: the read-back, the pending call,
the confirming turn, the notify command (tasks 1.9, 1.10, 1.11, 1.14;
design D33, D36 "The flow").

The fake `claude` here is a `Popen` that does what the real one would: it
hands tool calls to the gate, with the environment the wrapper built for
the turn. The gate is the real decision code.
"""
import json
import os
import subprocess
import sys
import time

import pytest

from agent_host import classify, decision, gatehash, pending, record, wrapper
from agent_host.config import config_from_dict
from agent_host.wrapper import V1_FIELDS, handle_request
from tests.gatekit import SHA, git_init, run_git

REAL_POPEN = subprocess.Popen


def _only_claude(fake):
    """Replace `claude` and nothing else: git, the status command and the
    notify command are real processes (subprocess.run uses Popen too)."""
    def popen(command, *args, **kwargs):
        if isinstance(command, list) and command and command[0] == "claude":
            return fake(command, *args, **kwargs)
        return REAL_POPEN(command, *args, **kwargs)
    return popen


MERGE = f"gh pr merge 21 --repo me/jarvis --match-head-commit {SHA}"
MERGE_SENTENCE = "Merge pull request 21 on jarvis, at commit 3f9c2ab."
SESSION = "22222222-2222-2222-2222-222222222222"

STATUS_SCRIPT = """
import json, sys
mode = open({mode!r}).read().strip()
if mode == "fail":
    sys.exit(1)
if mode == "garbage":
    print("not json"); sys.exit(0)
head = {sha!r} if mode != "moved" else "f" * 40
checks = {{"green": [{{"status": "COMPLETED", "conclusion": "SUCCESS"}}, {{"state": "SUCCESS"}}],
          "failing": [{{"status": "COMPLETED", "conclusion": "FAILURE"}}],
          "running": [{{"status": "IN_PROGRESS", "conclusion": None}}],
          "none": []}}.get(mode, [{{"status": "COMPLETED", "conclusion": "SUCCESS"}}])
review = {{"noreview": "REVIEW_REQUIRED", "changes": "CHANGES_REQUESTED", "empty": ""}}.get(mode, "APPROVED")
with open({log!r}, "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
print(json.dumps({{"headRefOid": head, "reviewDecision": review, "statusCheckRollup": checks}}))
"""

NOTIFY_SCRIPT = """
import json, os, sys
with open({log!r}, "a") as fh:
    fh.write(json.dumps({{"argv": sys.argv[1:], "ident": os.environ.get("IDENT")}}) + "\\n")
"""


class Host:
    """An agent host in a temporary directory, with one real checkout."""

    def __init__(self, tmp_path, **overrides):
        self.tmp = tmp_path
        self.checkout = tmp_path / "projects" / "demo"
        git_init(self.checkout)
        run_git(self.checkout, "remote", "add", "origin", "git@github.com:me/jarvis.git")
        self.mode = tmp_path / "status-mode"
        self.mode.write_text("green")
        self.status_log = tmp_path / "status.log"
        self.notify_log = tmp_path / "notify.log"
        status = tmp_path / "pr-status.py"
        status.write_text(STATUS_SCRIPT.format(mode=str(self.mode), sha=SHA, log=str(self.status_log)))
        notify = tmp_path / "notify.py"
        notify.write_text(NOTIFY_SCRIPT.format(log=str(self.notify_log)))
        env_file = tmp_path / "environment"
        env_file.write_text("PATH=/usr/local/bin:/usr/bin:/bin\n")
        for name, content in (("turn-settings.json", "{}"), ("prompt.md", "be jarvis"),
                              ("user-settings.json", "{}")):
            (tmp_path / name).write_text(content)
        data = {
            "projects_root": str(tmp_path / "projects"),
            "state_dir": str(tmp_path / "state"),
            "environment_file": str(env_file),
            "turn_settings": str(tmp_path / "turn-settings.json"),
            "prompt_file": str(tmp_path / "prompt.md"),
            "session_identity": "jarvis",
            "user_settings_path": str(tmp_path / "user-settings.json"),
            "rules_dir": str(tmp_path / "rules.d"),
            "own_owners": ["me"],
            "pr_status_command": [sys.executable, str(status)],
            "notify_command": [sys.executable, str(notify)],
        }
        data.update(overrides)
        self.config = config_from_dict(data, config_path=tmp_path / "config.toml")
        self.popens = []
        self.action_log = True     # False: a host where the PostToolUse hook does not run

    # --- the fake claude ---------------------------------------------------------
    def agent(self, monkeypatch, calls, *, reply="done.\nSPOKEN SUMMARY: all done.",
              returncode=0, during=None):
        """A turn in which the agent makes `calls` (Bash command strings,
        (tool, input) pairs, or (tool, input, payload) triples whose payload
        fields are added to the hook input, as a call from inside a subagent
        carries its own `cwd` and agent fields), each through the real gate."""
        host = self

        class Popen:
            def __init__(self, command, **kwargs):
                self.command, self.kwargs = command, kwargs
                self.pid = 999999
                self.returncode = returncode
                self.decisions = []
                self.prompt = None
                host.popens.append(self)

            def communicate(self, input=None, timeout=None):  # noqa: A002
                self.prompt = input
                env = self.kwargs["env"]
                for call in calls:
                    if isinstance(call, str):
                        call = ("Bash", {"command": call})
                    tool, tool_input, *more = call
                    payload = {"tool_name": tool, "tool_input": tool_input,
                               "cwd": str(self.kwargs["cwd"]), "tool_response": {"exit_code": 0}}
                    payload.update(*more)
                    result = decision.decide(
                        payload, host.config, turn_checkout=host.checkout,
                        turn_id=env["JARVIS_TURN_ID"],
                        grant=env.get("JARVIS_GRANT"), grant_base=env.get("JARVIS_GRANT_BASE"),
                        git=classify.git_runner(env),
                    )
                    self.decisions.append(result)
                    if result.allow and host.action_log:
                        # A call that runs: the PostToolUse hook logs it.
                        line = record.action_line(payload, "2026-11-16T10:00:00+00:00")
                        if line is not None:
                            record.append_action(host.config.actions_dir, env["JARVIS_TURN_ID"], line)
                if during is not None:
                    during(self)
                return json.dumps({"result": reply, "session_id": SESSION}), "boom"

        monkeypatch.setattr(subprocess, "Popen", _only_claude(Popen))

    def ask(self, prompt="please merge it", confirm=None, project="demo"):
        raw = json.dumps({"v": 1, "project": project, "prompt": prompt, "confirm": confirm}).encode()
        return handle_request(raw, self.config)

    def answer(self, reply, answer="yes", **changes):
        confirm = {"id": reply["confirm"]["id"], "sha256": reply["confirm"]["sha256"], "answer": answer}
        confirm.update(changes)
        return self.ask(prompt="", confirm=confirm)

    def records(self):
        return [json.loads(p.read_text()) for p in sorted(
            self.config.records_dir.glob("*.json"), key=lambda p: p.stat().st_mtime_ns)]

    def pending_file(self):
        return self.config.pending_dir / "demo.json"

    def notified(self):
        if not self.notify_log.exists():
            return []
        return [json.loads(line) for line in self.notify_log.read_text().splitlines()]


@pytest.fixture
def host(tmp_path):
    return Host(tmp_path)


# --- the read-back ----------------------------------------------------------------------------

def test_a_gated_call_is_refused_and_read_back(host, monkeypatch):
    host.agent(monkeypatch, [MERGE], reply="I tried.\nSPOKEN SUMMARY: I will merge it now, say yes.")
    reply = host.ask()

    assert set(reply) == set(V1_FIELDS)
    assert reply["status"] == "ok" and reply["error"] is None
    assert reply["pending"] is True
    assert reply["readback"] == f"{MERGE_SENTENCE} Checks green, review approved. Say yes to go ahead."
    # The agent's own line is not sent at all, and the read-back is never the marker line.
    assert reply["spoken"] == ""
    assert reply["notice"] is None
    confirm = reply["confirm"]
    assert set(confirm) == {"id", "sha256", "class", "expires_s"}
    assert confirm["class"] == "merge"
    assert confirm["expires_s"] == 60
    assert confirm["sha256"] == gatehash.call_hash(
        "Bash", str(host.checkout.resolve()), {"command": MERGE})
    assert not host.popens[0].decisions[0].allow          # refused in the turn
    assert host.notified() == []                           # nothing ran, nobody is pinged

    stored = json.loads(host.pending_file().read_text())
    # The call, when it was issued, and the two hashes that were read back
    # (they release nothing: see agent_host.pending).
    assert set(stored) == {"id", "call", "issued_at", "sha256", "base_hash"}
    assert stored["sha256"] == confirm["sha256"]
    assert stored["call"] == {"tool": "Bash", "input": {"command": MERGE},
                              "cwd": str(host.checkout.resolve())}
    assert host.pending_file().stat().st_mode & 0o777 == 0o600
    record = host.records()[-1]
    assert record["gated_actions"][-1] == {"outcome": "read back", "readback": reply["readback"]}


def test_the_status_was_asked_for_that_pull_request_with_the_turns_environment(host, monkeypatch):
    host.agent(monkeypatch, [MERGE])
    host.ask()
    assert json.loads(host.status_log.read_text().splitlines()[0]) == [
        "21", "--repo", "me/jarvis", "--json", "reviewDecision,statusCheckRollup,headRefOid"]


@pytest.mark.parametrize("mode,status", [
    ("green", "Checks green, review approved."),
    ("failing", "Checks failing, review approved."),
    ("running", "Checks still running, review approved."),
    ("none", "No checks, review approved."),
    ("noreview", "Checks green, no review."),
    ("empty", "Checks green, no review."),
    ("changes", "Checks green, changes requested."),
    ("fail", "Status unknown."),
    ("garbage", "Status unknown."),
    ("moved", "The pull request has moved."),
])
def test_the_merge_read_back_says_how_the_pull_request_stands(host, monkeypatch, mode, status):
    host.mode.write_text(mode)
    host.agent(monkeypatch, [MERGE])
    reply = host.ask()
    assert reply["readback"] == f"{MERGE_SENTENCE} {status} Say yes to go ahead."
    if mode in ("fail", "garbage"):
        assert "green" not in reply["readback"]


def test_a_status_command_that_does_not_exist_says_unknown_never_green(tmp_path, monkeypatch):
    host = Host(tmp_path, pr_status_command=[str(tmp_path / "no-such-gh")])
    host.agent(monkeypatch, [MERGE])
    assert host.ask()["readback"] == f"{MERGE_SENTENCE} Status unknown. Say yes to go ahead."


def test_a_ref_with_a_slash_and_a_full_commit_are_spoken_as_built(host, monkeypatch):
    run_git(host.checkout, "checkout", "-q", "-b", "release/1.2")
    run_git(host.checkout, "commit", "-q", "--allow-empty", "-m", "two")
    run_git(host.checkout, "update-ref", "refs/remotes/origin/main", "HEAD~1")
    host.agent(monkeypatch, ["git push origin release/1.2:main"])
    reply = host.ask()
    assert reply["readback"] == ("Push release slash 1.2 as main to the repository jarvis."
                                 " 1 commit, no review. Say yes to go ahead.")
    assert reply["confirm"]["class"] == "merge"
    # A 40-hex commit: seven digits in the sentence, the full value in the hash.
    host.agent(monkeypatch, [MERGE])
    reply = host.ask()
    assert "3f9c2ab." in reply["readback"] and SHA not in reply["readback"]


def test_a_push_whose_commits_cannot_be_counted_says_so(host, monkeypatch):
    host.agent(monkeypatch, ["git push origin main"])
    assert host.ask()["readback"] == ("Push main to the repository jarvis."
                                      " An unknown number of commits, no review. Say yes to go ahead.")


def test_several_classes_are_all_named(host, monkeypatch):
    host.agent(monkeypatch, ["sudo kubectl delete pod web -n shop --context homelab --force"])
    reply = host.ask()
    assert reply["readback"] == ("As root: Force delete pod web in namespace shop on cluster homelab."
                                 " Say yes to go ahead.")
    assert reply["confirm"]["class"] == "root, delete, force"


def test_a_second_gated_call_in_one_turn_is_not_read_back(host, monkeypatch):
    host.agent(monkeypatch, [MERGE, "gh repo delete me/jarvis --yes"])
    reply = host.ask()
    assert reply["readback"].startswith(MERGE_SENTENCE)
    assert host.popens[0].decisions[1].reason == decision.REASON_ONE_AT_A_TIME


def test_a_refused_call_and_its_repair_end_in_one_read_back(host, monkeypatch):
    host.agent(monkeypatch, ["gh pr merge 21", MERGE])
    reply = host.ask()
    assert reply["pending"] is True
    assert reply["readback"].startswith(MERGE_SENTENCE)
    assert host.popens[0].decisions[0].reason.startswith("[missing]")


# --- nothing pending, and why --------------------------------------------------------------------

@pytest.mark.parametrize("calls,notice", [
    (["terraform apply"], "needs_desk"),
    (["echo x >> ~/.bashrc"], "needs_desk"),
    ([MERGE + " && echo done"], "run_alone"),
    (["bash -c 'gh pr merge 1; gh repo delete x'"], "run_alone"),
    (["gh pr merge 21", "gh pr merge 22 --repo me/jarvis --match-head-commit " + SHA], "needs_desk"),
    (["ls", "gh pr view 21"], None),
    (["gh pr merge 21"], None),          # refused with its missing parts; the agent says so itself
])
def test_a_turn_with_nothing_pending_carries_the_notice_of_its_refusal(host, monkeypatch, calls, notice):
    host.agent(monkeypatch, calls, reply="x\nSPOKEN SUMMARY: that was refused.")
    reply = host.ask()
    assert reply["pending"] is False and reply["readback"] is None and reply["confirm"] is None
    assert reply["notice"] == notice
    assert reply["spoken"] == "SPOKEN SUMMARY: that was refused."
    assert not host.pending_file().exists()


def test_of_several_refusals_the_most_important_notice_is_sent_not_the_last(host, monkeypatch):
    host.agent(monkeypatch, ["terraform apply", MERGE + " && echo done", "cd x && make"],
               reply="x\nSPOKEN SUMMARY: refused.")
    reply = host.ask()
    assert reply["notice"] == "needs_desk"                  # before run_alone, by the fixed order
    codes = [a.get("code") for a in host.records()[-1]["gated_actions"]]
    assert codes == ["needs_desk", "run_alone", None]


def test_a_call_the_gate_could_not_read_leaves_no_notice(host, monkeypatch):
    host.agent(monkeypatch, ["cd x && make", "make"], reply="x\nSPOKEN SUMMARY: built.")
    reply = host.ask()
    assert reply["notice"] is None and reply["pending"] is False
    assert reply["spoken"] == "SPOKEN SUMMARY: built."
    assert [d.allow for d in host.popens[-1].decisions] == [False, True]


def test_the_wrapper_and_the_gate_judge_by_the_same_surroundings(host, monkeypatch):
    """Neither takes a temp root or a home from an environment: the turn's
    TMPDIR is not where `rm -r` is free, for the gate or for the wrapper."""
    victim = host.tmp / "victim"
    victim.mkdir()
    (host.tmp / "environment").write_text(f"PATH=/usr/local/bin:/usr/bin:/bin\nTMPDIR={victim.parent}\n")
    monkeypatch.setenv("TMPDIR", str(victim.parent))
    monkeypatch.setattr(wrapper.decision, "build_context", wrapper.decision.build_context)
    from agent_host import rules as rules_mod
    rules = rules_mod.load_rules(host.config.rules_dir)
    ctx = decision.build_context(host.config, host.checkout)
    assert set(ctx.temp_roots) == {os.path.realpath(root) for root in rules.temp_roots}


def test_a_read_back_that_cannot_be_built_leaves_nothing_pending(host, monkeypatch):
    """The stored call is altered into one no template can say: there is no
    fallback sentence, nothing is pending, and the notice is needs_desk."""
    def alter(popen):
        path = host.config.gate_dir / f"{popen.kwargs['env']['JARVIS_TURN_ID']}.json"
        state = json.loads(path.read_text())
        state["pending"]["input"]["command"] = "gh pr merge 'twenty one' --repo me/jarvis"
        path.write_text(json.dumps(state))

    host.agent(monkeypatch, [MERGE], during=alter)
    reply = host.ask()
    assert reply["pending"] is False and reply["readback"] is None and reply["confirm"] is None
    assert reply["notice"] == "needs_desk"
    assert not host.pending_file().exists()
    assert host.records()[-1]["gated_actions"][-1]["why"] == "the read-back could not be built"


@pytest.mark.parametrize("planted", [
    None, "not a call", {"tool": "Bash"}, {"tool": "Bash", "input": "x", "cwd": "/"},
    {"tool": "Bash", "input": {"command": MERGE}, "cwd": "relative"},
    {"tool": "Bash", "input": {"cmd": MERGE}, "cwd": "/"},
    {"tool": "Bash", "input": {"command": "ls"}, "cwd": "/"},              # not gated at all
    {"tool": "Bash", "input": {"command": "echo x > ~/.bashrc"}, "cwd": "/"},  # self
])
def test_a_planted_pending_call_that_is_no_nameable_call_is_never_read_back(host, monkeypatch, planted):
    def plant(popen):
        path = host.config.gate_dir / f"{popen.kwargs['env']['JARVIS_TURN_ID']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pending": planted, "refused": None, "repair_used": False,
                                    "desk": False, "grant_used": False, "events": []}))

    host.agent(monkeypatch, [], during=plant)
    reply = host.ask()
    assert reply["pending"] is False and reply["readback"] is None
    assert not host.pending_file().exists()


def test_a_pending_file_altered_between_gate_and_wrapper_is_read_back_as_altered(host, monkeypatch):
    """What is heard and what a yes releases are always the same call: the
    read-back and the hash both follow the altered call."""
    other = "gh repo delete me/jarvis --yes"

    def alter(popen):
        path = host.config.gate_dir / f"{popen.kwargs['env']['JARVIS_TURN_ID']}.json"
        state = json.loads(path.read_text())
        state["pending"]["input"]["command"] = other
        path.write_text(json.dumps(state))

    host.agent(monkeypatch, [MERGE], during=alter)
    reply = host.ask()
    assert reply["readback"] == "Delete the repository jarvis. Say yes to go ahead."
    assert reply["confirm"]["class"] == "delete"
    assert reply["confirm"]["sha256"] == gatehash.call_hash(
        "Bash", str(host.checkout.resolve()), {"command": other})

    # The yes releases exactly that call, and not the merge the gate saw.
    host.agent(monkeypatch, [MERGE, other])
    host.answer(reply)
    decisions = host.popens[-1].decisions
    assert not decisions[0].allow and decisions[1].allow


def test_a_read_back_over_the_bridges_length_is_not_sent(host, monkeypatch):
    monkeypatch.setattr(wrapper, "MAX_READBACK_CHARS", 40)
    host.agent(monkeypatch, [MERGE])
    reply = host.ask()
    assert reply["pending"] is False and reply["notice"] == "needs_desk"
    assert not host.pending_file().exists()


def test_a_failed_turn_reads_nothing_back(host, monkeypatch):
    host.agent(monkeypatch, [MERGE], returncode=1)
    reply = host.ask()
    assert reply["error"] == "agent_failed"
    assert reply["pending"] is False and reply["readback"] is None
    assert not host.pending_file().exists()


def test_settings_that_appear_during_the_turn_drop_the_pending_call(host, monkeypatch):
    def plant(popen):
        target = host.checkout / ".claude" / "settings.json"
        target.parent.mkdir()
        target.write_text('{"env": {"JARVIS_GRANT": "x"}}')

    host.agent(monkeypatch, [MERGE], during=plant)
    reply = host.ask()
    assert reply["pending"] is False and reply["readback"] is None and reply["confirm"] is None
    assert reply["notice"] == "settings_changed"
    assert not host.pending_file().exists()
    record = host.records()[-1]
    assert record["settings_changed"] == "settings.json sets env"
    assert record["gated_actions"][-1] == {"outcome": "dropped", "why": "settings.json sets env"}


# --- the confirming turn ---------------------------------------------------------------------------

def test_a_yes_releases_exactly_the_pending_call_in_a_new_turn(host, monkeypatch):
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    host.agent(monkeypatch, [MERGE], reply="merged.\nSPOKEN SUMMARY: It is merged.")
    reply = host.answer(asked)

    assert reply["status"] == "ok"
    assert reply["spoken"] == "SPOKEN SUMMARY: It is merged."
    assert reply["pending"] is False and reply["readback"] is None and reply["confirm"] is None
    assert reply["turn"] is not None

    popen = host.popens[-1]
    # The grant is the hash the bridge sent, in the environment of the claude process.
    assert popen.kwargs["env"]["JARVIS_GRANT"] == asked["confirm"]["sha256"]
    assert popen.kwargs["cwd"] == host.checkout.resolve()
    # The fixed prompt: what was confirmed, the directory, the exact call.
    assert popen.prompt.startswith(
        f"The driver confirmed: {MERGE_SENTENCE} Run exactly this now,"
        f" in {host.checkout.resolve()}: {MERGE}\n")
    assert popen.decisions[0].allow and popen.decisions[0].reason == ""   # silence on a match

    # The wrapper, not the agent, tells Mark outside the voice channel.
    assert host.notified() == [{"argv": [MERGE_SENTENCE + " Outcome: ran."], "ident": "jarvis"}]
    record = host.records()[-1]
    assert record["prompt"] == popen.prompt
    assert record["gated_actions"][-1] == {
        "outcome": "confirmed", "ran": "ran", "readback": MERGE_SENTENCE, "notify": "sent"}
    assert not host.pending_file().exists()                 # single use


def test_an_ordinary_turn_never_has_a_grant(host, monkeypatch):
    host.agent(monkeypatch, ["ls"])
    host.ask()
    env = host.popens[-1].kwargs["env"]
    assert "JARVIS_GRANT" not in env and "JARVIS_GRANT_BASE" not in env
    monkeypatch.setenv("JARVIS_GRANT", "0" * 64)            # the wrapper's own environment
    host.ask()
    assert "JARVIS_GRANT" not in host.popens[-1].kwargs["env"]


def test_a_confirmed_call_the_agent_then_does_not_run_is_notified_as_not_run(host, monkeypatch):
    """The notify fires after every confirming turn that had a grant: a yes
    Mark did not give must be visible whatever the agent then did."""
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    host.agent(monkeypatch, ["git commit -m x"])             # something else ran, not the merge
    host.answer(asked)
    assert host.notified() == [{"argv": [MERGE_SENTENCE + " Outcome: not run."], "ident": "jarvis"}]
    assert host.records()[-1]["gated_actions"][-1] == {
        "outcome": "confirmed", "ran": "not run", "readback": MERGE_SENTENCE, "notify": "sent"}


def test_without_an_action_log_the_outcome_is_unknown_not_guessed(host, monkeypatch):
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    host.action_log = False
    host.agent(monkeypatch, [MERGE])
    host.answer(asked)
    assert host.notified()[-1]["argv"] == [MERGE_SENTENCE + " Outcome: unknown."]
    assert host.records()[-1]["gated_actions"][-1]["ran"] == "unknown"


def test_ran_is_decided_from_the_action_log_not_from_the_gates_state(host, monkeypatch):
    """The gate's state says the grant was used; the action log has no such
    call. The wrapper believes the log, which the hook writes after a call
    really ran, and not a flag in a file."""
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()

    def claim(popen):
        path = host.config.gate_dir / f"{popen.kwargs['env']['JARVIS_TURN_ID']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pending": None, "refused": None, "repair_used": False,
                                    "desk": False, "grant_used": True, "events": []}))

    host.agent(monkeypatch, ["git commit -m x"], during=claim)
    host.answer(asked)
    assert host.records()[-1]["gated_actions"][-1]["ran"] == "not run"
    # And the other way round: the state says nothing, the log has the call.
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()

    def forget(popen):
        (host.config.gate_dir / f"{popen.kwargs['env']['JARVIS_TURN_ID']}.json").unlink()

    host.agent(monkeypatch, [MERGE], during=forget)
    host.answer(asked)
    assert host.records()[-1]["gated_actions"][-1]["ran"] == "ran"


def test_a_different_call_after_the_yes_is_refused(host, monkeypatch):
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    host.agent(monkeypatch, [MERGE + " --squash", "terraform apply"])
    host.answer(asked)
    assert [d.allow for d in host.popens[-1].decisions] == [False, False]
    assert host.notified()[-1]["argv"] == [MERGE_SENTENCE + " Outcome: unknown."]


def test_the_grant_is_used_once(host, monkeypatch):
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    host.agent(monkeypatch, [MERGE, MERGE])
    reply = host.answer(asked)
    decisions = host.popens[-1].decisions
    assert decisions[0].allow and not decisions[1].allow
    assert decisions[1].reason == decision.REASON_GRANT_USED
    assert reply["notice"] == "needs_desk"
    assert len(host.notified()) == 1
    # And the same confirm sent again finds nothing.
    assert host.answer(asked)["error"] == "expired"


def test_without_a_notify_command_a_confirmed_call_is_still_recorded(tmp_path, monkeypatch):
    host = Host(tmp_path, notify_command=[])
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    host.agent(monkeypatch, [MERGE])
    host.answer(asked)
    assert host.records()[-1]["gated_actions"][-1]["notify"] == "not configured"


def test_a_notify_command_that_fails_does_not_fail_the_turn(tmp_path, monkeypatch):
    host = Host(tmp_path, notify_command=[str(tmp_path / "no-such-notifier")])
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    host.agent(monkeypatch, [MERGE])
    reply = host.answer(asked)
    assert reply["status"] == "ok"
    assert host.records()[-1]["gated_actions"][-1]["notify"] == "failed"


# --- anything but that yes ----------------------------------------------------------------------------

def _no_turn(host, monkeypatch):
    started = []
    monkeypatch.setattr(subprocess, "Popen", _only_claude(lambda *a, **k: started.append(1)))
    return started


def test_a_confirm_with_another_id_or_another_hash_is_expired_and_starts_no_turn(host, monkeypatch):
    for changes in ({"id": "someone-elses"}, {"sha256": "0" * 64},
                    {"sha256": gatehash.call_hash("Bash", str(host.checkout), {"command": "ls"})}):
        host.agent(monkeypatch, [MERGE])
        asked = host.ask()
        started = _no_turn(host, monkeypatch)
        reply = host.answer(asked, **changes)
        assert reply["status"] == "error" and reply["error"] == "expired"
        assert reply["turn"] is None
        assert not started
        # The attempt cleared the pending call: the right answer is now too late.
        assert not host.pending_file().exists()
        assert host.answer(asked)["error"] == "expired"
        assert not started


def test_an_expired_pending_call_is_expired(host, monkeypatch):
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    stored = json.loads(host.pending_file().read_text())
    stored["issued_at"] -= 61
    host.pending_file().write_text(json.dumps(stored))
    started = _no_turn(host, monkeypatch)
    reply = host.answer(asked)
    assert reply["error"] == "expired" and not started
    assert host.notified() == []


def test_a_late_confirm_is_expired_by_the_agent_hosts_own_clock(host, monkeypatch):
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    real = time.time
    monkeypatch.setattr(pending.time, "time", lambda: real() + 59)
    host.agent(monkeypatch, [MERGE])
    assert host.answer(asked)["status"] == "ok"             # at 59 seconds: in time

    monkeypatch.setattr(pending.time, "time", real)
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    monkeypatch.setattr(pending.time, "time", lambda: real() + 70)
    started = _no_turn(host, monkeypatch)
    reply = host.answer(asked)
    assert reply["error"] == "expired" and not started
    # A clock that went backwards is no reason to accept either.
    host.agent(monkeypatch, [MERGE])
    monkeypatch.setattr(pending.time, "time", real)
    asked = host.ask()
    monkeypatch.setattr(pending.time, "time", lambda: real() - 10)
    started = _no_turn(host, monkeypatch)
    assert host.answer(asked)["error"] == "expired" and not started


def test_a_pending_call_is_cleared_by_an_unrelated_request(host, monkeypatch):
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    assert host.pending_file().exists()
    host.agent(monkeypatch, ["ls"])
    host.ask("what time is it")                              # any request for the project
    assert not host.pending_file().exists()
    started = _no_turn(host, monkeypatch)
    assert host.answer(asked)["error"] == "expired"
    assert not started


def test_a_request_that_starts_no_turn_clears_it_too(host, monkeypatch):
    from agent_host.turnlock import acquire_turn_lock
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    with acquire_turn_lock(host.config.lock_path):
        assert host.ask("hello")["error"] == "busy"
    assert host.answer(asked)["error"] == "expired"


def test_a_yes_that_meets_a_busy_host_leaves_the_pending_call_as_it_is(host, monkeypatch):
    """The contract with the bridge: `busy` and `low_memory` answer a yes
    before the pending call is touched, so the bridge can ask again."""
    from agent_host.turnlock import acquire_turn_lock
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    before = host.pending_file().read_text()

    started = _no_turn(host, monkeypatch)
    with acquire_turn_lock(host.config.lock_path):
        reply = host.answer(asked)
    assert reply["error"] == "busy" and reply["turn"] is None
    assert host.pending_file().read_text() == before        # same id, same expiry

    monkeypatch.setattr(wrapper, "available_memory_mb", lambda: 1.0)
    reply = host.answer(asked)
    assert reply["error"] == "low_memory" and reply["turn"] is None
    assert host.pending_file().read_text() == before
    assert not started and host.notified() == []

    # Said again when the host is free: the same yes still releases the call.
    monkeypatch.setattr(wrapper, "available_memory_mb", lambda: 10_000.0)
    host.agent(monkeypatch, [MERGE])
    reply = host.answer(asked)
    assert reply["status"] == "ok" and host.popens[-1].decisions[0].allow
    assert len(host.notified()) == 1


def test_a_busy_host_does_not_keep_an_expired_or_foreign_yes_alive(host, monkeypatch):
    from agent_host.turnlock import acquire_turn_lock
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    stored = json.loads(host.pending_file().read_text())
    stored["issued_at"] -= 61
    host.pending_file().write_text(json.dumps(stored))
    with acquire_turn_lock(host.config.lock_path):
        assert host.answer(asked)["error"] == "busy"
    started = _no_turn(host, monkeypatch)
    assert host.answer(asked)["error"] == "expired"          # busy did not reset the clock
    assert not started


def test_a_no_is_never_held_up_by_a_busy_host(host, monkeypatch):
    from agent_host.turnlock import acquire_turn_lock
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    with acquire_turn_lock(host.config.lock_path):
        assert host.answer(asked, answer="no")["status"] == "ok"
    assert not host.pending_file().exists()


def test_a_request_for_another_project_leaves_it_alone(host, monkeypatch):
    other = host.tmp / "projects" / "other"
    git_init(other)
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    host.agent(monkeypatch, ["ls"])
    host.ask("hello", project="other")
    host.agent(monkeypatch, [MERGE])
    assert host.answer(asked)["status"] == "ok"
    # And a yes for another project releases nothing there.
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    started = _no_turn(host, monkeypatch)
    confirm = {"id": asked["confirm"]["id"], "sha256": asked["confirm"]["sha256"], "answer": "yes"}
    assert host.ask("", confirm=confirm, project="other")["error"] == "expired"
    assert not started


def test_an_exact_no_clears_the_pending_call_and_starts_no_turn(host, monkeypatch):
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    started = _no_turn(host, monkeypatch)
    reply = host.answer(asked, answer="no")
    assert reply == {"v": 1, "status": "ok", "error": None, "turn": None, "spoken": "",
                     "pending": False, "readback": None, "confirm": None, "notice": None}
    assert not started
    assert not host.pending_file().exists()
    assert host.records()[-1]["outcome"] == "cancelled"
    assert host.records()[-1]["gated_actions"] == [{"outcome": "cancelled", "readback": MERGE_SENTENCE}]
    # A yes after the no releases nothing.
    assert host.answer(asked)["error"] == "expired"
    assert not started and host.notified() == []


def test_a_no_with_nothing_pending_is_just_ok(host, monkeypatch):
    started = _no_turn(host, monkeypatch)
    reply = host.ask("", confirm={"id": "x", "sha256": "0" * 64, "answer": "no"})
    assert reply["status"] == "ok" and reply["turn"] is None
    assert not started
    assert not host.config.records_dir.exists()


def test_a_yes_with_nothing_pending_is_expired(host, monkeypatch):
    started = _no_turn(host, monkeypatch)
    reply = host.ask("", confirm={"id": "x", "sha256": "0" * 64, "answer": "yes"})
    assert reply["error"] == "expired" and not started


def test_a_pending_file_altered_after_the_read_back_does_not_get_the_bridges_yes(host, monkeypatch):
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    stored = json.loads(host.pending_file().read_text())
    stored["call"]["input"]["command"] = "gh repo delete me/jarvis --yes"
    host.pending_file().write_text(json.dumps(stored))
    started = _no_turn(host, monkeypatch)
    assert host.answer(asked)["error"] == "expired"          # the hash is another call's
    assert not started


def test_unsafe_checkout_settings_refuse_the_confirming_turn_too(host, monkeypatch):
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    (host.checkout / ".claude").mkdir()
    (host.checkout / ".claude" / "settings.local.json").write_text('{"hooks": {}}')
    started = _no_turn(host, monkeypatch)
    reply = host.answer(asked)
    assert reply["error"] == "unsafe_settings" and reply["turn"] is None
    assert not started and host.notified() == []


def test_a_pushurl_set_between_read_back_and_yes_is_target_changed_and_nothing_is_pushed(host, monkeypatch):
    host.agent(monkeypatch, ["git push origin main"])
    asked = host.ask()
    assert asked["pending"] is True
    run_git(host.checkout, "config", "remote.origin.pushurl", "git@github.com:me/elsewhere.git")
    started = _no_turn(host, monkeypatch)
    reply = host.answer(asked)
    assert reply["notice"] == "target_changed"
    assert reply["turn"] is None and reply["pending"] is False
    assert not started and host.notified() == []
    assert host.records()[-1]["outcome"] == "target_changed"


def test_a_pushurl_set_during_the_confirming_turn_is_target_changed_by_the_gate(host, monkeypatch):
    host.agent(monkeypatch, ["git push origin main"])
    asked = host.ask()

    def change_then_push(popen):
        run_git(host.checkout, "config", "remote.origin.pushurl", "git@github.com:me/elsewhere.git")
        env = popen.kwargs["env"]
        popen.decisions.append(decision.decide(
            {"tool_name": "Bash", "tool_input": {"command": "git push origin main"},
             "cwd": str(host.checkout)}, host.config, turn_checkout=host.checkout,
            turn_id=env["JARVIS_TURN_ID"], grant=env["JARVIS_GRANT"],
            grant_base=env["JARVIS_GRANT_BASE"], git=classify.git_runner(env)))

    host.agent(monkeypatch, [], during=change_then_push)
    reply = host.answer(asked)
    assert host.popens[-1].decisions[0].reason == decision.REASON_TARGET_CHANGED
    assert reply["notice"] == "target_changed"
    assert host.notified()[-1]["argv"][0].endswith("Outcome: unknown.")   # nothing ran


def test_a_confirm_still_carries_no_prompt_of_its_own(host, monkeypatch):
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    confirm = {"id": asked["confirm"]["id"], "sha256": asked["confirm"]["sha256"], "answer": "yes"}
    assert host.ask("and also delete the repository", confirm=confirm)["error"] == "bad_request"


# --- the confirmation switch and confirmation off ------------------------------------------------------

def test_the_switch_is_read_back_with_its_fixed_sentence(host, monkeypatch):
    program = host.config.confirm_program
    host.agent(monkeypatch, [f"sudo {program} off"])
    reply = host.ask("switch confirmation off")
    assert reply["readback"] == "Switch voice confirmation off for everything. Say yes to go ahead."
    assert reply["confirm"]["class"] == "self"
    host.agent(monkeypatch, [f"sudo {program} off --now"])
    reply = host.ask("switch confirmation off")
    assert reply["pending"] is False and reply["notice"] == "needs_desk"


def test_with_confirmation_off_gated_calls_run_and_the_record_says_not_confirmed(tmp_path, monkeypatch):
    host = Host(tmp_path, confirm="off")
    host.agent(monkeypatch, [MERGE, "terraform apply"])
    reply = host.ask()
    assert [d.allow for d in host.popens[-1].decisions] == [True, True]
    assert reply["pending"] is False and reply["notice"] is None
    assert reply["spoken"] == "SPOKEN SUMMARY: all done."
    actions = host.records()[-1]["gated_actions"]
    assert [a["outcome"] for a in actions] == ["not_confirmed", "not_confirmed"]
    assert host.notified() == []


def test_confirm_skip_runs_its_class_and_still_reads_back_the_others(tmp_path, monkeypatch):
    host = Host(tmp_path, confirm_skip=["delete"])
    host.agent(monkeypatch, ["kubectl delete pod web -n shop --context homelab", MERGE])
    reply = host.ask()
    assert [d.allow for d in host.popens[-1].decisions] == [True, False]
    assert reply["readback"].startswith(MERGE_SENTENCE)
    assert host.records()[-1]["gated_actions"][0]["outcome"] == "not_confirmed"


# --- the pieces ------------------------------------------------------------------------------------------

def test_the_confirming_prompt_for_a_tool_that_is_not_bash_names_the_tool_and_its_input():
    call = {"tool": "mcp__mail__send", "cwd": "/srv/repo",
            "input": {"to": "a@example.org", "description": "cosmetic", "body": "hi"}}
    prompt = wrapper.confirming_prompt("Send a mail to a@example.org.", call)
    assert prompt.startswith(
        'The driver confirmed: Send a mail to a@example.org. Run exactly this now, in /srv/repo:'
        ' the tool mcp__mail__send with exactly this input:'
        ' {"body":"hi","description":"cosmetic","to":"a@example.org"}')


def test_take_pending_reads_once_and_distrusts_what_it_reads(tmp_path):
    directory = tmp_path / "pending"
    call = pending.make_call("Bash", {"command": "x"}, "/srv")
    pending.store_pending(directory, "demo", "id-1", call, now=100.0)
    taken = pending.take_pending(directory, "demo")
    assert (taken.id, taken.call, taken.issued_at) == ("id-1", call, 100.0)
    assert pending.take_pending(directory, "demo") is None
    assert not taken.expired(now=160.0) and taken.expired(now=160.1) and taken.expired(now=99.0)
    for content in ("not json", "[]", '{"id": 5, "call": {}, "issued_at": 1}',
                    '{"id": "x", "call": {"tool": "Bash", "input": {}, "cwd": "rel"}, "issued_at": 1}',
                    '{"id": "x", "call": {"tool": "Bash", "input": {}, "cwd": "/"}, "issued_at": "now"}',
                    '{"id": "x", "call": {"tool": "Bash", "input": {}, "cwd": "/"}}'):
        (directory / "demo.json").write_text(content)
        assert pending.take_pending(directory, "demo") is None
        assert not (directory / "demo.json").exists()
    with pytest.raises(ValueError):
        pending.take_pending(directory, "../x")


# --- the second review of #44 -------------------------------------------------------------------

def test_the_branch_behind_head_changing_before_the_yes_is_target_changed(host, monkeypatch):
    host.agent(monkeypatch, ["git push origin HEAD:main"])
    asked = host.ask()
    assert asked["readback"].startswith("Push main to the repository jarvis.")
    run_git(host.checkout, "checkout", "-q", "-b", "surprise")   # not a gated call
    started = _no_turn(host, monkeypatch)
    reply = host.answer(asked)
    assert reply["notice"] == "target_changed" and reply["turn"] is None
    assert not started and host.notified() == []


def test_the_branch_behind_head_changing_during_the_confirming_turn_is_refused_by_the_gate(host, monkeypatch):
    host.agent(monkeypatch, ["git push origin HEAD:main"])
    asked = host.ask()

    def switch_then_push(popen):
        run_git(host.checkout, "checkout", "-q", "-b", "surprise")
        env = popen.kwargs["env"]
        popen.decisions.append(decision.decide(
            {"tool_name": "Bash", "tool_input": {"command": "git push origin HEAD:main"},
             "cwd": str(host.checkout)}, host.config, turn_checkout=host.checkout,
            turn_id=env["JARVIS_TURN_ID"], grant=env["JARVIS_GRANT"],
            grant_base=env["JARVIS_GRANT_BASE"], git=classify.git_runner(env)))

    host.agent(monkeypatch, [], during=switch_then_push)
    reply = host.answer(asked)
    assert host.popens[-1].decisions[0].reason == decision.REASON_TARGET_CHANGED
    assert reply["notice"] == "target_changed"


def test_an_answer_for_another_call_is_expired_not_target_changed(host, monkeypatch):
    """`target_changed` is only said when the answer is for the very call
    that was read back and that call resolves differently now."""
    host.agent(monkeypatch, ["git push origin main"])
    asked = host.ask()
    started = _no_turn(host, monkeypatch)
    assert host.answer(asked, sha256="0" * 64)["error"] == "expired"
    host.agent(monkeypatch, ["git push origin main"])
    asked = host.ask()
    stored = json.loads(host.pending_file().read_text())
    stored["call"]["input"]["command"] = "git push origin main:production"
    host.pending_file().write_text(json.dumps(stored))
    started = _no_turn(host, monkeypatch)
    assert host.answer(asked)["error"] == "expired"
    assert not started


def test_a_new_session_is_still_said_when_a_call_is_pending(host, monkeypatch):
    from agent_host.sessions import SessionStore
    SessionStore(host.config.sessions_dir / "sessions.json").set("demo", "--version")
    host.agent(monkeypatch, [MERGE])
    reply = host.ask()
    assert reply["pending"] is True and reply["spoken"] == ""
    assert reply["notice"] == "new_session"
    from bridge.remote_adapter import parse_reply
    parsed = parse_reply(json.dumps(reply).encode())
    assert parsed["notice"] == "new_session" and parsed["confirmation"] is not None


def test_the_read_back_of_a_push_uses_the_classifiers_own_default_branch(host, monkeypatch):
    """The remote's default branch is `trunk`: the gate calls a push to it a
    merge, and the read-back counts commits for that same branch."""
    run_git(host.checkout, "checkout", "-q", "-b", "trunk")
    run_git(host.checkout, "update-ref", "refs/remotes/origin/trunk", "HEAD")
    run_git(host.checkout, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    run_git(host.checkout, "checkout", "-q", "-b", "feature")
    run_git(host.checkout, "commit", "-q", "--allow-empty", "-m", "two")
    run_git(host.checkout, "commit", "-q", "--allow-empty", "-m", "three")
    host.agent(monkeypatch, ["git push origin feature:trunk"])
    reply = host.ask()
    assert reply["readback"] == ("Push feature as trunk to the repository jarvis."
                                 " 2 commits, no review. Say yes to go ahead.")
    assert reply["confirm"]["class"] == "merge"


def test_the_gates_state_is_in_the_class_self(host, monkeypatch):
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()
    state = host.config.state_dir
    commands = [
        f"echo '{{}}' > {state}/pending/demo.json",
        f"rm -rf {state}/gate",
        f"cp evil.json {state}/pending/demo.json",
        f"sed -i s/a/b/ {host.pending_file()}",
        f"touch {state}/actions/x.jsonl",
    ]
    host.agent(monkeypatch, commands + [
        ("Write", {"file_path": str(host.pending_file()), "content": "{}"}),
        ("Edit", {"file_path": str(state / "gate" / "x.json"), "old_string": "a", "new_string": "b"}),
    ])
    host.ask("tamper")
    assert all(d.reason == decision.REASON_SELF for d in host.popens[-1].decisions)
    del asked


def test_a_grant_releases_exactly_once_whatever_the_state_file_says(host, monkeypatch):
    host.agent(monkeypatch, [MERGE])
    asked = host.ask()

    def forget_and_retry(popen):
        env = popen.kwargs["env"]
        # The agent cannot do this through its tools (the class self); even
        # so, the "used" marker is its own file, created atomically.
        (host.config.gate_dir / f"{env['JARVIS_TURN_ID']}.json").unlink()
        popen.decisions.append(decision.decide(
            {"tool_name": "Bash", "tool_input": {"command": MERGE}, "cwd": str(host.checkout)},
            host.config, turn_checkout=host.checkout, turn_id=env["JARVIS_TURN_ID"],
            grant=env["JARVIS_GRANT"], grant_base=env["JARVIS_GRANT_BASE"],
            git=classify.git_runner(env)))

    host.agent(monkeypatch, [MERGE], during=forget_and_retry)
    host.answer(asked)
    first, second = host.popens[-1].decisions
    assert first.allow and not second.allow
    assert second.reason == decision.REASON_GRANT_USED
    markers = list(host.config.gate_dir.glob("*.used"))
    assert len(markers) == 1 and markers[0].stat().st_mode & 0o777 == 0o600
