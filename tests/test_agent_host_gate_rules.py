"""One test per gate rule: a match, and a near miss that must not match
(task 1.14a, design D36). Classification only: what the gate answers is in
test_agent_host_gate_entrypoint.py, the shell reading in
test_agent_host_gate_shell.py.
"""
import dataclasses

import pytest

from agent_host import classify, rules as rules_mod
from agent_host.rules import RulesError, load_rules, rules_from_dict
from tests.gatekit import bash, git_init, make_config, make_ctx, run_git

M = "me/demo"  # a repository of the configured own owner
F = "them/demo"  # somebody else's


@pytest.fixture
def ctx(tmp_path):
    return make_ctx(tmp_path)


def _assert(ctx, command, gated, classes=None):
    verdict = bash(command, ctx)
    assert verdict.gated is gated, (command, verdict)
    assert not verdict.run_alone, (command, verdict)
    if classes is not None:
        assert verdict.classes == tuple(classes), (command, verdict)


# --- rules by default: every subcommand that is not a read verb -----------------

GATED_BY_DEFAULT = [
    # (command, classes)
    ("gh issue delete 5 --repo " + M, ["delete"]),
    ("gh gist create --public notes.txt", ["create"]),
    ("kubectl cordon node-1 --context homelab", []),
    ("helm plugin install https://example.invalid/plugin", []),
    ("bao kv put secret/app key=value", []),
    ("vault kv put secret/app key=value", []),
    ("terraform apply", []),
    ("tofu apply", []),
    ("aws s3 rm s3://bucket/key", []),
    ("aws ec2 terminate-instances --instance-ids i-1", []),
    ("gcloud compute instances delete vm-1", []),
    ("az group delete --name rg", []),
    ("doctl compute droplet delete 1", []),
    ("hcloud server delete web", []),
    ("pulumi up", []),
    ("docker rm web", []),
    ("docker compose up -d", []),
    ("argocd app sync web", ["deploy"]),
    ("gh secret set TOKEN --repo " + M, ["account"]),
    ("gh run watch 5 --repo " + M, []),
    ("gh auth token", []),
    ("/usr/local/bin/kubectl cordon node-1", []),
]

NOT_GATED_BY_DEFAULT = [
    "gh pr view 5 --repo " + M,
    "gh pr list",
    "gh pr checks 5",
    "gh pr diff 5",
    "gh search repos jarvis",
    "gh status",
    "gh auth status",
    "kubectl get pods -n web --context homelab",
    "kubectl -n web get pods",
    "kubectl --context=homelab describe pod web",
    "kubectl logs web-1",
    "kubectl rollout status deployment/web",
    "kubectl config view",
    "helm list -n web",
    "helm repo list",
    "helm status web",
    "argocd app get web",
    "argocd app history web",
    "bao read secret/app",
    "bao kv get secret/app",
    "vault read secret/app",
    "terraform version",
    "terraform show",
    "terraform state list",
    "aws s3 ls",
    "aws ec2 describe-instances",
    "aws --region eu-west-1 sts get-caller-identity",
    "gcloud compute instances list",
    "az vm list",
    "doctl compute droplet list",
    "hcloud server list",
    "pulumi stack ls",
    "docker version",
    "docker image ls",
    "docker logs web",
    # No verb at all: the program prints its help.
    "gh",
    "gh pr",
    "kubectl --help",
    "gh --version",
    # Not a program any rule knows.
    "ls -la",
    "pytest -q",
    "git status",
    "make test",
]


@pytest.mark.parametrize("command,classes", GATED_BY_DEFAULT)
def test_a_subcommand_that_is_not_a_read_verb_is_gated(ctx, command, classes):
    _assert(ctx, command, True, classes)


@pytest.mark.parametrize("command", NOT_GATED_BY_DEFAULT)
def test_a_read_verb_is_not_gated(ctx, command):
    _assert(ctx, command, False)


@pytest.mark.parametrize("command", [
    # A read verb as the NAME of something is not the verb.
    "helm uninstall status",
    "kubectl delete pod list",
    "gh issue delete view",
    "gcloud compute instances delete list",
    "aws ls rm",
    "bao kv delete get",
    "docker rm logs",
    "argocd app delete history",
])
def test_a_read_verb_behind_a_writing_verb_does_not_make_the_call_a_read(ctx, command):
    _assert(ctx, command, True)


