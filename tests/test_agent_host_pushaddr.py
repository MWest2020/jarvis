"""Where a push really goes, which address names a repository, and the hash
that binds a confirmation to it (tasks 1.7e, 1.8, 1.14; design D36). Real
`git` on throwaway repositories; nothing is ever pushed anywhere."""
import pytest

from agent_host import classify, gatehash, pushaddr
from agent_host.naming import Missing, Named
from tests.gatekit import derive, git_init, make_config, make_ctx, run_git

HOST = "github.com"


@pytest.fixture
def repo(tmp_path):
    work = tmp_path / "work"
    git_init(work)
    run_git(work, "remote", "add", "origin", "git@github.com:me/jarvis.git")
    config = make_config(tmp_path)
    ctx = make_ctx(tmp_path, cwd=work, git=classify.run_git, config=config)
    return work, ctx, config


def _outcome(repo, command):
    work, ctx, config = repo
    return derive(command, ctx, config)


# --- the three exact forms ------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "git@github.com:me/jarvis", "git@github.com:me/jarvis.git",
    "ssh://git@github.com/me/jarvis", "ssh://git@github.com/me/jarvis.git",
    "https://github.com/me/jarvis", "https://github.com/me/jarvis.git",
])
def test_the_three_exact_forms_with_and_without_dot_git_name_the_repository(raw):
    address = pushaddr.parse_address(raw, HOST)
    assert address is not None
    assert (address.owner, address.name, address.repository) == ("me", "jarvis", "me/jarvis")
    assert address.raw == raw


@pytest.mark.parametrize("raw", [
    "git@elsewhere.example:me/jarvis.git",              # another host, the user's own owner and name
    "https://elsewhere.example/me/jarvis",
    "https://github.com.evil.example/me/jarvis",
    "https://evilgithub.com/me/jarvis",
    "https://githubXcom/me/jarvis",                      # the dot of the host is a dot
    "root@github.com:me/jarvis.git",                     # another user
    "ssh://root@github.com/me/jarvis",
    "ssh://git@github.com:2222/me/jarvis",               # a port
    "https://github.com:8443/me/jarvis",
    "https://token@github.com/me/jarvis",                # credentials in the address
    "https://user:secret@github.com/me/jarvis",
    "git://github.com/me/jarvis",                        # another scheme
    "http://github.com/me/jarvis",
    "file:///srv/git/me/jarvis",
    "/srv/git/me/jarvis",
    "../jarvis",
    "https://github.com/me/jarvis/extra",                # an extra path part
    "git@github.com:me/jarvis/extra",
    "https://github.com/me",
    "git@github.com:jarvis",
    "https://github.com/me/jarvis/",
    "https://github.com/me/jarvis?x=1",
    "https://github.com/me/jarvis#frag",
    "https://github.com/me/.git",
    "https://github.com/../jarvis",
    "HTTPS://github.com/me/jarvis",
    "https://github.com/me/jar vis",
    " https://github.com/me/jarvis",
    "",
    "not an address at all",
])
def test_any_other_address_names_no_repository(raw):
    assert pushaddr.parse_address(raw, HOST) is None


def test_the_forge_host_is_configuration(tmp_path):
    assert pushaddr.parse_address("git@git.example.org:me/jarvis.git", "git.example.org")
    assert pushaddr.parse_address("git@github.com:me/jarvis.git", "git.example.org") is None


# --- resolving the remote a push names ---------------------------------------------------------

def test_a_push_names_its_repository_and_every_ref(repo):
    derived = _outcome(repo, "git push origin feature:main")
    assert isinstance(derived.outcome, Named)
    assert derived.outcome.sentence == "Push feature as main to the repository jarvis."
    assert derived.verdict.classes == ("merge",)
    assert derived.outcome.status == ("push", "origin", (("feature", "main"),))


