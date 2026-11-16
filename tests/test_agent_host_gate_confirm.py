"""The gate's flow around a confirmation: the pending call, the refusal that
names missing parts and its one repair, the grant matched by hash and used
once, `target_changed`, and confirmation switched off (tasks 1.7f, 1.8, 1.9,
1.11, 1.14; design D36 "The flow")."""
import json

import pytest

from agent_host import classify, decision, pending
from agent_host.naming import Named
from tests.gatekit import SHA, Turn, git_init, make_config, run_git

M = "me/demo"
MERGE = f"gh pr merge 21 --repo {M} --match-head-commit {SHA}"
DELETE = "kubectl delete pod web -n shop --context homelab"


# --- a nameable call becomes the one pending call ------------------------------------------------

def test_a_nameable_gated_call_is_denied_and_becomes_pending(tmp_path):
    turn = Turn(tmp_path)
    result = turn.call(MERGE)
    assert not result.allow
    assert result.reason == decision.REASON_PENDING
    assert "end your turn" in result.reason
    state = turn.state()
    # The call, and nothing derived: no class, no target, no hash, no sentence.
    assert state["pending"] == {
        "tool": "Bash", "input": {"command": MERGE},
        "cwd": str((tmp_path / "projects" / "demo").resolve()),
    }
    assert state["events"][-1]["outcome"] == "pending"
    assert state["events"][-1]["readback"] == "Merge pull request 21 on demo, at commit 3f9c2ab."


def test_the_stored_cwd_is_the_real_path_of_the_hook_inputs_cwd(tmp_path):
    turn = Turn(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "projects" / "demo")
    turn.call(MERGE, cwd=link)
    assert turn.state()["pending"]["cwd"] == str((tmp_path / "projects" / "demo").resolve())


def test_a_second_gated_call_in_one_turn_is_denied_and_not_pending(tmp_path):
    turn = Turn(tmp_path)
    turn.call(MERGE)
    for command in (DELETE, MERGE, "gh pr merge 22", "terraform apply"):
        result = turn.call(command)
        assert not result.allow
        assert result.reason == decision.REASON_ONE_AT_A_TIME
    assert turn.state()["pending"]["input"] == {"command": MERGE}
    # An ungated call still runs.
    assert turn.call("gh pr view 21").allow


def test_what_cannot_be_named_is_never_pending(tmp_path):
    turn = Turn(tmp_path)
    for command, reason in (
        ("terraform apply", decision.REASON_DESK),
        ("gh api -X DELETE repos/me/demo", decision.REASON_DESK),
        ("sudo systemctl restart web", decision.REASON_DESK),
        (f"gh pr merge 'twenty one' --repo {M} --match-head-commit {SHA}", decision.REASON_DESK),
        ("bash -c 'gh pr merge 1; gh repo delete x'", decision.REASON_RUN_ALONE),
        (MERGE + " && echo done", decision.REASON_RUN_ALONE),
    ):
        result = turn.call(command)
        assert not result.allow and result.reason == reason, command
    state = turn.state()
    assert state["pending"] is None
    assert [e["code"] for e in state["events"]] == ["needs_desk"] * 4 + ["run_alone"] * 2
    # ... and a nameable call after them still can be.
    assert turn.call(MERGE).reason == decision.REASON_PENDING


def test_a_self_write_is_never_pending(tmp_path):
    turn = Turn(tmp_path)
    target = tmp_path / "projects" / "demo" / ".claude" / "settings.local.json"
    for result in (
        turn.call(tool="Write", tool_input={"file_path": str(target), "content": "{}"}),
        turn.call("echo x >> ~/.bashrc"),
        turn.call(f"sudo {turn.config.confirm_program} off --force"),
        turn.call(f"{turn.config.confirm_program} off"),
    ):
        assert not result.allow
        assert result.reason == decision.REASON_SELF
    assert turn.state()["pending"] is None


# --- the confirmation switch ------------------------------------------------------------------------

