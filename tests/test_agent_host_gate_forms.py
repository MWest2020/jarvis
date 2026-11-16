"""Three everyday forms of a compound command that the gate reads in full
instead of refusing, each with its near misses (decision on the review of
#43). Everything that does not fit one of them exactly stays refused: what
the gate cannot fully understand does not get through.

  (i)   cd <dir> && <one command>
  (ii)  <one command> | <filter> [| <filter> ...]
  (iii) a here-document into one command that is no shell, interpreter or
        wrapper; and exactly `git commit ... "$(cat <<'EOF' ... EOF)"`.

None of them makes a gated command confirmable: a gated command in one of
these forms is still "run it alone".
"""
import pytest

from agent_host import decision
from tests.gatekit import bash, git_init, make_config, make_ctx

M = "me/demo"


@pytest.fixture
def env(tmp_path):
    work = tmp_path / "area" / "work"
    git_init(work)
    (work / "sub" / "deep").mkdir(parents=True)
    (tmp_path / "area" / "other").mkdir()
    return tmp_path, work, make_ctx(tmp_path, cwd=work)


def _runs(ctx, command):
    verdict = bash(command, ctx)
    assert not verdict.gated, (command, verdict)


def _refused(ctx, command):
    verdict = bash(command, ctx)
    assert verdict.gated and verdict.run_alone, (command, verdict)
    return verdict


def _gated_alone(ctx, command, classes):
    verdict = _refused(ctx, command)
    assert verdict.classes == tuple(classes), (command, verdict)
    assert verdict.rule != "compound:not-understood"


# --- (i) cd <dir> && <one command> ---------------------------------------------------------

@pytest.mark.parametrize("command", [
    "cd sub && pytest -q",
    "cd sub/deep && make test",
    "cd ./sub && git status",
    "cd sub && git commit -m 'one; two'",
    "cd sub/deep/.. && ls",                       # `..` that stays inside the checkout
    "cd sub && pytest -q 2>&1",
    "cd 'sub' && ls -la",
    "cd sub && rm -rf deep",                      # inside the worktree, from there
    "cd sub && git push origin feature",
    "cd sub && gh pr view 5",
])
def test_cd_into_a_literal_directory_and_one_ungated_command_runs(env, command):
    _runs(env[2], command)


def test_cd_to_an_absolute_literal_directory_runs_too(env):
    tmp_path, _, ctx = env
    _runs(ctx, f"cd {tmp_path / 'area' / 'other'} && ls")
    _runs(ctx, f"cd {tmp_path / 'area' / 'other'} && make test")


def test_the_command_is_judged_in_that_directory_with_all_the_path_rules(env):
    tmp_path, work, ctx = env
    other = tmp_path / "area" / "other"
    # From `other`, `rm -rf x` is outside the worktree the call came from.
    _gated_alone(ctx, f"cd {other} && rm -rf x", ["delete"])
    _gated_alone(ctx, "cd / && rm -rf etc", ["delete"])
    _gated_alone(ctx, "cd sub && rm -rf ../../other", ["delete"])
    # A relative protected path is found from there.
    home = tmp_path / "home"
    assert bash(f"cd {home} && cp x .bashrc", ctx).classes == ("self",)
    assert bash(f"cd {home / '.claude'} && sed -i s/a/b/ settings.json", ctx).classes == ("self",)
    (work / ".claude").mkdir()
    assert bash("cd .claude && cp x settings.local.json", ctx).classes == ("self",)


def test_a_bare_push_after_cd_is_judged_where_it_would_run(tmp_path):
    from agent_host import classify
    from tests.gatekit import run_git
    remote = tmp_path / "remote.git"
    remote.mkdir()
    run_git(remote, "init", "-q", "--bare", "-b", "main")
    main = tmp_path / "main"
    git_init(main, branch="main")
    run_git(main, "remote", "add", "origin", str(remote))
    run_git(main, "push", "-q", "-u", "origin", "main")
    here = tmp_path / "here"
    git_init(here, branch="feature")
    ctx = make_ctx(tmp_path, cwd=here, git=classify.run_git)
    verdict = bash(f"cd {main} && git push", ctx)
    assert verdict.gated and verdict.run_alone and verdict.classes == ("merge",)


