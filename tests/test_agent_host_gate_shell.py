"""How the gate reads a `Bash` command (tasks 1.7, 1.8a, 1.14a; design D36):
unwrapping of `ssh`, `bash -c`, `sh -c` and `sudo`, and the refusal of a
gated command that is not run alone.
"""
import pytest

from agent_host import shell
from tests.gatekit import bash, make_ctx

M = "me/demo"


@pytest.fixture
def ctx(tmp_path):
    return make_ctx(tmp_path)


def _analyse(command, ctx):
    rules = ctx.rules
    return shell.analyse(
        command, known_programs=rules.known_programs, root_programs=rules.root_programs,
        shells=rules.shells, indirect=rules.indirect,
    )


# --- unwrapping: the inner command is judged by the same rules ---------------------

@pytest.mark.parametrize("command,classes", [
    ("ssh jumpy kubectl delete pod web -n web --context homelab", ["delete"]),
    ("ssh jumpy 'kubectl delete pod web -n web'", ["delete"]),
    ("ssh -p 2222 -i key deploy@jumpy kubectl apply -f x.yaml", ["deploy"]),
    ("ssh -o StrictHostKeyChecking=yes jumpy helm upgrade web ./chart", ["deploy"]),
    ("ssh jumpy sudo systemctl restart web", ["root", "outward"]),
    ("ssh jumpy -- kubectl delete pod web", ["delete"]),
    ("ssh jumpy -t 'kubectl delete pod web'", ["delete"]),
    ("ssh -t jumpy -p 2222 kubectl delete pod web", ["delete"]),
    ("ssh jumpy \"bash -c 'kubectl delete pod web'\"", ["delete"]),
    ("bash -c 'gh pr merge 1 --repo me/demo'", ["merge"]),
    ("sh -c 'gh pr merge 1 --repo me/demo'", ["merge"]),
    ("bash -lc 'gh repo delete me/demo'", ["delete"]),
    ("bash -o pipefail -c 'kubectl apply -f x.yaml'", ["deploy"]),
    ("zsh -c 'helm uninstall web'", ["delete"]),
    ("bash -c \"sh -c 'gh pr merge 1'\"", ["merge"]),
    ("sudo ssh jumpy kubectl delete pod web", ["root", "delete"]),
    ("env KUBECONFIG=/x kubectl delete pod web", ["delete"]),
    ("KUBECONFIG=/x kubectl delete pod web", ["delete"]),
    ("nohup kubectl apply -f x.yaml", ["deploy"]),
    ("timeout 30 kubectl apply -f x.yaml", ["deploy"]),
    ("timeout -k 5 30 kubectl apply -f x.yaml", ["deploy"]),
    ("command gh pr merge 1", ["merge"]),
    ("exec gh pr merge 1", ["merge"]),
    ("! gh pr merge 1", ["merge"]),
    ("\"gh\" pr merge 1", ["merge"]),
    ("g'h' pr merge 1", ["merge"]),
    ("\\gh pr merge 1", ["merge"]),
])
def test_a_wrapped_command_is_judged_like_the_command_itself(ctx, command, classes):
    verdict = bash(command, ctx)
    assert verdict.gated, (command, verdict)
    assert not verdict.run_alone, (command, verdict)
    assert verdict.classes == tuple(classes), (command, verdict)


@pytest.mark.parametrize("command", [
    "ssh jumpy kubectl get pods -n web",
    "ssh jumpy 'kubectl get pods -n web'",
    "ssh jumpy",
    "ssh jumpy -t",
    "ssh jumpy -- kubectl get pods",
    "ssh -p 2222 jumpy uptime",
    "bash -c 'kubectl get pods'",
    "bash -c 'ls -la'",
    "bash script.sh",
    "sh -x script.sh",
    "env FOO=1 pytest -q",
    "timeout 30 kubectl get pods",
    "command -v gh",
    "nohup pytest -q",
])
def test_a_wrapped_read_is_not_gated(ctx, command):
    assert not bash(command, ctx).gated, command