@pytest.mark.parametrize("command", [
    # An option the rules do not know, before the verb: is `get` the verb or
    # the option's value? Nobody can say, so the call is gated.
    "kubectl --as-uid get delete pod web",
    "kubectl -x get pods",
    "helm --burst-limit get uninstall web",
    "gh --unknown pr view",
])
def test_an_unknown_option_before_the_verb_gates_the_call(ctx, command):
    _assert(ctx, command, True)


def test_a_known_value_option_before_the_verb_is_skipped_with_its_value(ctx):
    # `get` here is the namespace, and `delete` is the verb.
    _assert(ctx, "kubectl -n get delete pod web", True, ["delete"])
    _assert(ctx, "kubectl -n delete get pods", False)


def test_an_unknown_group_is_taken_as_the_verb_and_gated(ctx):
    _assert(ctx, "gh newthing list", True)
    _assert(ctx, "gcloud newservice things list", True)


# --- gh: the work that never needs a yes, on the user's own repositories ------------

@pytest.mark.parametrize("command", [
    f"gh pr create --repo {M} --title x --body y",
    f"gh pr create -R {M} --fill",
    f"gh pr create --repo={M} --fill",
    f"gh pr checkout 5 --repo {M}",
    f"gh pr comment 5 --repo {M} --body ok",
    f"gh issue create --repo {M} --title x",
    f"gh issue comment 5 --repo {M} --body ok",
    f"gh repo clone {M}",
    "gh pr create --repo ME/demo --fill",  # owner names do not depend on case
])
def test_own_repository_work_needs_no_yes(ctx, command):
    _assert(ctx, command, False)


@pytest.mark.parametrize("command,classes", [
    (f"gh pr create --repo {F} --fill", ["message"]),
    (f"gh pr comment 5 --repo {F} --body hi", ["message"]),
    (f"gh issue create --repo {F} --title x", ["message"]),
    (f"gh issue comment 5 --repo {F} --body hi", ["message"]),
    (f"gh pr review 5 --repo {F} --approve", ["message"]),
    (f"gh pr checkout 5 --repo {F}", []),
    (f"gh repo clone {F}", []),
    # No explicit repository: nothing is taken from the checkout.
    ("gh pr create --fill", ["message"]),
    ("gh issue comment 5 --body hi", ["message"]),
    ("gh repo clone demo", []),
    # Not the shape of owner/name.
    ("gh pr create --repo github.com/me/demo --fill", ["message"]),
    ("gh pr create --repo me --fill", ["message"]),
    # Own repository, but not on the list of work that needs no yes.
    (f"gh pr review 5 --repo {M} --approve", []),
    (f"gh pr close 5 --repo {M}", []),
    (f"gh pr ready 5 --repo {M}", []),
])
def test_the_same_work_elsewhere_or_without_a_repository_is_gated(ctx, command, classes):
    _assert(ctx, command, True, classes)


def test_without_configured_owners_no_repository_is_own(tmp_path):
    ctx = make_ctx(tmp_path, config=make_config(tmp_path, own_owners=[]))
    _assert(ctx, f"gh pr create --repo {M} --fill", True, ["message"])


# --- rules that name an action (D36's table) ---------------------------------------