@pytest.mark.parametrize("command,sentence", [
    ("git push origin main", "Push main to the repository jarvis."),
    ("git push -u origin feature:main", "Push feature as main to the repository jarvis."),
    # A push of two refs names both.
    ("git push origin main release/1.2:main", "Push main and release slash 1.2 as main to the repository jarvis."),
    # HEAD is the current branch, read from the checkout: the name is spoken, not the word.
    ("git push origin HEAD:refs/heads/main", "Push main to the repository jarvis."),
    ("git push origin HEAD", "Push main to the repository jarvis."),
    ("git push origin @:main", "Push main to the repository jarvis."),
    ("git push origin feature:heads/main", "Push feature as main to the repository jarvis."),
    ("git push -f origin feature", "Force push feature to the repository jarvis."),
    ("git push --force-with-lease origin feature:feature", "Force push feature to the repository jarvis."),
    ("git push origin +feature:main", "Force push feature as main to the repository jarvis."),
    ("git push origin :old-branch", "Delete old-branch in the repository jarvis."),
    ("git push --delete origin old-branch other", "Delete old-branch and other in the repository jarvis."),
    ("git push origin :refs/tags/v1", "Delete tag v1 in the repository jarvis."),
])
def test_push_sentences(repo, command, sentence):
    derived = _outcome(repo, command)
    assert isinstance(derived.outcome, Named), derived
    assert derived.outcome.sentence == sentence


def test_a_push_without_a_remote_or_refspec_names_the_missing_part(repo):
    for command in ("git push", "git push origin"):
        outcome = _outcome(repo, command).outcome
        assert isinstance(outcome, Missing), command
        assert outcome.parts == ("name the remote and the refspec",)
        assert outcome.words and outcome.options == ()


@pytest.mark.parametrize("command", [
    "git push --all origin",
    "git push --mirror origin",
    "git push --tags origin",
    "git push --prune origin main",
    "git push origin main a b c",                     # more refs than the sentence holds
    "git push origin 'ma in:main'",
    "git push origin +feature:refs/for/main",
    "git push -f origin tag v1",
    "git push origin :main feature:main",             # sends and deletes
    "git push --delete origin a:b",
    "git push nosuchremote main",
    "git push https://github.com/me/jarvis main",     # an address, not a remote the checkout names
    "git -C /elsewhere push origin main",
    "git -c remote.origin.pushurl=git@elsewhere.example:me/jarvis.git push origin main",
    "git push --force-with-lease=main:abc origin main",
    "git push --receive-pack=evil origin main",
    "git push -o ci.skip origin main",
])
def test_pushes_the_template_cannot_say_are_not_nameable(repo, command):
    derived = _outcome(repo, command)
    assert derived.verdict.gated, command
    assert derived.outcome is None, (command, derived.outcome)


def test_a_remote_with_a_pushurl_is_read_back_by_its_push_address(repo):
    work, _, _ = repo
    run_git(work, "remote", "set-url", "origin", "git@github.com:me/fetch-side.git")
    run_git(work, "config", "remote.origin.pushurl", "git@github.com:them/push-side.git")
    derived = _outcome(repo, "git push origin main")
    # The push address, not the fetch address.
    assert derived.outcome.sentence == "Push main to the repository push-side of them."


def test_a_push_instead_of_rewrite_is_applied(repo):
    work, _, _ = repo
    run_git(work, "config", "url.git@github.com:them/.pushInsteadOf", "git@github.com:me/")
    assert _outcome(repo, "git push origin main").outcome.sentence \
        == "Push main to the repository jarvis of them."


def test_a_push_instead_of_that_rewrites_to_another_host_is_not_nameable(repo):
    work, _, _ = repo
    run_git(work, "config", "url.git@elsewhere.example:.pushInsteadOf", "git@github.com:")
    derived = _outcome(repo, "git push origin main")
    assert derived.outcome is None


def test_a_pushurl_on_another_host_with_the_users_own_owner_and_name_is_not_nameable(repo):
    work, _, _ = repo
    run_git(work, "config", "remote.origin.pushurl", "git@elsewhere.example:me/jarvis.git")
    derived = _outcome(repo, "git push origin main")
    assert derived.outcome is None  # the name is never spoken
    assert derived.verdict.gated