def test_the_wrappers_of_the_inner_command_are_kept(ctx):
    analysis = _analyse("sudo -u deploy ssh -p 22 jumpy \"bash -c 'kubectl delete pod web'\"", ctx)
    assert not analysis.compound
    (command,) = analysis.commands
    assert command.argv == ("kubectl", "delete", "pod", "web")
    assert command.wrappers == (("root", "sudo"), ("ssh", "jumpy"), ("shell", "bash"))
    assert command.program == "kubectl"


def test_the_verdict_carries_the_innermost_command(ctx):
    verdict = bash("ssh jumpy kubectl delete pod web", ctx)
    assert verdict.command.argv == ("kubectl", "delete", "pod", "web")
    assert verdict.command.wrappers == (("ssh", "jumpy"),)


def test_ssh_judges_the_string_the_remote_shell_will_read(ctx):
    # ssh joins its arguments with spaces; the remote shell splits them again.
    verdict = bash("ssh jumpy kubectl 'delete pod' web", ctx)
    assert verdict.gated and verdict.classes == ("delete",)


# --- run it alone ----------------------------------------------------------------------

RUN_ALONE = [
    # A compound command.
    f"gh pr merge 1 --repo {M} && gh repo delete {M}",
    f"gh pr merge 1 --repo {M}; echo done",
    f"echo start; gh pr merge 1 --repo {M}",
    f"gh pr merge 1 --repo {M} || true",
    f"gh pr merge 1 --repo {M} | tee log",
    f"gh pr merge 1 --repo {M} &",
    f"cd /srv/repo && gh pr merge 1 --repo {M}",
    "git add -A && git commit -m x && git push origin main",
    # Behind a shell keyword.
    "if gh pr merge 1; then echo ok; fi",
    "while true; do kubectl delete pod web; done",
    "for p in a b; do kubectl delete pod $p; done",
    # Substitutions and newlines.
    f"gh pr merge $(cat number) --repo {M}",
    f"gh pr merge `cat number` --repo {M}",
    f"echo $(gh pr merge 1 --repo {M})",
    f"diff <(gh repo delete {M}) other",
    f"echo ok\ngh pr merge 1 --repo {M}",
    f"gh pr merge 1 --repo {M}\necho ok",
    # Inside a nested shell or an ssh argument.
    "bash -c 'gh pr merge 1; gh repo delete x'",
    "ssh jumpy 'gh pr merge 1; gh repo delete x'",
    "ssh jumpy 'kubectl delete pod web && echo done'",
    "bash -c 'kubectl get pods | xargs kubectl delete pod'",
    "sudo bash -c 'kubectl delete pod a; kubectl delete pod b'",
    # A separator inside a quoted argument.
    f"gh pr merge 1 --repo {M} --subject 'one; two'",
    f"gh pr merge 1 --repo {M} --body 'a && b'",
    f"gh pr merge 1 --repo {M} --body 'a | b'",
    f"gh pr merge 1 --repo {M} --body \"$(cat body.md)\"",
    "kubectl delete pod web --context homelab -l 'app in (a;b)'",
    # eval, xargs, source and other programs that run their arguments.
    f"eval gh pr merge 1 --repo {M}",
    f"eval 'gh pr merge 1 --repo {M}'",
    "xargs kubectl delete pod",
    "xargs -n1 gh issue delete",
    "source deploy.sh kubectl apply -f x.yaml",
    "find . -name '*.yaml' -exec kubectl apply -f {} +",
    "watch -n 5 kubectl delete pod web",
    "env --split-string 'gh pr merge 1'",
    # A subshell, a group or a redirection in front of the program.
    f"(gh pr merge 1 --repo {M})",
    f">out.txt gh pr merge 1 --repo {M}",
    f"2>err.txt gh pr merge 1 --repo {M}",
    # Running as root, anywhere in a compound command.
    "echo hello; sudo systemctl restart web",
    "ls | sudo tee /etc/thing",
    # An unbalanced quote: not one command the gate can read.
    f"gh pr merge 1 --repo {M} --body 'unfinished",
]