def test_the_fixed_confirm_command_is_read_back_from_its_template(tmp_path):
    turn = Turn(tmp_path)
    program = turn.config.confirm_program
    for command, sentence in (
        (f"sudo {program} off", "Switch voice confirmation off for everything."),
        (f"sudo {program} on", "Switch voice confirmation on for everything."),
        (f"sudo {program} off message", "Switch voice confirmation off for message actions."),
        (f"sudo {program} on merge", "Switch voice confirmation on for merge actions."),
    ):
        derived = turn.derive(command)
        assert isinstance(derived.outcome, Named), command
        assert derived.outcome.sentence == sentence
        assert derived.verdict.classes == ("self",)
        assert not derived.verdict.desk_only
    assert turn.call(f"sudo {program} off").reason == decision.REASON_PENDING


def test_every_other_argument_form_of_the_switch_is_desk_only(tmp_path):
    turn = Turn(tmp_path)
    program = turn.config.confirm_program
    for command in (
        f"sudo {program}",
        f"sudo {program} maybe",
        f"sudo {program} off self",
        f"sudo {program} off nonsense",
        f"sudo {program} off merge deploy",
        f"sudo {program} --off",
        f"sudo {program} off --config /tmp/other.toml",
        f"sudo -u root {program} off",
        f"sudo env X=1 {program} off",
        f"sudo bash -c '{program} off'",
        f"{program} off",
        f"doas {program} off",
        f"sudo {program} off; echo done",
        f"sudo {program} off > /dev/null",
        f"sudo ./{program.name} off",
        f"sudo {program}2 off",
        f"sudo cp x {program}",
    ):
        derived = turn.derive(command)
        assert derived.verdict.gated, command
        assert derived.outcome is None, command
        assert not turn.call(command).allow
    assert turn.state()["pending"] is None


# --- the refusal that names missing parts, and its one repair -----------------------------------------

def test_a_merge_without_repo_and_pin_is_refused_with_exactly_those_two_parts(tmp_path):
    turn = Turn(tmp_path)
    result = turn.call("gh pr merge 21")
    assert not result.allow
    assert result.reason == decision.reason_missing(
        ("add --repo owner/name", "add --match-head-commit and the full commit"))
    assert "add --repo owner/name; add --match-head-commit and the full commit." in result.reason
    assert turn.state()["pending"] is None

    # The agent may look up what it needs: reading is not gated.
    assert turn.call("gh pr view 21 --json headRefOid").allow
    # The same call, with only those parts added: pending.
    result = turn.call(MERGE)
    assert result.reason == decision.REASON_PENDING
    assert turn.state()["pending"]["input"] == {"command": MERGE}


@pytest.mark.parametrize("second", [
    f"gh pr merge 22 --repo {M} --match-head-commit {SHA}",       # also changes the number
    f"gh pr merge 21 --repo {M} --match-head-commit {SHA} --squash",
    f"gh repo delete {M}",                                          # another command
    DELETE,
    "gh pr merge 21",                                               # the same call again
    f"gh pr merge 21 --repo {M}",                                   # a repair that is still incomplete
])
def test_anything_else_after_such_a_refusal_needs_the_desk(tmp_path, second):
    turn = Turn(tmp_path)
    turn.call("gh pr merge 21")
    result = turn.call(second)
    assert not result.allow
    assert result.reason == decision.REASON_DESK
    state = turn.state()
    assert state["pending"] is None
    assert state["events"][-1]["code"] == "needs_desk"
    # A second repair, now the right one: too late, also the desk.
    assert turn.call(MERGE).reason == decision.REASON_DESK
    assert turn.state()["pending"] is None


def test_a_repair_with_another_tool_needs_the_desk(tmp_path):
    rules_dir = tmp_path / "etc" / "rules.d"
    rules_dir.mkdir(parents=True)
    (rules_dir / "mail.toml").write_text(
        '[[tool_actions]]\ntool = "mcp__mail__send"\nclass = "message"\nrecipients = ["to"]\n')
    turn = Turn(tmp_path)
    turn.call("gh pr merge 21")
    result = turn.call(tool="mcp__mail__send", tool_input={"to": "a@example.org"})
    assert result.reason == decision.REASON_DESK
    assert turn.state()["pending"] is None