def test_two_push_addresses_are_not_nameable(repo):
    work, _, _ = repo
    run_git(work, "config", "--add", "remote.origin.pushurl", "git@github.com:me/jarvis.git")
    run_git(work, "config", "--add", "remote.origin.pushurl", "git@github.com:me/mirror.git")
    assert pushaddr.resolve_push("origin", work, HOST, classify.run_git) is None
    assert _outcome(repo, "git push origin main").outcome is None


def test_an_address_that_does_not_parse_is_not_nameable(repo):
    work, _, _ = repo
    run_git(work, "remote", "set-url", "origin", "/srv/git/jarvis")
    assert _outcome(repo, "git push origin main").outcome is None
    run_git(work, "remote", "set-url", "origin", "git://github.com/me/jarvis")
    assert _outcome(repo, "git push origin main").outcome is None


def test_a_remote_name_that_is_no_remote_name_is_never_given_to_git(tmp_path):
    calls = []

    def git(args, cwd):
        calls.append(args)
        return "git@github.com:me/jarvis.git"

    assert pushaddr.resolve_push("--upload-pack=evil", tmp_path, HOST, git) is None
    assert pushaddr.resolve_push("-x", tmp_path, HOST, git) is None
    assert calls == []
    assert pushaddr.resolve_push("origin", tmp_path, HOST, git).repository == "me/jarvis"
    assert calls == [["remote", "get-url", "--push", "--all", "origin"]]


# --- the --repo consistency check of a gh call --------------------------------------------------

SHA = "3f9c2ab" + "0123456789abcdef0123456789abcdef0"


def test_a_repo_that_is_not_the_checkouts_repository_is_not_nameable(repo):
    ok = _outcome(repo, f"gh pr merge 21 --repo me/jarvis --match-head-commit {SHA}")
    assert ok.outcome.sentence == "Merge pull request 21 on jarvis, at commit 3f9c2ab."
    same = _outcome(repo, f"gh pr merge 21 --repo ME/Jarvis --match-head-commit {SHA}")
    assert isinstance(same.outcome, Named)
    other = _outcome(repo, f"gh pr merge 21 --repo me/other --match-head-commit {SHA}")
    assert other.outcome is None
    assert _outcome(repo, "gh issue delete 5 --repo me/other").outcome is None


def test_a_gh_call_whose_checkout_remote_is_on_another_host_is_not_nameable(repo):
    work, _, _ = repo
    run_git(work, "remote", "set-url", "origin", "git@elsewhere.example:me/jarvis.git")
    derived = _outcome(repo, f"gh pr merge 21 --repo me/jarvis --match-head-commit {SHA}")
    assert derived.outcome is None
    run_git(work, "remote", "set-url", "--add", "origin", "git@github.com:me/jarvis.git")
    assert _outcome(repo, f"gh pr merge 21 --repo me/jarvis --match-head-commit {SHA}").outcome is None


def test_outside_a_checkout_there_is_nothing_to_be_consistent_with(tmp_path):
    config = make_config(tmp_path)
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    ctx = make_ctx(tmp_path, cwd=lonely, git=classify.run_git, config=config)
    derived = derive(f"gh pr merge 21 --repo me/other --match-head-commit {SHA}", ctx, config)
    assert isinstance(derived.outcome, Named)
    # A checkout without an origin remote: the same.
    bare = tmp_path / "noremote"
    git_init(bare)
    ctx = make_ctx(tmp_path, cwd=bare, git=classify.run_git, config=config)
    assert isinstance(
        derive(f"gh pr merge 21 --repo me/other --match-head-commit {SHA}", ctx, config).outcome,
        Named)


def test_the_repository_of_a_gh_call_is_never_taken_from_the_checkout(repo):
    outcome = _outcome(repo, f"gh pr merge 21 --match-head-commit {SHA}").outcome
    assert isinstance(outcome, Missing)
    assert outcome.parts == ("add --repo owner/name",)


# --- the hash -------------------------------------------------------------------------------------