NAMED = [
    ("gh pr merge 21 --repo " + M, ["merge"]),
    ("gh pr merge", ["merge"]),
    ("kubectl apply -f x.yaml", ["deploy"]),
    ("kubectl patch deployment web -p {}", ["deploy"]),
    ("kubectl scale deployment web --replicas=2", ["deploy"]),
    ("kubectl rollout restart deployment/web", ["deploy"]),
    ("kubectl replace -f x.yaml", ["deploy"]),
    ("kubectl edit deployment web", ["deploy"]),
    ("helm install web ./chart", ["deploy"]),
    ("helm upgrade web ./chart", ["deploy"]),
    ("helm rollback web 1", ["deploy"]),
    ("argocd app sync web", ["deploy"]),
    ("ansible-playbook site.yml", ["deploy"]),
    ("ansible all -m ping", ["deploy"]),
    ("gh workflow run build.yml --repo " + M, ["deploy"]),
    ("gh release create v1 --repo " + M, ["deploy"]),
    ("gh repo delete " + M, ["delete"]),
    ("gh repo archive " + M, ["delete"]),
    ("gh release delete v1 --repo " + M, ["delete"]),
    ("gh issue delete 5 --repo " + M, ["delete"]),
    ("kubectl delete pod web -n web --context homelab", ["delete"]),
    ("helm uninstall web", ["delete"]),
    ("argocd app delete web", ["delete"]),
    ("bao kv delete secret/app", ["delete"]),
    ("bao kv destroy secret/app", ["delete"]),
    ("bao token revoke abc", ["delete"]),
    ("bao secrets disable kv", ["delete"]),
    ("bao operator seal", ["delete"]),
    ("vault lease revoke abc", ["delete"]),
    ("kubectl delete pod web --force", ["delete", "force"]),
    ("helm upgrade web ./chart --force", ["deploy", "force"]),
    ("argocd app sync web --force", ["deploy", "force"]),
    ("gh secret set TOKEN", ["account"]),
    ("gh secret delete TOKEN", ["account"]),
    ("gh variable set NAME", ["account"]),
    ("gh variable delete NAME", ["account"]),
    ("gh ssh-key add key.pub", ["account"]),
    ("gh repo deploy-key add key.pub", ["account"]),
    ("gh repo rename new-name", ["account"]),
    ("gh repo edit --visibility public", ["account"]),
    ("gh repo create me/new --private", ["create"]),
    ("gh repo fork them/demo", ["create"]),
    ("gh gist create notes.txt", ["create"]),
    ("mail -s hello someone@example.invalid", ["message"]),
    ("sendmail someone@example.invalid", ["message"]),
    ("msmtp someone@example.invalid", ["message"]),
    ("mutt -s hello someone@example.invalid", ["message"]),
]

NAMED_NEAR_MISSES = [
    "gh pr view 21",
    "gh pr status",
    "kubectl get deployment web",
    "kubectl rollout status deployment/web",
    "kubectl rollout history deployment/web",
    "helm get values web",
    "helm history web",
    "argocd app diff web",
    "ansible-lint site.yml",
    "gh workflow view build.yml",
    "gh workflow list",
    "gh release view v1",
    "gh release list",
    "gh repo view " + M,
    "gh issue view 5",
    "bao kv get secret/app",
    "bao token lookup-self-not-a-command --help",  # an option after the verb changes nothing: gated
    "gh secret list",
    "gh variable list",
    "gh gist list",
    "gh gist view abc",
    "mailcap --help",
    "man mail",
]


@pytest.mark.parametrize("command,classes", NAMED)
def test_a_named_action_is_gated_in_its_class(ctx, command, classes):
    _assert(ctx, command, True, classes)


@pytest.mark.parametrize("command", [c for c in NAMED_NEAR_MISSES if "not-a-command" not in c])
def test_a_near_miss_of_a_named_action_is_not_gated(ctx, command):
    _assert(ctx, command, False)


def test_force_on_a_read_is_not_a_forced_action(ctx):
    _assert(ctx, "kubectl get pods --force", False)


# --- gh api ---------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "gh api -X DELETE repos/me/demo/issues/1",
    "gh api repos/me/demo/issues/1 -X DELETE",
    "gh api -XDELETE repos/me/demo/issues/1",
    "gh api --method DELETE repos/me/demo",
    "gh api --method=PATCH repos/me/demo",
    "gh api -X post repos/me/demo/issues",
    "gh api repos/me/demo/issues -f title=x",
    "gh api repos/me/demo/issues -ftitle=x",
    "gh api repos/me/demo/issues -F count=1",
    "gh api repos/me/demo/issues --field title=x",
    "gh api repos/me/demo/issues --field=title=x",
    "gh api repos/me/demo/issues --raw-field title=x",
    "gh api repos/me/demo/issues --input body.json",
    "gh api repos/me/demo/issues -if title=x",
    "gh api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: \"x\"}) { clientMutationId } }'",
    "gh api repos/me/demo/pulls/1/merge -X PUT",
])
def test_gh_api_with_a_writing_method_or_a_field_is_class_api(ctx, command):
    _assert(ctx, command, True, ["api"])