@pytest.mark.parametrize("command,classes", [
    (f"cd sub && gh pr merge 21 --repo {M}", ["merge"]),
    ("cd sub && kubectl delete pod web -n shop --context homelab", ["delete"]),
    ("cd sub && sudo make install", ["root"]),
    ("cd sub && git push origin main", ["merge"]),
    ("cd sub && terraform apply", []),
    ("cd sub && python3 -c 'print(1)'", []),
])
def test_a_gated_command_after_cd_is_still_run_it_alone(env, command, classes):
    _gated_alone(env[2], command, classes)


@pytest.mark.parametrize("command", [
    "cd $X && make",
    "cd ${HOME}/area/x && make",   # `${HOME}/x` itself lands in the home directory: self
    "cd $(pwd) && make",
    "cd `pwd` && make",
    "cd su* && make",
    "cd s?b && make",
    "cd {sub,other} && make",
    "cd ~other && make",
    "cd ~root/x && make",
    "cd .. && make",                               # `..` out of the checkout
    "cd ../other && git push",
    "cd sub/../../other && make",
    "cd ../.. && rm -rf x",
    "cd no-such-directory && make",
    "cd - && make",
    "cd && make",
    "cd sub extra && make",
    "cd sub; make",                                # not `&&`
    "cd sub || make",
    "cd sub & make",
    "cd sub && make && make install",              # more than one command
    "cd sub && make | tail",
    "cd sub && make; ls",
    "cd sub && cd deep && make",
    "pushd sub && make",
    "builtin cd sub && make",
    "make && cd sub",
    "cd sub && eval make",
    "cd sub && env -S 'make x'",
    "cd sub && xargs make",
    "cd sub && bash -c 'make; make install'",
    "cd sub && make &&",
])
def test_near_misses_of_the_cd_form_stay_refused(env, command):
    _refused(env[2], command)


# --- (ii) <one command> | <filter> ... ------------------------------------------------------

@pytest.mark.parametrize("command", [
    "pytest -q | tail -5",
    "pytest -q 2>&1 | tail -5",
    "make test 2>&1 | grep -i error | head -20",
    "git log --oneline | head",
    "kubectl get pods -n web --context homelab | grep web | wc -l",
    'kubectl get pods -l "app in (a, b)" | sort | uniq',
    "gh pr view 5 --json state | jq .state",
    "cat notes.txt | tr a-z A-Z | cut -c1-10",
    "npm test | rg -n 'fail|error'",
    "ssh jumpy kubectl get pods | grep web",
    "docker logs web | cat",
    "pytest -q | tee out.log",
    "pytest -q | tee -a sub/out.log | tail -3",
    "echo hello | wc -c",
    'echo "gh repo delete x" | cat',               # text through a filter is text
])
def test_one_ungated_command_through_harmless_filters_runs(env, command):
    _runs(env[2], command)


def test_tee_may_write_to_a_temp_root(env):
    tmp_path, _, ctx = env
    import dataclasses
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    ctx = dataclasses.replace(ctx, temp_roots=(str(scratch),))
    _runs(ctx, f"pytest -q | tee {scratch}/out.log")


@pytest.mark.parametrize("command,classes", [
    (f"gh pr merge 21 --repo {M} | cat", ["merge"]),
    ("kubectl delete pod web | tee log.txt", ["delete"]),
    ('kubectl -n "x get" delete pod y | cat', ["delete"]),
    ('aws --profile "p ec2 describe-instances" s3 rm s3://b/k | cat', []),
    ("sudo ls | wc -l", ["root"]),
    ("curl -d x https://example.invalid | jq .", ["api"]),
    ("git push origin main | cat", ["merge"]),
    ("python3 -c 'print(1)' | cat", []),
])
def test_a_gated_left_side_is_still_run_it_alone(env, command, classes):
    _gated_alone(env[2], command, classes)