def test_the_hash_is_over_tool_cwd_and_command_with_zero_bytes_between():
    import hashlib
    expected = hashlib.sha256(b"Bash\0/srv/repo\0gh pr merge 21").hexdigest()
    assert gatehash.call_hash("Bash", "/srv/repo", {"command": "gh pr merge 21"}) == expected
    # Other fields of a Bash call are not part of it.
    assert gatehash.call_hash("Bash", "/srv/repo", {
        "command": "gh pr merge 21", "description": "merge it", "timeout": 5}) == expected


def test_any_byte_of_the_command_the_cwd_or_the_tool_changes_the_hash():
    base = gatehash.call_hash("Bash", "/srv/repo", {"command": "gh pr merge 21"})
    assert gatehash.call_hash("Bash", "/srv/repo", {"command": "gh pr merge 21 "}) != base
    assert gatehash.call_hash("Bash", "/srv/repo", {"command": "gh pr merge 22"}) != base
    assert gatehash.call_hash("Bash", "/srv/other", {"command": "gh pr merge 21"}) != base
    assert gatehash.call_hash("Shell", "/srv/repo", {"command": "gh pr merge 21"}) != base
    # The zero bytes keep the three parts apart.
    assert gatehash.call_hash("Bash", "/srv/repogh", {"command": " pr merge 21"}) != base


def test_another_tool_is_hashed_over_its_whole_canonical_input():
    import hashlib
    one = gatehash.call_hash("mcp__mail__send", "/srv", {"to": "a@example.org", "body": "é"})
    two = gatehash.call_hash("mcp__mail__send", "/srv", {"body": "é", "to": "a@example.org"})
    assert one == two                                          # key order is not part of it
    expected = hashlib.sha256(
        'mcp__mail__send\0/srv\0{"body":"é","to":"a@example.org"}'.encode()).hexdigest()
    assert one == expected
    # For a tool that is not Bash a field named `description` can be what is
    # sent (the description of an issue, of an event): it is hashed.
    with_one = gatehash.call_hash("mcp__x__create_issue", "/srv", {"title": "t", "description": "a"})
    with_other = gatehash.call_hash("mcp__x__create_issue", "/srv", {"title": "t", "description": "b"})
    assert with_one != with_other
    assert with_one != gatehash.call_hash("mcp__x__create_issue", "/srv", {"title": "t"})
    assert gatehash.call_hash("mcp__mail__send", "/srv", {"to": "b@example.org", "body": "é"}) != one
    with pytest.raises(ValueError):
        gatehash.call_hash("Bash", "/srv", {"cmd": "ls"})


def test_a_push_hash_also_covers_the_resolved_push_address(repo):
    import hashlib
    work, _, _ = repo
    derived = _outcome(repo, "git push origin main")
    expected = hashlib.sha256(
        b"Bash\0" + str(work.resolve()).encode() + b"\0git push origin main\0"
        + b"git@github.com:me/jarvis.git" + b"\0main:main").hexdigest()
    assert derived.hash == expected
    assert derived.base_hash == gatehash.call_hash("Bash", str(work.resolve()), {"command": "git push origin main"})
    assert derived.hash != derived.base_hash


def test_a_pushurl_set_after_the_read_back_changes_the_hash_and_only_the_full_one(repo):
    work, _, _ = repo
    before = _outcome(repo, "git push origin main")
    # `git config` is not a gated call: nothing stops this between read-back and release.
    run_git(work, "config", "remote.origin.pushurl", "git@github.com:me/elsewhere.git")
    after = _outcome(repo, "git push origin main")
    assert after.hash != before.hash
    assert after.base_hash == before.base_hash
    run_git(work, "config", "--unset", "remote.origin.pushurl")
    assert _outcome(repo, "git push origin main").hash == before.hash


def test_a_push_whose_remote_cannot_be_resolved_has_no_hash_and_is_never_named(repo):
    derived = _outcome(repo, "git push nosuchremote main")
    assert derived.hash is None
    assert derived.outcome is None