@pytest.mark.parametrize("command", [
    "gh api repos/me/demo/pulls",
    "gh api -X GET repos/me/demo/pulls",
    "gh api -XGET repos/me/demo/pulls",
    "gh api --method GET repos/me/demo/pulls",
    "gh api repos/me/demo/pulls --paginate --jq .[].number",
    "gh api repos/me/demo/pulls -H 'Accept: application/json' -i",
])
def test_a_plain_gh_api_read_is_not_gated(ctx, command):
    _assert(ctx, command, False)


# --- HTTP clients ------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "curl -d a=b https://example.invalid/x",
    "curl -X DELETE https://example.invalid/x",
    "curl -XDELETE https://example.invalid/x",
    "curl -X GET https://example.invalid/x",  # D36 lists -X itself
    "curl -sSd a=b https://example.invalid/x",
    "curl --data a=b https://example.invalid/x",
    "curl --data-urlencode a=b https://example.invalid/x",
    "curl --data=a=b https://example.invalid/x",
    "curl --dat a=b https://example.invalid/x",
    "curl -F file=@x https://example.invalid/x",
    "curl --form file=@x https://example.invalid/x",
    "curl -T x.txt https://example.invalid/x",
    "curl --upload-file x.txt https://example.invalid/x",
    "curl --json '{}' https://example.invalid/x",
    "curl --request POST https://example.invalid/x",
    "wget --post-data a=b https://example.invalid/x",
    "wget --post-data=a=b https://example.invalid/x",
    "wget --post-file body https://example.invalid/x",
    "wget --post-d a=b https://example.invalid/x",
    "wget --body-data a=b --method PUT https://example.invalid/x",
    "wget --method=DELETE https://example.invalid/x",
    "http POST example.invalid/x",
    "http post example.invalid/x",
    "http delete localhost:8080/x",
    "http PUT example.invalid/x name=value",
    "http example.invalid/x name=value",
    "http example.invalid/x count:=1",
    "http example.invalid/x file@body.txt",
    "https DELETE example.invalid/x",
    "http --form example.invalid/x",
])
def test_an_http_client_that_writes_is_class_api(ctx, command):
    _assert(ctx, command, True, ["api"])


@pytest.mark.parametrize("command", [
    "curl https://example.invalid/x",
    "curl -sSfL https://example.invalid/x",
    "curl -I https://example.invalid/x",
    "curl -o out.html https://example.invalid/x",
    "curl -H 'Accept: text/plain' https://example.invalid/x",
    "curl --head https://example.invalid/x",
    "wget https://example.invalid/x",
    "wget -q -O out.html https://example.invalid/x",
    "http example.invalid/x",
    "http GET example.invalid/x",
    "http HEAD example.invalid/x",
    "http GET example.invalid/x q==term Accept:text/plain",
    "https example.invalid/x",
])
def test_a_plain_http_read_is_not_gated(ctx, command):
    _assert(ctx, command, False)


# --- git push, by its option grammar -------------------------------------------------

@pytest.mark.parametrize("command,classes", [
    ("git push -f origin feature", ["force"]),
    ("git push -fu origin feature", ["force"]),
    ("git push -uf origin feature", ["force"]),
    ("git push --force origin feature", ["force"]),
    ("git push --forc origin feature", ["force"]),
    ("git push --force-with-lease origin feature", ["force"]),
    ("git push --force-with-lease=feature:abc123 origin feature", ["force"]),
    ("git push --force-if-includes origin feature", ["force"]),
    ("git push origin +feature", ["force"]),
    ("git push origin +feature:feature", ["force"]),
    ("git push --mirror origin", ["force"]),
    ("git push --mir origin", ["force"]),
    ("git push --delete origin feature", ["delete"]),
    ("git push --del origin feature", ["delete"]),
    ("git push -d origin feature", ["delete"]),
    ("git push origin :feature", ["delete"]),
    ("git push --prune origin", ["delete"]),
    ("git push origin --delete feature", ["delete"]),
    ("git push --all origin", []),
    ("git push --tags origin", []),
    ("git push --tag origin", []),
    ("git push --branches origin", []),
    ("git push origin main", ["merge"]),
    ("git push origin feature:main", ["merge"]),
    ("git push origin feature:refs/heads/main", ["merge"]),
    ("git push origin HEAD:master", ["merge"]),
    ("git push -u origin feature:main", ["merge"]),
    ("git push origin feature other:main", ["merge"]),
    ("git push origin +feature:main", ["merge", "force"]),
    ("git push -f origin main", ["merge", "force"]),
    ("git push origin :main", ["delete"]),
    ("git -C /srv/repo push origin main", ["merge"]),
    ("git -c http.proxy=x push origin main", ["merge"]),
    ("git --no-pager push origin main", ["merge"]),
    ("/usr/bin/git push origin main", ["merge"]),
    # git's own options that the gate cannot place, with a push behind them.
    ("git --unknown-option push origin feature", []),
    # An alias defined on the command line that is a push.
    ("git -c alias.p=push p origin main", []),
    ("git -c alias.p='push -f' p", []),
])
def test_a_push_is_classified_by_git_option_grammar(ctx, command, classes):
    _assert(ctx, command, True, classes)