@pytest.mark.parametrize("command", RUN_ALONE)
def test_a_gated_command_that_is_not_run_alone_is_refused_as_such(ctx, command):
    verdict = bash(command, ctx)
    assert verdict.gated, (command, verdict)
    assert verdict.run_alone, (command, verdict)


NOT_THE_GATES_BUSINESS = [
    # Every piece is a known-harmless read program.
    "ls | wc -l",
    "git status && git diff",
    "cat notes.txt | grep -n todo | sort | uniq | head -5",
    "cd /srv/repo && ls -la",
    "grep -rn docker docs/ | head",
    "ls -la 2>/dev/null | wc -l",
    "ls -la 2>/dev/null; pwd",          # right before a separator (#59)
    "cd /srv/repo >/dev/null&& ls",
    "git log --oneline | head -20",
    "(cd sub && ls)",
    # ... or, in a call without any quoting, a read of a default-gated program.
    "kubectl get pods -n web | grep web",
    "gh pr view 5 --json state | jq .state",
    "kubectl get pods -n web --context homelab | wc -l",
]


@pytest.mark.parametrize("command", NOT_THE_GATES_BUSINESS)
def test_a_compound_command_of_harmless_reads_runs(ctx, command):
    verdict = bash(command, ctx)
    assert not verdict.gated, (command, verdict)
    assert not verdict.run_alone


# What the gate cannot fully understand does not get through: a compound
# command with any piece that is not a known-harmless read is refused,
# whether or not a rule knows what is in it.
NOT_UNDERSTOOD = [
    'echo "gh repo delete x" | bash',
    "cd / && rm -rf etc",
    "cd ../other && git push",
    "cd /srv/repo && pytest -q",
    "for f in *.py; do wc -l $f; done",
    "source .venv/bin/activate && pytest -q",
    "find . -name '*.py' | xargs wc -l",
    "git add -A && git commit -m 'fix: one; two'",
    "make test && make lint",
    "cat notes.txt | sh",
    "cat notes.txt | python3",
    "ls | awk '{print $1}'",
    "ls; ./deploy.sh",
    "true && mystery-program",
    "echo $(date)",
    "echo 'unfinished",
    # A read of a default-gated program counts only in a call without quoting.
    'kubectl -n "x get" delete pod y | cat',
    'aws --profile "p ec2 describe-instances" s3 rm s3://b/k | cat',
    # Programs that run other commands, alone or not.
    "eval \"$(ssh-agent)\"",
    "eval true",
    "xargs ls",
    "source ./env.sh",
    ". ./env.sh",
    "trap 'gh repo delete x' EXIT",
    "trap 'echo bye' EXIT",
    "coproc gh repo delete x",
    "watch -n 5 ls",
    "chroot /srv/root ls",
    "busybox sh",
    # A wrapper the gate does not follow, or an option of one it does not know.
    "env -S 'ls -la'",
    "env --split-string='gh repo delete x'",
    "exec --weird gh pr view 1",
    "timeout notaduration ls",
    "stdbuf --weird ls",
    "sudo --unknown-option ls",
    # Here-documents, here-strings, process substitution, ANSI-C and locale quoting.
    "bash <<< 'gh repo delete x'",
    "bash <<EOF\ngh repo delete x\nEOF",
    "python3 <<EOF\nprint(1)\nEOF",
    "tee >(gh repo delete x)",
    "$'gh' repo delete x",
    "gh repo delete $'x'",
    "$'\\x67h' repo delete x",
    '$"gh" repo delete x',
    # A program no rule knows, with one the rules know among its arguments.
    "watchdog sudo systemctl restart web",
    "runner gh repo delete x",
    "mystery kubectl delete pod web",
    "man mail",
    "find . -name '*.yaml' -exec cat {} +",
    "find . -type f -ok rm {} +",
]