def test_the_same_bytes_in_another_directory_have_another_hash(repo, tmp_path):
    work, ctx, config = repo
    other = tmp_path / "other"
    git_init(other)
    run_git(other, "remote", "add", "origin", "git@github.com:me/jarvis.git")
    one = derive("git push origin main", ctx, config)
    two = derive("git push origin main",
                 make_ctx(tmp_path, cwd=other, git=classify.run_git, config=config), config)
    assert one.outcome.sentence == two.outcome.sentence
    assert one.hash != two.hash and one.base_hash != two.base_hash


def test_the_hash_is_computed_in_exactly_one_module():
    """If the gate and the wrapper ever computed it separately they would
    drift, and a drift means a read-back that releases another call."""
    from tests.gatekit import REPO_ROOT
    users = []
    for path in sorted((REPO_ROOT / "agent_host").iterdir()):
        if path.is_dir() or path.suffix in (".toml", ".md", ".pyc"):
            continue
        source = path.read_text()
        if "hashlib" in source or "sha256(" in source:
            users.append(path.name)
    assert users == ["gatehash.py"]
    assert "gatehash" in (REPO_ROOT / "agent_host" / "naming.py").read_text()
    for module in ("decision.py", "wrapper.py"):
        assert "naming.derive" in (REPO_ROOT / "agent_host" / module).read_text()


# --- the second review of #44: what a push really pushes --------------------------------------

def test_a_push_of_head_names_and_hashes_the_branch_it_is_now(repo):
    work, _, _ = repo
    on_main = _outcome(repo, "git push origin HEAD:main")
    assert on_main.outcome.sentence == "Push main to the repository jarvis."
    assert on_main.outcome.status == ("push", "origin", (("main", "main"),))
    # `git checkout` is not a gated call: the branch behind HEAD can change
    # between the read-back and the release. The sentence and the hash follow.
    run_git(work, "checkout", "-q", "-b", "other")
    on_other = _outcome(repo, "git push origin HEAD:main")
    assert on_other.outcome.sentence == "Push other as main to the repository jarvis."
    assert on_other.hash != on_main.hash
    assert on_other.base_hash == on_main.base_hash           # the same bytes, the same directory
    run_git(work, "checkout", "-q", "main")
    assert _outcome(repo, "git push origin HEAD:main").hash == on_main.hash


def test_a_detached_head_cannot_be_named(repo):
    work, _, _ = repo
    run_git(work, "checkout", "-q", "--detach")
    derived = _outcome(repo, "git push origin HEAD:main")
    assert derived.verdict.gated and derived.outcome is None and derived.hash is None


def test_a_refspec_without_a_colon_under_a_push_mapping_is_not_nameable(repo):
    work, _, _ = repo
    assert isinstance(_outcome(repo, "git push origin main").outcome, Named)
    run_git(work, "config", "remote.origin.push", "refs/heads/main:refs/heads/production")
    derived = _outcome(repo, "git push origin main")
    assert derived.verdict.gated and derived.outcome is None   # it would land elsewhere
    run_git(work, "config", "--unset", "remote.origin.push")
    run_git(work, "config", "push.default", "upstream")
    assert _outcome(repo, "git push origin main").outcome is None
    # An explicit src:dst says where it lands itself.
    assert _outcome(repo, "git push origin main:main").outcome.sentence \
        == "Push main to the repository jarvis."


@pytest.mark.parametrize("command", [
    "ssh jumpy git push origin main",
    "sudo git push origin main",
    "sudo -u bob git push origin main",
    "bash -c 'git push origin main'",
    "env -C /srv/other git push origin main",
    "git -C /srv/other push origin main",
    "GIT_DIR=/srv/other/.git git push origin main",
    "sudo --chdir=/srv/other git push origin main",
    "sudo -D /srv/other git push origin main",
    "env --chdir=/srv/other git push origin main",
    "git --git-dir=/srv/other/.git push origin main",
    "git --work-tree=/srv/other push origin main",
    "GIT_SSH_COMMAND='ssh -o ProxyCommand=evil' git push origin main",
])
def test_a_push_behind_a_wrapper_is_not_nameable(repo, command):
    derived = _outcome(repo, command)
    assert derived.verdict.gated, command
    assert derived.outcome is None, command