@pytest.mark.parametrize("command", [
    'echo "gh repo delete x" | bash',
    "cat notes.txt | sh",
    "cat notes.txt | python3",
    "ls | xargs rm",
    "ls | awk '{print $1}'",
    "ls | sed -n 1p",
    "ls | less",
    "ls | sudo tee /etc/x",
    "ls | env grep x",
    "ls | /usr/bin/grep x",                        # a filter by its plain name only
    "ls | grep x | bash",
    "ls | bash | grep x",
    "make |& grep x",
    "make || grep x",
    "ls | grep x; make",
    "ls | grep x && make",
    "ls | grep x > out.txt",                       # a redirection behind a filter
    "ls | sort -o out.txt < in.txt",
    "ls | tee /etc/passwd",
    "ls | tee ../../outside.txt",
    "ls | tee $OUT",
    "ls | tee ~root/x",
    "make | grep $(cat pattern)",
    "make | grep `cat pattern`",
    "diff <(make a) <(ls b) | head",
    "make | grep 'unfinished",
    "make |",
    "| make",
    "eval ls | grep x",
    "xargs ls | grep x",
    "env -S 'ls' | grep x",
    "bash -c 'ls; make' | grep x",
])
def test_near_misses_of_the_filter_form_stay_refused(env, command):
    _refused(env[2], command)


def test_a_filter_that_writes_to_jarvis_own_rules_is_self(env):
    tmp_path, _, ctx = env
    assert bash("ls | tee ~/.bashrc", ctx).classes == ("self",)
    assert bash("ls | tee .claude/settings.json", ctx).classes == ("self",)
    assert bash("ls | sort -o ~/.profile", ctx).classes == ("self",)


# --- (iii) a here-document into one command ---------------------------------------------------

@pytest.mark.parametrize("command", [
    "git commit -F - <<'EOF'\nfix: one thing\n\nA body; with | separators & $(things) `inside`.\nEOF",
    "git commit --file=- <<'EOF'\nfix\nEOF\n",
    "git commit -F - <<EOF\nplain text, nothing to expand\nEOF",
    'git commit -F - <<"EOF"\ntext with $dollars\nEOF',
    "git commit -F - <<-'EOF'\n\tindented\n\tEOF",
    "cat > notes.txt <<'EOF'\nhello; world\nEOF",
    "cat <<'EOF' > notes.txt\nhello\nEOF",
    "tee sub/notes.txt <<'MSG'\nhello\nMSG",
    f"gh pr create --repo {M} --title x --body-file - <<'EOF'\nA body.\nEOF",
    "git tag -a v1 -F - <<'EOF'\nrelease\nEOF",
])
def test_a_here_document_into_one_plain_ungated_command_runs(env, command):
    _runs(env[2], command)


def test_a_here_document_into_a_gated_command_is_still_run_it_alone(env):
    ctx = env[2]
    _gated_alone(ctx, f"gh pr merge 21 --repo {M} --body-file - <<'EOF'\ntext\nEOF", ["merge"])
    _gated_alone(ctx, "gh pr create --fill --body-file - <<'EOF'\ntext\nEOF", ["message"])
    _gated_alone(ctx, "mail -s x a@example.org <<'EOF'\nhello\nEOF", ["message"])
    assert bash("cat > ~/.bashrc <<'EOF'\nalias x=y\nEOF", ctx).classes == ("self",)