@pytest.mark.parametrize("text,only_quoted", [
    ("grep -E 'a|b' f", True),
    ('git commit -m "a; b & c"', True),
    ("echo a\\;b", True),
    ('echo "a \\" ; b"', True),
    ("ls; pwd", False),
    ("ls 'a' | wc", False),
    ("echo 'a' ; echo 'b'", False),
    ('echo "a" && echo b', False),
    ("echo 'unfinished ; x", False),
    ('echo "$(date); x"', False),
    ("echo '`date`;'", False),
    ("echo 'a\nb;'", False),
    ("echo $'a;b'", False),
    ("cat <<'EOF'", False),
    ("echo 'a'\\''; evil'", True),
    ("echo 'a' \\; evil", True),
    ("echo 'a';evil", False),
    ('echo "a\\\\";evil', False),
])
def test_separators_only_inside_quotes(text, only_quoted):
    assert shell.separators_only_inside_quotes(text) is only_quoted


@pytest.mark.parametrize("command", NOT_UNDERSTOOD)
def test_a_command_the_gate_cannot_fully_understand_is_refused(ctx, command):
    verdict = bash(command, ctx)
    assert verdict.gated, (command, verdict)
    assert verdict.run_alone, (command, verdict)


@pytest.mark.parametrize("command", [
    "find . -name '*.py'",
    "find /srv -type f -newer x",
    "pytest -q 2>&1",
    "make test 2>/dev/null",
    "git commit -m 'fix one thing (and another) > before'",
    "which gh",
    "grep -rn kubectl docs/",
    "command -v kubectl",
    "ls --color=auto gh",
    "ls > listing.txt",          # a redirection in one plain command is no second command
    # One command with a separator character inside a quoted argument.
    "git commit -m 'fix: one; two'",
    "git commit -m 'a & b | c'",
    "grep -E 'a|b' notes.txt",
    "jq '.a | .b' data.json",
    "grep 'a;b' notes.txt",
    'git commit -m "it\'s a; b"',
    "pytest -k 'one or two' -q",
    "echo a\\;b",
])
def test_one_plain_command_no_rule_gates_still_runs(ctx, command):
    assert not bash(command, ctx).gated, command


def test_a_single_gated_command_is_gated_but_not_refused_as_compound(ctx):
    verdict = bash(f"gh pr merge 1 --repo {M} --subject 'one, two (three) > four'", ctx)
    assert verdict.gated and not verdict.run_alone
    assert verdict.classes == ("merge",)


def test_a_graphql_mutation_is_one_command_despite_its_braces(ctx):
    verdict = bash("gh api graphql -f query='mutation { mergePullRequest(input: {}) { id } }'", ctx)
    assert verdict.gated and not verdict.run_alone
    assert verdict.classes == ("api",)


# --- the pieces --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "a; b", "a && b", "a || b", "a | b", "a &", "a $(b)", "a <(b)", "a `b`", "a\nb", "a\rb",
    "a ';' b", "a \"$(b)\"",
])
def test_every_separator_of_d36_is_found_also_inside_quotes(text):
    assert shell.has_separator(text)


@pytest.mark.parametrize("text", [
    "a b c", "a 'b c'", "a (b)", "a > b", "a < b", "a $b", "a ${b}", "a {b,c}", "a --x=y",
])
def test_what_is_not_a_separator(text):
    assert not shell.has_separator(text)