@pytest.mark.parametrize("command", [
    "git push origin feature",
    "git push -u origin feature",
    "git push --set-upstream origin feature",
    "git push origin feature:feature",
    "git push origin feature:refs/heads/feature",
    "git push origin fix-main",
    "git push origin feature/main",
    "git push origin refs/tags/v1",
    "git push origin tag v1",
    "git push --no-verify origin feature",
    "git push --follow-tags origin feature",
    "git push --dry-run origin feature",
    "git push -o ci.skip origin feature",
    "git push --push-option ci.skip origin feature",
    "git push --no-force-with-lease origin feature",
    "git commit -m 'push -f to main'",
    "git log --all",
    "git fetch --prune --tags",
    "git branch -d feature",
    "git stash push -m wip",
    "git -c alias.l=log l",
    "git -c user.name=x commit -m y",
])
def test_a_push_of_a_feature_branch_and_other_git_work_is_not_gated(ctx, command):
    _assert(ctx, command, False)


def _repo_with_remote(tmp_path, *, branch, upstream=True):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    run_git(remote, "init", "-q", "--bare", "-b", "main")
    work = tmp_path / "work"
    git_init(work, branch="main")
    run_git(work, "remote", "add", "origin", str(remote))
    run_git(work, "push", "-q", "-u", "origin", "main")
    if branch != "main":
        run_git(work, "checkout", "-q", "-b", branch)
        if upstream:
            run_git(work, "push", "-q", "-u", "origin", branch)
    return work


def test_a_bare_push_on_a_default_branch_is_a_merge(tmp_path):
    work = _repo_with_remote(tmp_path, branch="main")
    ctx = make_ctx(tmp_path, cwd=work, git=classify.run_git)
    _assert(ctx, "git push", True, ["merge"])
    _assert(ctx, "git push origin", True, ["merge"])
    _assert(ctx, "git push origin HEAD", True, ["merge"])


def test_a_bare_push_on_a_feature_branch_is_not_gated(tmp_path):
    work = _repo_with_remote(tmp_path, branch="feature")
    ctx = make_ctx(tmp_path, cwd=work, git=classify.run_git)
    _assert(ctx, "git push", False)
    _assert(ctx, "git push origin", False)
    _assert(ctx, "git push origin HEAD", False)


def test_a_bare_push_that_cannot_be_resolved_is_gated(tmp_path):
    # No upstream: where it lands is a guess, and the gate does not guess.
    work = _repo_with_remote(tmp_path, branch="feature", upstream=False)
    ctx = make_ctx(tmp_path, cwd=work, git=classify.run_git)
    _assert(ctx, "git push", True, [])
    # Outside any repository.
    _assert(make_ctx(tmp_path, cwd=tmp_path), "git push", True, [])
    # A `git` that cannot be run at all.
    _assert(make_ctx(tmp_path, cwd=work), "git push", True, [])
    _assert(make_ctx(tmp_path, cwd=work), "git push origin HEAD", True, [])