def test_the_repair_must_run_in_the_same_directory(tmp_path):
    turn = Turn(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    turn.call("gh pr merge 21")
    assert turn.call(MERGE, cwd=other).reason == decision.REASON_DESK


def test_missing_parts_of_cluster_calls_and_of_a_push(tmp_path):
    for command, parts, repair in (
        ("kubectl delete pod x", ("add -n namespace", "add --context"),
         "kubectl delete pod x -n y --context homelab"),
        ("kubectl delete pod x -n y", ("add --context",), "kubectl delete pod x -n y --context homelab"),
    ):
        turn = Turn(tmp_path)
        assert turn.call(command).reason == decision.reason_missing(parts)
        assert turn.call(repair).reason == decision.REASON_PENDING

    work = tmp_path / "work"
    git_init(work)
    run_git(work, "remote", "add", "origin", "git@github.com:me/jarvis.git")
    turn = Turn(tmp_path, cwd=work, git=classify.run_git)
    result = turn.call("git push")
    assert result.reason == decision.reason_missing(("name the remote and the refspec",))
    assert turn.call("git push origin main").reason == decision.REASON_PENDING
    assert turn.state()["pending"]["input"] == {"command": "git push origin main"}


def test_a_repaired_call_that_is_no_longer_gated_just_runs(tmp_path):
    work = tmp_path / "work"
    git_init(work)
    run_git(work, "remote", "add", "origin", "git@github.com:me/jarvis.git")
    turn = Turn(tmp_path, cwd=work, git=classify.run_git)
    turn.call("git push")
    assert turn.call("git push origin feature").allow       # a feature branch needs no yes
    turn = Turn(tmp_path)
    turn.call("gh pr create --fill")
    assert turn.call("gh pr create --fill --repo me/demo").allow  # an own repository needs no yes


# --- the grant -------------------------------------------------------------------------------------------

def _granted(tmp_path, command=MERGE, **kwargs):
    asking = Turn(tmp_path, **kwargs)
    derived = asking.derive(command)
    assert isinstance(derived.outcome, Named)
    return Turn(tmp_path, asking.config, grant=derived.hash, grant_base=derived.base_hash, **kwargs)


def test_a_call_whose_hash_is_the_grant_passes_in_silence_once(tmp_path):
    turn = _granted(tmp_path)
    result = turn.call(MERGE)
    assert result.allow and result.reason == ""
    state = turn.state()
    assert state["grant_used"] is True
    assert state["events"][-1]["outcome"] == "released"
    assert state["pending"] is None
    # A reused grant.
    result = turn.call(MERGE)
    assert not result.allow
    assert result.reason == decision.REASON_GRANT_USED
    assert turn.state()["pending"] is None


@pytest.mark.parametrize("other", [
    MERGE + " ",                                                     # one more byte
    MERGE.replace("21", "22"),
    MERGE.replace("--repo me/demo", "--repo them/demo"),
    MERGE + " --squash",
    DELETE,
])
def test_a_grant_for_another_hash_releases_nothing(tmp_path, other):
    turn = _granted(tmp_path)
    result = turn.call(other)
    assert not result.allow
    assert turn.state()["grant_used"] is False
    # The grant is still good for the call it was given for.
    assert turn.call(MERGE).allow
    assert turn.state()["grant_used"] is True


def test_the_confirmed_bytes_from_another_cwd_are_refused(tmp_path):
    turn = _granted(tmp_path)
    other = tmp_path / "projects" / "other"
    (other / ".git").mkdir(parents=True)
    result = turn.call(MERGE, cwd=other)
    assert not result.allow
    assert turn.state()["grant_used"] is False
    assert turn.call(MERGE).allow


def test_a_grant_does_not_release_what_is_not_gated_or_not_nameable(tmp_path):
    turn = Turn(tmp_path, grant="0" * 64, grant_base="0" * 64)
    assert turn.call("ls").allow
    assert turn.state()["grant_used"] is False
    assert not turn.call("terraform apply").allow
    assert not turn.call("echo x >> ~/.bashrc").allow
    for bad in ("", "yes", "true", "*", "0" * 63):
        turn = Turn(tmp_path, grant=bad or None)
        assert not turn.call(MERGE).allow


def test_no_grant_in_the_state_directory_releases_anything(tmp_path):
    """The agent can write every file the wrapper writes; the grant is in the
    environment of the claude process for that reason."""
    turn = Turn(tmp_path)
    derived = turn.derive(MERGE)
    gate_dir = turn.config.gate_dir
    gate_dir.mkdir(parents=True)
    (gate_dir / "grant").write_text(derived.hash)
    (gate_dir / f"{turn.turn_id}.grant").write_text(derived.hash)
    (turn.config.state_dir / "JARVIS_GRANT").write_text(derived.hash)
    assert not turn.call(MERGE).allow


def test_with_unsafe_checkout_settings_a_matching_grant_releases_nothing(tmp_path):
    turn = _granted(tmp_path)
    settings = tmp_path / "projects" / "demo" / ".claude" / "settings.json"
    settings.parent.mkdir()
    for content in ('{"env": {"JARVIS_GRANT": "x"}}', '{"hooks": {}}', '{"disableAllHooks": true}',
                    "{ not json"):
        settings.write_text(content)
        result = turn.call(MERGE)
        assert not result.allow
        assert result.reason == decision.REASON_SETTINGS
        assert turn.call("ls").allow                        # an ungated call still runs
    state = turn.state()
    assert state["grant_used"] is False and state["pending"] is None
    settings.write_text("{}")                                # removing the key lifts the denial
    assert turn.call(MERGE).allow


def test_unsafe_settings_in_the_calls_own_worktree_deny_with_a_grant_too(tmp_path):
    worktree = tmp_path / "worktrees" / "x"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: elsewhere\n")
    turn = _granted(tmp_path, cwd=worktree)
    (worktree / ".claude").mkdir()
    (worktree / ".claude" / "settings.local.json").write_text('{"env": {"A": "1"}}')
    assert turn.call(MERGE).reason == decision.REASON_SETTINGS
    (worktree / ".claude" / "settings.local.json").unlink()
    assert turn.call(MERGE).allow


# --- a push: the address is resolved again at release ------------------------------------------------------

def test_a_pushurl_set_between_read_back_and_release_is_target_changed(tmp_path):
    work = tmp_path / "work"
    git_init(work)
    run_git(work, "remote", "add", "origin", "git@github.com:me/jarvis.git")
    turn = _granted(tmp_path, "git push origin main", cwd=work, git=classify.run_git)

    run_git(work, "config", "remote.origin.pushurl", "git@github.com:me/elsewhere.git")
    result = turn.call("git push origin main")
    assert not result.allow                                   # nothing is pushed
    assert result.reason == decision.REASON_TARGET_CHANGED
    state = turn.state()
    assert state["grant_used"] is False
    assert state["pending"] is None
    assert state["events"][-1]["code"] == "target_changed"

    run_git(work, "config", "--unset", "remote.origin.pushurl")
    assert turn.call("git push origin main").allow            # the confirmed address again


def test_a_push_to_an_address_on_another_host_at_release_is_target_changed(tmp_path):
    work = tmp_path / "work"
    git_init(work)
    run_git(work, "remote", "add", "origin", "git@github.com:me/jarvis.git")
    turn = _granted(tmp_path, "git push origin main", cwd=work, git=classify.run_git)
    run_git(work, "config", "url.git@elsewhere.example:.pushInsteadOf", "git@github.com:")
    assert turn.call("git push origin main").reason == decision.REASON_TARGET_CHANGED


# --- confirmation switched off --------------------------------------------------------------------------------

def test_confirm_off_lets_gated_calls_through_and_marks_them_not_confirmed(tmp_path):
    turn = Turn(tmp_path, make_config(tmp_path, confirm="off"))
    for command in (MERGE, "terraform apply", "gh pr merge 21", "sudo systemctl restart web"):
        assert turn.call(command).allow, command
    state = turn.state()
    assert [e["outcome"] for e in state["events"]] == ["not_confirmed"] * 4
    assert state["pending"] is None and state["grant_used"] is False


def test_confirm_off_never_applies_to_what_the_gate_did_not_fully_understand(tmp_path):
    """"Off" means "do not ask me", not "do not look": only a single call
    the gate fully understood is let through."""
    turn = Turn(tmp_path, make_config(tmp_path, confirm="off"))
    for command, reason in (
        (MERGE + " && echo done", decision.REASON_RUN_ALONE),             # compound
        ("bash -c 'gh pr merge 1; gh repo delete x'", decision.REASON_RUN_ALONE),
        (MERGE + " --subject 'a; b'", decision.REASON_RUN_ALONE),         # run it alone
        ("cd /srv/x && make", decision.REASON_NOT_UNDERSTOOD),            # not understood
        ("xargs kubectl delete pod", decision.REASON_RUN_ALONE),
        ("mystery gh repo delete x", decision.REASON_NOT_UNDERSTOOD),
        ("python3 -c 'print(1)'", decision.REASON_DESK),                  # inline code
        ("bash", decision.REASON_DESK),
        ("awk 'BEGIN{system(1)}'", decision.REASON_DESK),
        ("echo x >> ~/.bashrc", decision.REASON_SELF),                    # self
    ):
        result = turn.call(command)
        assert not result.allow and result.reason == reason, command
    result = turn.call(tool="SendMessage", tool_input={"to": "x"})
    assert result.reason == decision.REASON_DESK
    assert all(e["outcome"] != "not_confirmed" for e in turn.state()["events"])


def test_the_class_self_stays_desk_only_with_confirmation_off(tmp_path):
    turn = Turn(tmp_path, make_config(tmp_path, confirm="off"))
    assert turn.call("echo x >> ~/.bashrc").reason == decision.REASON_SELF
    # The switch itself still wants its yes.
    assert turn.call(f"sudo {turn.config.confirm_program} on").reason == decision.REASON_PENDING


def test_confirm_skip_lets_only_its_classes_through(tmp_path):
    turn = Turn(tmp_path, make_config(tmp_path, confirm_skip=["delete"]))
    assert turn.call(DELETE).allow
    assert turn.state()["events"][-1]["outcome"] == "not_confirmed"
    # Every class of the call must be skipped.
    assert not turn.call("sudo " + DELETE).allow
    assert not turn.call("kubectl delete pod web -n shop --context homelab --force").allow
    # A gated call in no class is in no skipped class.
    assert not turn.call("terraform apply").allow
    turn = Turn(tmp_path, make_config(tmp_path, confirm_skip=["delete"]))
    assert turn.call(MERGE).reason == decision.REASON_PENDING


def test_with_confirmation_off_the_settings_rule_still_holds(tmp_path):
    turn = Turn(tmp_path, make_config(tmp_path, confirm="off"))
    settings = tmp_path / "projects" / "demo" / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"hooks": {}}')
    assert turn.call(MERGE).reason == decision.REASON_SETTINGS