@pytest.mark.parametrize("argv,rest,ok", [
    (["FOO=1", "BAR=2", "gh", "x"], ["gh", "x"], True),
    (["env", "-i", "FOO=1", "gh"], ["gh"], True),
    (["env", "-u", "HOME", "gh"], ["gh"], True),
    (["env", "--chdir=/x", "gh"], ["gh"], True),
    (["env", "-S", "gh x"], ["env", "-S", "gh x"], False),
    (["exec", "-a", "name", "gh"], ["gh"], True),
    (["exec", "--weird", "gh"], ["exec", "--weird", "gh"], False),
    (["stdbuf", "-oL", "-e", "0", "gh"], ["gh"], True),
    (["time", "-f", "%e", "-o", "t.txt", "gh"], ["gh"], True),
    (["ionice", "-c", "3", "-n7", "gh"], ["gh"], True),
    (["nice", "--adjustment=5", "gh"], ["gh"], True),
    (["timeout", "--kill-after=5", "-s", "KILL", "1.5m", "gh"], ["gh"], True),
    (["timeout", "gh", "pr"], ["timeout", "gh", "pr"], False),
    (["env", "-u", "A", "B=1", "-C", "/x", "gh"], ["gh"], True),
    (["command", "-v", "gh"], [], True),
    (["command", "-p", "gh"], ["gh"], True),
    (["nohup", "--weird", "gh"], ["nohup", "--weird", "gh"], False),
    (["time", "-p", "gh"], ["gh"], True),
    (["nice", "-n", "5", "gh"], ["gh"], True),
    (["nice", "-5", "gh"], ["gh"], True),
    (["timeout", "--signal", "KILL", "5s", "gh"], ["gh"], True),
    (["nohup", "env", "A=1", "time", "gh", "pr"], ["gh", "pr"], True),
    (["gh", "FOO=1"], ["gh", "FOO=1"], True),
    ([], [], True),
])
def test_assignments_and_transparent_prefixes_are_dropped(argv, rest, ok):
    assert shell.strip_prefixes(argv) == (rest, ok)


def test_lenient_segments_cut_at_every_separator_and_ignore_quotes():
    assert shell.lenient_segments("bash -c 'gh pr merge 1; gh repo delete x'") == [
        ["bash", "-c", "gh", "pr", "merge", "1"], ["gh", "repo", "delete", "x"],
    ]
    assert shell.lenient_segments("a $(b c) > d") == [["a"], ["b", "c"], ["d"]]
    assert shell.lenient_segments("  ") == []


def test_an_empty_command_is_nothing(ctx):
    assert not bash("", ctx).gated
    assert not bash("   ", ctx).gated
    assert not bash("FOO=1", ctx).gated


def test_nesting_deeper_than_the_limit_is_not_followed_as_one_command(ctx):
    command = "kubectl delete pod web"
    for _ in range(12):
        command = "bash -c " + __import__("shlex").quote(command)
    verdict = bash(command, ctx)
    assert verdict.gated  # still found, as a candidate
    assert verdict.run_alone


@pytest.mark.parametrize("text, reduced", [
    ("cd x 2>/dev/null; pwd", "cd x  ; pwd"),
    ("cd x 2>/dev/null|| cd y", "cd x  || cd y"),
    ("ls >/dev/null&& ls", "ls  && ls"),
    ("(ls 2>&1)", "(ls  )"),
    # Not a redirection to /dev/null: left alone.
    ("ls >/dev/nullx", "ls >/dev/nullx"),
    ("ls 2>/dev/null>x", "ls 2>/dev/null>x"),
    ("ls x>/dev/null", "ls x>/dev/null"),
])
def test_benign_redirects_are_taken_out_up_to_a_separator(text, reduced):
    assert shell.without_benign_redirects(text) == reduced


@pytest.mark.parametrize("text", [
    "echo x >\v/dev/null ~/.bashrc", "echo x >/dev/null\v~/.bashrc", "echo x >\f/dev/null",
    "echo x >\u00a0/dev/null", "echo x >/dev/null\x1c",
])
def test_only_bash_word_separators_make_a_redirect_benign(text):
    # bash splits words on space, tab and newline only (review of #63)
    assert shell.without_benign_redirects(text) == text