def test_a_bare_push_is_gated_when_configuration_decides_where_it_goes(tmp_path):
    work = _repo_with_remote(tmp_path, branch="feature")
    ctx = make_ctx(tmp_path, cwd=work, git=classify.run_git)
    _assert(ctx, "git push", False)
    for key, value in (("push.default", "matching"), ("remote.origin.push", "HEAD:main"),
                       ("remote.origin.mirror", "true")):
        run_git(work, "config", key, value)
        _assert(ctx, "git push", True, [])
        run_git(work, "config", "--unset", key)
    # Another repository or another remote than the branch's own.
    _assert(ctx, "git -C /somewhere/else push", True, [])
    _assert(ctx, "git push --repo=elsewhere", True, [])
    _assert(ctx, "git push elsewhere", True, [])


def test_the_default_branch_of_the_remote_counts_even_under_another_name(tmp_path):
    work = _repo_with_remote(tmp_path, branch="main")
    run_git(work, "checkout", "-q", "-b", "trunk")
    run_git(work, "push", "-q", "-u", "origin", "trunk")
    run_git(work, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    ctx = make_ctx(tmp_path, cwd=work, git=classify.run_git)
    _assert(ctx, "git push origin feature:trunk", True, ["merge"])
    _assert(ctx, "git push", True, ["merge"])
    # Without a readable remote HEAD only the configured names count.
    _assert(make_ctx(tmp_path, cwd=work), "git push origin feature:trunk", False)


# --- scp, rsync, sftp ---------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "scp notes.txt web.example.invalid:/srv/",
    "scp deploy@web.example.invalid:/srv/x .",
    "scp -P 2222 -i key notes.txt web:/srv/",
    "scp -r dir [2001:db8::1]:/srv/",
    "rsync -av dir/ web.example.invalid:/srv/dir/",
    "rsync -av -e 'ssh -p 2222' web:/srv/dir/ dir/",
    "rsync -av dir/ rsync://web.example.invalid/module/",
    "rsync -av dir/ web::module/",
    "sftp web.example.invalid",
    "sftp -b batch.txt deploy@web.example.invalid",
])
def test_a_copy_to_or_from_another_host_is_class_outward(ctx, command):
    _assert(ctx, command, True, ["outward"])


@pytest.mark.parametrize("command", [
    "scp notes.txt copy.txt",
    "scp -r dir/ /srv/backup/",
    "rsync -av dir/ /srv/backup/dir/",
    "rsync -av --delete ./a:b/ other/",
    "cp notes.txt copy.txt",
])
def test_a_local_copy_is_not_outward(ctx, command):
    _assert(ctx, command, False)


def test_the_hosts_of_a_copy_are_found_as_written(ctx):
    assert classify.remote_hosts("scp", ["-P", "22", "a", "deploy@web.example.invalid:/srv"], ctx) \
        == ["web.example.invalid"]
    assert classify.remote_hosts("scp", ["-3", "one:/a", "two:/b"], ctx) == ["one", "two"]
    assert classify.remote_hosts("sftp", ["web"], ctx) == ["web"]
    assert classify.remote_hosts("rsync", ["a", "b"], ctx) == []


# --- rm -r outside the worktree and the temp directory ------------------------------------

def test_rm_r_outside_the_worktree_is_class_delete(tmp_path):
    work = tmp_path / "area" / "work"  # `..` must not be a tree that holds Jarvis's own rules
    git_init(work)
    (work / "sub").mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    ctx = make_ctx(tmp_path, cwd=work, temp_roots=[scratch])

    for command in (
        f"rm -r {tmp_path / 'elsewhere'}",
        f"rm -rf {tmp_path / 'elsewhere'}",
        f"rm -fR {tmp_path / 'elsewhere'}",
        f"rm --recursive {tmp_path / 'elsewhere'}",
        "rm -rf ..",
        "rm -rf ../*",
        "rm -rf build ../other",
        "rm -rf ~/projects",
        "rm -rf $TARGET",
        "rm -rf -- -weird ../other",
    ):
        _assert(ctx, command, True, ["delete"])

    for command in (
        "rm -rf build",
        "rm -r ./sub",
        "rm -rf sub/*",
        f"rm -rf {work / 'sub'}",
        f"rm -rf {scratch / 'x'}",
        "rm notes.txt",
        f"rm -f {tmp_path / 'elsewhere' / 'file'}",  # not recursive
        "rm --force --verbose ../file",
    ):
        _assert(ctx, command, False)