# --- the state the gate keeps --------------------------------------------------------------------------------

def test_a_state_file_the_gate_cannot_read_is_an_error_not_an_empty_state(tmp_path):
    turn = Turn(tmp_path)
    turn.config.gate_dir.mkdir(parents=True)
    path = turn.config.gate_dir / f"{turn.turn_id}.json"
    for content in ("{ not json", "[]", '{"pending": null}'):
        path.write_text(content)
        with pytest.raises(pending.StateError):
            turn.call(MERGE)
    assert turn.call("ls").allow  # an ungated call never touches the state


def test_a_turn_id_that_is_not_the_wrappers_is_refused(tmp_path):
    turn = Turn(tmp_path)
    for bad in ("", "t1", "../../etc/x", "a/b"):
        turn.turn_id = bad
        with pytest.raises(pending.StateError):
            turn.call(MERGE)


def test_the_state_is_private_to_the_owner(tmp_path):
    turn = Turn(tmp_path)
    turn.call(MERGE)
    path = turn.config.gate_dir / f"{turn.turn_id}.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert turn.config.gate_dir.stat().st_mode & 0o777 == 0o700
    assert set(json.loads(path.read_text())) == {
        "pending", "refused", "repair_used", "desk", "grant_used", "events"}


def test_every_denial_has_a_code_and_the_notice_codes_are_d33s():
    from agent_host.wrapper import NOTICE_CODES
    for reason, code in (
        (decision.REASON_SETTINGS, "settings_changed"), (decision.REASON_RUN_ALONE, "run_alone"),
        (decision.REASON_SELF, "needs_desk"), (decision.REASON_DESK, "needs_desk"),
        (decision.REASON_ERROR, "needs_desk"), (decision.REASON_GRANT_USED, "needs_desk"),
        (decision.REASON_TARGET_CHANGED, "target_changed"),
    ):
        assert reason.startswith(f"[{code}]")
        assert code in NOTICE_CODES
    for reason in (decision.REASON_PENDING, decision.REASON_ONE_AT_A_TIME):
        assert reason.startswith("[pending]") and "end your turn" in reason
    assert decision.reason_missing(("add -n namespace",)).startswith("[missing]")