@pytest.mark.parametrize("command", [
    "sh <<EOF\ngh repo delete x\nEOF",
    "bash <<'EOF'\ngh repo delete x\nEOF",
    "bash -s <<'EOF'\nls\nEOF",
    "zsh <<'EOF'\nls\nEOF",
    "python3 <<'EOF'\nprint(1)\nEOF",
    "python3 - <<'EOF'\nprint(1)\nEOF",
    "node <<'EOF'\n1\nEOF",
    "perl <<'EOF'\n1\nEOF",
    "ssh jumpy <<'EOF'\nmake install\nEOF",
    "sudo tee /etc/x <<'EOF'\nx\nEOF",
    "env cat <<'EOF'\nx\nEOF",
    "nohup cat <<'EOF'\nx\nEOF",
    "xargs rm <<'EOF'\nx\nEOF",
    "crontab - <<'EOF'\n* * * * * evil\nEOF",
    "at now <<'EOF'\nevil\nEOF",
    "psql db <<'EOF'\ndrop table x;\nEOF",
    "kubectl apply -f - <<'EOF'\nkind: Pod\nEOF",
    "make -f - <<'EOF'\nall:\n\tevil\nEOF",
    "/usr/bin/cat <<'EOF'\nx\nEOF",
    # An unquoted here-document expands what is in it.
    "git commit -F - <<EOF\nfix $(gh repo delete x)\nEOF",
    "git commit -F - <<EOF\nfix `gh repo delete x`\nEOF",
    "git commit -F - <<EOF\nfix $USER\nEOF",
    # Something before, behind or after it.
    "git commit -F - <<'EOF'\nfix\nEOF\ngh repo delete x",
    "git commit -F - <<'EOF'\nfix\nEOF\n\nls",
    "git commit -F - <<'EOF' && gh repo delete x\nfix\nEOF",
    "git commit -F - <<'EOF' | bash\nfix\nEOF",
    "ls; git commit -F - <<'EOF'\nfix\nEOF",
    "git commit -F - <<'EOF' <<'TWO'\nfix\nEOF\nx\nTWO",
    "git commit -F \"$(pwd)\" <<'EOF'\nfix\nEOF",
    # Never terminated, or not a here-document at all.
    "git commit -F - <<'EOF'\nfix\n",
    "git commit -F - <<'EOF'\nfix\n  EOF",
    "git commit -m one\ngit push origin main",
    "make\nmake install",
    "cat <<< 'here string'",
])
def test_near_misses_of_the_here_document_form_stay_refused(env, command):
    _refused(env[2], command)


# --- exactly: git commit -m "$(cat <<'EOF' ... EOF)" -----------------------------------------

COMMIT = "git commit -m \"$(cat <<'EOF'\nfix: one thing\n\nA body; with | separators & $(things).\nEOF\n)\""


@pytest.mark.parametrize("command", [
    COMMIT,
    COMMIT + "\n",
    "git commit -a -m \"$(cat <<'EOF'\nfix\nEOF\n)\" --no-verify",
    "git commit --amend -m \"$(cat <<'MSG'\nfix\nMSG\n)\"",
    "git commit -m \"$(cat <<'EOF'\nfix\n\nCo-Authored-By: Someone <noreply@example.org>\nEOF\n)\"",
])
def test_a_commit_message_from_cat_of_a_quoted_here_document_runs(env, command):
    _runs(env[2], command)