def test_rm_r_through_a_symlink_out_of_the_worktree_is_gated(tmp_path):
    work = tmp_path / "work"
    git_init(work)
    outside = tmp_path / "outside"
    outside.mkdir()
    (work / "link").symlink_to(outside)
    ctx = make_ctx(tmp_path, cwd=work)
    _assert(ctx, "rm -rf link/data", True, ["delete"])


def test_rm_r_with_no_worktree_around_it_is_gated(tmp_path):
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    _assert(make_ctx(tmp_path, cwd=lonely), "rm -rf build", True, ["delete"])


# --- root, in any position -----------------------------------------------------------------

@pytest.mark.parametrize("command,classes", [
    ("sudo systemctl restart web", ["root"]),
    ("sudo -u postgres psql", ["root"]),
    ("sudo -i", ["root"]),
    ("su -", ["root"]),
    ("su -c 'systemctl restart web' root", ["root"]),
    ("pkexec systemctl restart web", ["root"]),
    ("doas systemctl restart web", ["root"]),
    ("/usr/bin/sudo ls", ["root"]),
    # sudo in second position, and further back.
    ("time sudo systemctl restart web", ["root"]),
    ("env sudo systemctl restart web", ["root"]),
    ("FOO=1 sudo systemctl restart web", ["root"]),
    ("nice -n 5 sudo systemctl restart web", ["root"]),
    ("watchdog sudo systemctl restart web", ["root"]),
    # The inner command keeps its own classes.
    ("sudo kubectl delete pod web", ["root", "delete"]),
    ("sudo -u deploy helm upgrade web ./chart", ["root", "deploy"]),
    ("sudo bash -c 'kubectl delete pod web'", ["root", "delete"]),
    ("sudo kubectl get pods", ["root"]),
])
def test_running_as_another_user_is_class_root_in_any_position(ctx, command, classes):
    _assert(ctx, command, True, classes)


@pytest.mark.parametrize("command", [
    "pseudo ls",
    "sudoku --new",
    "ls /usr/share/doc/sudo/",
    "grep 'use sudo here' notes.txt",
    "git commit -m 'no sudo needed'",
    "cat sudoers.example",
])
def test_a_word_that_only_looks_like_sudo_is_not_root(ctx, command):
    _assert(ctx, command, False)


# --- connector tools -------------------------------------------------------------------------

@pytest.mark.parametrize("tool", [
    "mcp__x__send_thing", "mcp__x__post_comment", "mcp__mail__reply", "mcp__mail__forward",
    "mcp__mail__delete_draft", "mcp__mail__trash_thread", "mcp__docs__publish",
    "mcp__docs__share", "mcp__chat__message_user", "mcp__files__upload", "mcp__org__invite",
    "mcp__X__SEND_Thing", "mcp__postbox__list",
])
def test_a_connector_tool_whose_name_says_it_sends_is_gated(ctx, tool):
    verdict = classify.classify(tool, {"anything": 1}, ctx)
    assert verdict.gated
    assert verdict.classes == ()


@pytest.mark.parametrize("tool", [
    "mcp__x__get_thing", "mcp__mail__search_threads", "mcp__docs__read", "mcp__x__list_labels",
    "send_thing", "Read", "Grep", "Glob", "WebFetch", "Task",
])
def test_a_connector_tool_that_reads_and_a_tool_that_is_no_connector_are_not_gated(ctx, tool):
    assert not classify.classify(tool, {"anything": 1}, ctx).gated


# --- the rules as data ---------------------------------------------------------------------

def test_the_repository_rules_load_and_hold_the_programs_of_d36(tmp_path):
    rules = load_rules(None)
    for program in ("kubectl", "helm", "argocd", "bao", "vault", "gh", "docker", "terraform",
                    "tofu", "aws", "gcloud", "az", "doctl", "hcloud", "pulumi"):
        assert program in rules.programs
    kubectl = rules.programs["kubectl"]
    for verb in ("get", "describe", "logs", "list", "ls", "view", "status", "diff", "top",
                 "version", "show", "search", "checks", "history", "explain"):
        assert verb in kubectl.read_verbs
    assert "read" in rules.programs["bao"].read_verbs
    assert "read" not in kubectl.read_verbs
    assert set(rules.mcp_words) == {"send", "reply", "forward", "delete", "trash", "publish",
                                    "share", "post", "message", "comment", "upload", "invite"}
    assert rules.root_programs == {"sudo", "su", "pkexec", "doas"}