# --- after the review of #43: what the gate cannot read, in the 1b2 flow ---------------------

def test_a_call_the_gate_cannot_read_is_never_pending_and_ends_nothing(tmp_path):
    turn = Turn(tmp_path)
    result = turn.call("cd /srv/repo && pytest -q")
    assert not result.allow and result.reason == decision.REASON_NOT_UNDERSTOOD
    state = turn.state()
    assert state["pending"] is None
    assert state["events"][-1]["code"] is None            # no notice: the agent goes on
    # ... and a nameable call after it can still become pending.
    assert turn.call(MERGE).reason == decision.REASON_PENDING


def test_a_grant_never_releases_a_call_the_gate_cannot_read(tmp_path):
    asking = Turn(tmp_path)
    command = "cd /srv/repo && pytest -q"
    turn = Turn(tmp_path, asking.config, grant=asking.derive(command).hash,
                grant_base=asking.derive(command).base_hash)
    assert not turn.call(command).allow
    assert turn.state()["grant_used"] is False


def test_confirm_off_does_not_let_a_call_the_gate_cannot_read_through(tmp_path):
    turn = Turn(tmp_path, make_config(tmp_path, confirm="off"))
    assert not turn.call("cd /srv/repo && pytest -q").allow
    assert not turn.call("python3 -c 'print(1)'").allow
    assert not turn.call("cd /tmp && echo x >> ~/.bashrc").allow
    turn = Turn(tmp_path, make_config(tmp_path, confirm_skip=["merge"]))
    assert not turn.call("cd /srv/repo && pytest -q").allow