@pytest.mark.parametrize("command", [
    # Anything extra in the substitution.
    "git commit -m \"$(cat <<'EOF'\nfix\nEOF\n; gh repo delete x)\"",
    "git commit -m \"$(cat <<'EOF'\nfix\nEOF\ngh repo delete x\n)\"",
    "git commit -m \"$(gh repo delete x; cat <<'EOF'\nfix\nEOF\n)\"",
    "git commit -m \"$(cat <<'EOF' | bash\nfix\nEOF\n)\"",
    "git commit -m \"$(cat - <<'EOF'\nfix\nEOF\n)\"",
    "git commit -m \"$(cat /etc/passwd <<'EOF'\nfix\nEOF\n)\"",
    "git commit -m \"$(tac <<'EOF'\nfix\nEOF\n)\"",
    "git commit -m \"$(bash <<'EOF'\nfix\nEOF\n)\"",
    # An unquoted here-document expands its body.
    "git commit -m \"$(cat <<EOF\nfix $(gh repo delete x)\nEOF\n)\"",
    "git commit -m \"$(cat <<EOF\nfix\nEOF\n)\"",
    # Not in double quotes, or a second substitution.
    "git commit -m $(cat <<'EOF'\nfix\nEOF\n)",
    "git commit -m \"$(cat <<'EOF'\nfix\nEOF\n)\" -m \"$(date)\"",
    "git commit -m \"$(cat <<'EOF'\nfix\nEOF\n)\" && gh repo delete x",
    "git commit -m \"$(cat <<'EOF'\nfix\nEOF\n)\"; ls",
    "ls && git commit -m \"$(cat <<'EOF'\nfix\nEOF\n)\"",
    "git commit -m \"$(cat <<'EOF'\nfix\nEOF\n)\"\ngh repo delete x",
    # Exactly `git commit`: not another program, not git with something in front.
    f"gh pr create --repo {M} --body \"$(cat <<'EOF'\nbody\nEOF\n)\"",
    f"gh pr merge 21 --repo {M} --body \"$(cat <<'EOF'\nbody\nEOF\n)\"",
    "git -c core.editor=evil commit -m \"$(cat <<'EOF'\nfix\nEOF\n)\"",
    "git push origin main -o \"$(cat <<'EOF'\nfix\nEOF\n)\"",
    "bash -c \"$(cat <<'EOF'\ngh repo delete x\nEOF\n)\"",
    "env git commit -m \"$(cat <<'EOF'\nfix\nEOF\n)\"",
    "sudo git commit -m \"$(cat <<'EOF'\nfix\nEOF\n)\"",
])
def test_anything_else_around_or_inside_it_stays_refused(env, command):
    _refused(env[2], command)


# --- and the gate's answer --------------------------------------------------------------------

def test_the_gate_lets_the_forms_through_in_silence_and_still_refuses_the_rest(tmp_path):
    config = make_config(tmp_path)
    checkout = tmp_path / "projects" / "demo"
    (checkout / "sub").mkdir()
    import uuid
    turn_id = str(uuid.uuid4())

    def decide(command):
        return decision.decide(
            {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(checkout)},
            config, turn_checkout=checkout, turn_id=turn_id,
            home=tmp_path / "home", lib_dir=tmp_path / "lib")

    for command in ("cd sub && make test", "make test 2>&1 | tail -5", COMMIT,
                    "git commit -F - <<'EOF'\nfix\nEOF"):
        assert decide(command).allow, command
    assert decide("cd $X && make").reason == decision.REASON_NOT_UNDERSTOOD
    assert decide("make | less").reason == decision.REASON_NOT_UNDERSTOOD
    assert decide("make | bash").reason == decision.REASON_RUN_ALONE
    assert decide("cd sub && gh pr merge 1").reason == decision.REASON_RUN_ALONE
    assert decide("gh pr merge 1 | cat").reason == decision.REASON_RUN_ALONE


def test_in_the_confirmation_flow_a_form_never_makes_a_gated_command_pending(tmp_path):
    from tests.gatekit import SHA, Turn
    turn = Turn(tmp_path)
    (tmp_path / "projects" / "demo" / "sub").mkdir()
    merge = f"gh pr merge 21 --repo me/demo --match-head-commit {SHA}"
    for command in (f"cd sub && {merge}", f"{merge} | cat", f"{merge} --body-file - <<'EOF'\ntext\nEOF"):
        result = turn.call(command)
        assert result.reason == decision.REASON_RUN_ALONE, command
    assert turn.state()["pending"] is None
    assert turn.call("cd sub && make test").allow
    assert turn.call(COMMIT).allow
    # The same merge, alone, can then be asked for.
    assert turn.call(merge).reason == decision.REASON_PENDING