def test_host_local_rules_are_merged_on_top_of_the_repository_rules(tmp_path):
    rules_dir = tmp_path / "rules.d"
    rules_dir.mkdir()
    (rules_dir / "10-own.toml").write_text(
        '[[actions]]\nclass = "message"\nprograms = ["ping.sh"]\n\n'
        '[[actions]]\nclass = "deploy"\nprograms = ["factory"]\npaths = [["build"]]\n\n'
        '[programs.kubectl]\nvalue_options = ["--as-uid"]\n'
    )
    (rules_dir / "notes.txt").write_text("not a rules file")
    config = make_config(tmp_path, rules_dir=str(rules_dir))
    ctx = make_ctx(tmp_path, config=config)

    _assert(ctx, "ping.sh hello", True, ["message"])
    _assert(ctx, "factory build jarvis", True, ["deploy"])
    _assert(ctx, "factory status", False)
    _assert(ctx, "kubectl --as-uid 5 get pods", False)
    # The repository's own rules are still there.
    _assert(ctx, "gh pr merge 1", True, ["merge"])
    _assert(ctx, "kubectl -n web get pods", False)
    # Without the host-local file none of this is known.
    plain = make_ctx(tmp_path, config=make_config(tmp_path, rules_dir=str(tmp_path / "none")))
    _assert(plain, "ping.sh hello", False)


@pytest.mark.parametrize("content", [
    "this is not toml [",
    'version = 2\n',
    '[[actions]]\nclass = "nonsense"\nprograms = ["x"]\n',
    '[[actions]]\nclass = "deploy"\n',
    '[programs.kubectl]\ngroups = "rollout"\n',
    '[programs.kubectl]\nunknown_key = 1\n',
    'read_verbs = [1, 2]\n',
    'mcp = "send"\n',
    'surprise = true\n',
])
def test_a_broken_host_local_rules_file_is_an_error_not_an_empty_rule_set(tmp_path, content):
    rules_dir = tmp_path / "rules.d"
    rules_dir.mkdir()
    (rules_dir / "broken.toml").write_text(content)
    with pytest.raises(RulesError):
        load_rules(rules_dir)


def test_a_rules_file_that_cannot_be_read_is_an_error(tmp_path):
    rules_dir = tmp_path / "rules.d"
    rules_dir.mkdir()
    (rules_dir / "dangling.toml").symlink_to(tmp_path / "nowhere")
    with pytest.raises(RulesError):
        load_rules(rules_dir)
    with pytest.raises(RulesError):
        load_rules(None, base_path=tmp_path / "missing.toml")
    not_a_dir = tmp_path / "file"
    not_a_dir.write_text("")
    with pytest.raises(RulesError):
        load_rules(not_a_dir)


def test_a_missing_rules_directory_is_just_the_repository_rules(tmp_path):
    assert load_rules(tmp_path / "nope").programs.keys() == load_rules(None).programs.keys()


def test_rules_without_the_essentials_are_refused():
    import tomllib
    base = tomllib.loads(rules_mod.BASE_RULES_PATH.read_text())
    for key in ("read_verbs", "programs", "root", "mcp"):
        broken = {k: v for k, v in base.items() if k != key}
        with pytest.raises(RulesError):
            rules_from_dict(broken)


def test_an_action_matches_by_path_prefix_or_by_verb():
    action = rules_mod.Action(cls="delete", programs=frozenset({"bao"}),
                              paths=(("operator",),), verbs=frozenset({"revoke"}))
    assert action.matches("bao", ("operator", "seal"))
    assert action.matches("bao", ("token", "revoke"))
    assert not action.matches("bao", ("token", "lookup"))
    assert not action.matches("vault", ("operator", "seal"))
    assert not action.matches("bao", ())
    every = dataclasses.replace(action, paths=(), verbs=frozenset())
    assert every.matches("bao", ())