@pytest.mark.parametrize("command", [
    f"GH_HOST=evil.example gh pr merge 21 --repo {M} --match-head-commit {SHA}",
    f"env GH_REPO=them/x gh pr merge 21 --repo {M} --match-head-commit {SHA}",
    "KUBECONFIG=/tmp/other kubectl delete pod web -n shop --context homelab",
    "env KUBECONFIG=/tmp/other kubectl delete pod web -n shop --context homelab",
    "HELM_KUBECONTEXT=x helm uninstall web -n shop --kube-context homelab",
    f"FOO=1 gh repo delete {M}",
])
def test_a_variable_set_in_front_of_a_call_makes_it_not_nameable(tmp_path, command):
    """The sentence would name targets that the variable can point somewhere
    else: another forge host, another cluster behind the same context name."""
    turn = Turn(tmp_path)
    derived = turn.derive(command)
    assert derived.verdict.gated and derived.outcome is None
    assert turn.call(command).reason == decision.REASON_DESK
    assert turn.state()["pending"] is None


def test_gated_calls_the_review_added_have_no_sentence(tmp_path):
    turn = Turn(tmp_path)
    for command in ("python3 -c 'print(1)'", "ssh jumpy systemctl restart web",
                    "ssh jumpy rm -rf build", "git -c alias.x=push x",
                    f"gh pr comment 5 --repo {M} --repo them/demo --body hi",
                    f"gh pr comment https://github.com/them/demo/pull/5 --repo {M} --body hi"):
        derived = turn.derive(command)
        assert derived.verdict.gated and derived.outcome is None, command
    result = turn.call(tool="SendMessage", tool_input={"to": "main", "message": "x"})
    assert result.reason == decision.REASON_DESK
    assert turn.state()["pending"] is None


def test_a_gated_call_with_a_separator_in_a_quoted_argument_is_run_it_alone(tmp_path):
    turn = Turn(tmp_path)
    result = turn.call(MERGE + " --subject 'one; two'")
    assert result.reason == decision.REASON_RUN_ALONE
    assert turn.state()["pending"] is None
    assert turn.call("git commit -m 'one; two'").allow


# --- the second review of #44: the grant ------------------------------------------------------

def test_only_a_well_formed_grant_can_be_used(tmp_path):
    asking = Turn(tmp_path)
    good = asking.derive(MERGE).hash
    for bad in (good.upper(), good[:-1], good + "0", "../" + good, good[:32] + "/" + good[33:]):
        turn = Turn(tmp_path, asking.config, grant=bad)
        assert not turn.call(MERGE).allow
        assert not list(asking.config.gate_dir.glob("*.used"))


def test_two_gates_at_the_same_moment_release_a_grant_once(tmp_path):
    import threading
    turn = _granted(tmp_path)
    results = []
    barrier = threading.Barrier(8)

    def call():
        barrier.wait()
        results.append(turn.call(MERGE).allow)

    threads = [threading.Thread(target=call) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [False] * 7 + [True]
    state = turn.state()
    assert state["grant_used"] is True
    assert len(state["events"]) == 8                      # no event was lost under the lock


def test_two_gates_at_the_same_moment_make_one_call_pending(tmp_path):
    import threading
    turn = Turn(tmp_path)
    reasons = []
    barrier = threading.Barrier(6)

    def call(number):
        barrier.wait()
        reasons.append(turn.call(MERGE.replace("21", str(number))).reason)

    threads = [threading.Thread(target=call, args=(n,)) for n in range(30, 36)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert reasons.count(decision.REASON_PENDING) == 1
    assert reasons.count(decision.REASON_ONE_AT_A_TIME) == 5


def test_confirm_skip_works_for_a_connector_tool_by_its_class(tmp_path):
    turn = Turn(tmp_path, make_config(tmp_path, confirm_skip=["tool"]))
    assert turn.call(tool="mcp__github__push_files", tool_input={"x": 1}).allow
    assert not turn.call(tool="mcp__x__send_thing", tool_input={"x": 1}).allow     # message
    assert not turn.call(tool="SendMessage", tool_input={"x": 1}).allow            # nobody listed it
    turn = Turn(tmp_path, make_config(tmp_path, confirm_skip=["message"]))
    assert turn.call(tool="mcp__x__send_thing", tool_input={"x": 1}).allow


def test_the_switch_read_back_says_what_on_for_a_class_means_when_everything_is_off(tmp_path):
    turn = Turn(tmp_path, make_config(tmp_path, confirm="off"))
    program = turn.config.confirm_program
    assert turn.derive(f"sudo {program} on merge").outcome.sentence == (
        "Switch voice confirmation on for merge actions only. Everything else stays unconfirmed.")
    assert turn.derive(f"sudo {program} on").outcome.sentence \
        == "Switch voice confirmation on for everything."
    turn = Turn(tmp_path)
    assert turn.derive(f"sudo {program} on merge").outcome.sentence \
        == "Switch voice confirmation on for merge actions."
