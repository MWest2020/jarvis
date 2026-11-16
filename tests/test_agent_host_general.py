"""jarvis-general-conversation on the agent host: the list request (D51) and
the clone after a confirmed `gh repo create` (D53).

The fake `claude` is the one of test_agent_host_wrapper_confirm (its calls go
through the real gate); the clone command is a small script that does what
`gh repo clone` would, or fails in the ways the tests ask for.
"""
import json
import os
import sys

import pytest

from agent_host import clones, projects, request, wrapper
from agent_host.config import ConfigError, config_from_dict
from tests.gatekit import git_init
from tests.test_agent_host_wrapper_confirm import Host

CREATE = "gh repo create me/birdsong --private"
CREATE_SENTENCE = "Create the private repository birdsong."

CLONE_SCRIPT = """
import json, os, subprocess, sys, time
mode = open({mode!r}).read().strip()
repo, dest = sys.argv[1], sys.argv[2]
with open({log!r}, "a") as fh:
    fh.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd(),
                         "ident": os.environ.get("IDENT"),
                         "grant": os.environ.get("JARVIS_GRANT")}}) + "\\n")
if mode == "failonce":
    open({mode!r}, "w").write("ok")
    mode = "fail"
if mode == "slow":
    os.makedirs(dest)
    time.sleep(30)
if mode == "fail":
    os.makedirs(os.path.join(dest, ".git"))
    sys.exit(128)
if mode == "notree":
    os.makedirs(dest)
    sys.exit(0)
subprocess.run(["git", "init", "-q", dest], check=True)
subprocess.run(["git", "-C", dest, "remote", "add", "origin",
                "https://github.com/" + repo + ".git"], check=True)
"""


class CloneHost(Host):
    def __init__(self, tmp_path, **overrides):
        self.clone_mode = tmp_path / "clone-mode"
        self.clone_mode.write_text("ok")
        self.clone_log = tmp_path / "clone.log"
        script = tmp_path / "clone.py"
        script.write_text(CLONE_SCRIPT.format(mode=str(self.clone_mode), log=str(self.clone_log)))
        overrides.setdefault("clone_command", [sys.executable, str(script)])
        super().__init__(tmp_path, **overrides)
        self.root = tmp_path / "projects"

    def clones(self):
        if not self.clone_log.exists():
            return []
        return [json.loads(line) for line in self.clone_log.read_text().splitlines()]

    def create_and_confirm(self, monkeypatch, *, ran=True, reply="made.\nSPOKEN SUMMARY: I made birdsong."):
        self.agent(monkeypatch, [CREATE])
        asked = self.ask(prompt="make a repo called birdsong")
        assert asked["pending"] is True
        assert asked["readback"] == f"{CREATE_SENTENCE} Say yes to go ahead."
        self.agent(monkeypatch, [CREATE] if ran else [], reply=reply)
        return self.answer(asked)

    def leftovers(self):
        return sorted(p.name for p in self.root.iterdir() if p.name.startswith("."))


@pytest.fixture
def host(tmp_path):
    return CloneHost(tmp_path)


# --- the list request (D51) -------------------------------------------------------------

def _list(config):
    return wrapper.handle_request(b'{"v":1,"list":true}', config)


def test_a_list_names_exactly_what_a_turn_accepts(host, tmp_path):
    root = host.root
    git_init(root / "alpha")
    git_init(root / "Bravo")
    (root / "notes").mkdir()                              # not a checkout
    (root / ".hidden").mkdir()
    git_init(root / ".hidden-checkout")                   # hidden: not a name
    (root / "bare").mkdir()
    (root / "bare" / "HEAD").write_text("ref: refs/heads/main\n")
    outside = tmp_path / "outside"
    git_init(outside)
    (root / "link").symlink_to(outside)                    # a symlink out of the root

    reply = _list(host.config)
    assert set(reply) == set(wrapper.LIST_FIELDS)
    assert reply == {"v": 1, "status": "ok", "error": None,
                     "projects": ["Bravo", "alpha", "demo"]}
    for name in reply["projects"]:
        projects.resolve_project(host.config, name)        # every one is turnable
    for name in ("notes", ".hidden", ".hidden-checkout", "bare", "link"):
        with pytest.raises(projects.ProjectError):
            projects.resolve_project(host.config, name)


def test_a_list_leaves_out_the_exclusions(tmp_path):
    host = CloneHost(tmp_path, project_exclusions=["demo"])
    git_init(host.root / "alpha")
    assert _list(host.config)["projects"] == ["alpha"]


def test_a_list_with_an_explicit_project_list(tmp_path):
    git_init(tmp_path / "elsewhere" / "one")
    (tmp_path / "elsewhere" / "plain").mkdir()
    host = CloneHost(tmp_path, projects_root=None, projects={
        "one": str(tmp_path / "elsewhere" / "one"),
        "plain": str(tmp_path / "elsewhere" / "plain"),
        "gone": str(tmp_path / "elsewhere" / "gone"),
    })
    assert _list(host.config)["projects"] == ["one"]


def test_a_list_is_capped(host, monkeypatch):
    monkeypatch.setattr(projects, "MAX_LISTED", 2)
    git_init(host.root / "alpha")
    git_init(host.root / "bravo")
    assert _list(host.config)["projects"] == ["alpha", "bravo"]


def test_a_list_starts_nothing_and_touches_no_pending_call(host, monkeypatch):
    host.agent(monkeypatch, [CREATE])
    asked = host.ask(prompt="make it")
    before = host.pending_file().read_text()
    records = len(host.records())
    popens = len(host.popens)
    from agent_host.turnlock import acquire_turn_lock
    with acquire_turn_lock(host.config.lock_path):           # a turn is running
        assert _list(host.config)["status"] == "ok"
    assert host.pending_file().read_text() == before
    assert len(host.records()) == records and len(host.popens) == popens
    # The read-back is still good.
    host.agent(monkeypatch, [CREATE])
    assert host.answer(asked)["error"] is None


def test_a_root_that_cannot_be_listed_is_an_error_not_an_empty_list(tmp_path):
    host = CloneHost(tmp_path, projects_root=str(tmp_path / "no-such-root"))
    assert _list(host.config) == wrapper._reply(status="error", error="no_checkout")


def test_an_entry_that_cannot_be_looked_at_is_skipped(host, monkeypatch, capsys):
    git_init(host.root / "alpha")
    real = projects.resolve_project

    def resolve(config, name):
        if name == "alpha":
            raise PermissionError(13, "Permission denied")
        return real(config, name)
    monkeypatch.setattr(projects, "resolve_project", resolve)
    assert _list(host.config)["projects"] == ["demo"]
    assert "alpha" in capsys.readouterr().err


def test_a_list_with_the_kill_switch_is_disabled(tmp_path):
    host = CloneHost(tmp_path, enabled=False)
    assert _list(host.config) == wrapper.disabled_reply()


@pytest.mark.parametrize("raw", [
    b'{"v":1,"list":false}', b'{"v":1,"list":1}', b'{"v":1,"list":"yes"}',
    b'{"v":2,"list":true}', b'{"v":true,"list":true}', b'{"list":true}',
    b'{"v":1,"list":true,"project":"demo"}',
    b'{"v":1,"list":true,"project":"demo","prompt":"","confirm":null}',
])
def test_anything_but_the_exact_list_request_is_bad(host, raw):
    with pytest.raises(request.RequestError):
        request.parse_request(raw)
    assert wrapper.handle_request(raw, host.config)["error"] == "bad_request"


def test_a_turn_request_is_unchanged():
    req = request.parse_request(b'{"v":1,"project":"demo","prompt":"hi","confirm":null}')
    assert isinstance(req, request.Request) and req.project == "demo"


# --- the clone after a confirmed create (D53) ---------------------------------------------

READY = {"name": "birdsong", "ready": True, "reason": None}


def _not_ready(reason):
    return {"name": "birdsong", "ready": False, "reason": reason}


def _confirmed_action(host):
    return [a for a in host.records()[-1]["gated_actions"] if a["outcome"] == "confirmed"][0]


def test_a_confirmed_create_that_ran_becomes_a_project(host, monkeypatch):
    reply = host.create_and_confirm(monkeypatch)

    assert reply["status"] == "ok" and reply["error"] is None
    assert reply["project"] == READY
    assert reply["notice"] is None
    assert reply["spoken"] == "SPOKEN SUMMARY: I made birdsong."
    assert projects.resolve_project(host.config, "birdsong") == (host.root / "birdsong").resolve()
    assert "birdsong" in _list(host.config)["projects"]
    [clone] = host.clones()
    assert clone["argv"][0] == "me/birdsong"
    assert clone["argv"][1].startswith(str(host.root / clones.CLONE_DIR_PREFIX))
    assert clone["argv"][1].endswith("/birdsong")
    assert clone["argv"][2:] == ["--", "--depth", "1"]          # shallow
    assert clone["cwd"] == str(host.root)
    assert clone["ident"] == "jarvis"          # the turn's identity
    assert clone["grant"] is None              # never the grant
    assert host.leftovers() == []
    assert _confirmed_action(host)["project"] == "ready"


def test_a_reply_without_a_new_repository_has_no_project_field(host, monkeypatch):
    host.agent(monkeypatch, [])
    assert "project" not in host.ask(prompt="hello")
    assert set(host.ask(prompt="hello")) == set(wrapper.V1_FIELDS)


def test_the_project_field_survives_a_new_session_notice(host, monkeypatch):
    host.agent(monkeypatch, [CREATE])
    asked = host.ask(prompt="make it")
    monkeypatch.setattr(wrapper.SessionStore, "get", lambda self, project: (None, True))
    host.agent(monkeypatch, [CREATE])
    reply = host.answer(asked)
    assert reply["project"] == READY and reply["notice"] == "new_session"


def test_the_project_field_survives_a_new_read_back(host, monkeypatch):
    merge = "gh pr merge 21 --repo me/jarvis --match-head-commit " + "3f9c2ab" + "0" * 33
    host.agent(monkeypatch, [CREATE])
    asked = host.ask(prompt="make it")
    host.agent(monkeypatch, [CREATE, merge])
    reply = host.answer(asked)
    assert reply["pending"] is True and reply["project"] == READY


def test_the_project_field_survives_a_failed_turn(host, monkeypatch):
    host.agent(monkeypatch, [CREATE])
    asked = host.ask(prompt="make it")
    host.agent(monkeypatch, [CREATE], returncode=1)
    reply = host.answer(asked)
    assert reply["error"] == "agent_failed" and reply["project"] == READY


def test_the_project_field_survives_changed_settings(host, monkeypatch):
    host.agent(monkeypatch, [CREATE])
    asked = host.ask(prompt="make it")

    def plant(_popen):
        (host.checkout / ".claude").mkdir(exist_ok=True)
        (host.checkout / ".claude" / "settings.json").write_text('{"hooks": {}}')
    host.agent(monkeypatch, [CREATE], during=plant)
    reply = host.answer(asked)
    assert reply["notice"] == "settings_changed" and reply["project"] == READY


def test_the_wrapper_clones_after_the_agent_turn_not_the_agent(host, monkeypatch):
    host.create_and_confirm(monkeypatch)
    assert [d.allow for d in host.popens[-1].decisions] == [True]


def test_a_create_that_did_not_run_clones_nothing(host, monkeypatch):
    reply = host.create_and_confirm(monkeypatch, ran=False)
    assert "project" not in reply
    assert host.clones() == [] and not (host.root / "birdsong").exists()


def test_a_create_that_ran_and_failed_clones_nothing(host, monkeypatch):
    monkeypatch.setattr(wrapper.record_mod, "ran", lambda *a: "ran, failed")
    reply = host.create_and_confirm(monkeypatch)
    assert "project" not in reply
    assert host.clones() == []


@pytest.mark.parametrize("mode,why", [
    ("fail", "the clone exited 128"),
    ("notree", "the clone is not a work tree"),
])
def test_a_failed_clone_leaves_nothing_and_is_retried_later(host, monkeypatch, mode, why):
    host.clone_mode.write_text(mode)
    reply = host.create_and_confirm(monkeypatch)
    assert reply["project"] == _not_ready("retrying")
    assert not (host.root / "birdsong").exists()
    assert host.leftovers() == []
    assert _confirmed_action(host)["project"] == f"not ready (retrying): {why}"
    assert clones.pending(host.config) == [("me/birdsong", 1)]


def test_a_clone_that_takes_too_long_is_stopped_and_removed(tmp_path, monkeypatch):
    host = CloneHost(tmp_path, clone_timeout_s=1)
    host.clone_mode.write_text("slow")
    reply = host.create_and_confirm(monkeypatch)
    assert reply["project"] == _not_ready("retrying")
    assert not (host.root / "birdsong").exists()
    assert host.leftovers() == []


def test_the_next_turn_retries_a_pending_clone_first(host, monkeypatch):
    host.clone_mode.write_text("failonce")
    host.create_and_confirm(monkeypatch)
    assert not (host.root / "birdsong").exists()
    host.agent(monkeypatch, [])
    host.ask(prompt="anything")
    assert (host.root / "birdsong" / ".git").is_dir()
    assert clones.pending(host.config) == []
    retry = [r for r in host.records() if r["outcome"] == "clone"]
    assert retry[0]["gated_actions"][0]["attempt"] == 2
    assert retry[0]["gated_actions"][0]["project"] == "ready"


def test_a_list_retries_a_pending_clone_after_answering(host, monkeypatch):
    host.clone_mode.write_text("failonce")
    host.create_and_confirm(monkeypatch)
    ran = []
    monkeypatch.setattr(wrapper, "_in_background", lambda work: ran.append(work))
    first = _list(host.config)
    assert "birdsong" not in first["projects"]          # answered before the retry
    [work] = ran
    work()
    assert "birdsong" in _list(host.config)["projects"]


def test_a_list_does_not_retry_while_a_turn_holds_the_lock(host, monkeypatch):
    host.clone_mode.write_text("failonce")
    host.create_and_confirm(monkeypatch)
    from agent_host.turnlock import acquire_turn_lock
    with acquire_turn_lock(host.config.lock_path):
        wrapper._retry_clones_if_free(host.config)
    assert clones.pending(host.config) == [("me/birdsong", 1)]


def test_three_attempts_in_all_then_the_desk(host, monkeypatch):
    host.clone_mode.write_text("fail")
    host.create_and_confirm(monkeypatch)                # attempt 1
    host.agent(monkeypatch, [])
    host.ask(prompt="one")                              # attempt 2
    assert clones.pending(host.config) == [("me/birdsong", 2)]
    host.ask(prompt="two")                              # attempt 3, the last
    assert clones.pending(host.config) == []
    last = [r for r in host.records() if r["outcome"] == "clone"][-1]["gated_actions"][0]
    assert last["attempt"] == 3 and last["project"].startswith("not ready (needs_desk)")
    host.ask(prompt="three")
    assert len(host.clones()) == 3                      # no fourth


def test_a_planted_marker_for_another_owner_is_dropped(host):
    directory = clones.markers_dir(host.config)
    directory.mkdir(parents=True)
    (directory / "evil.json").write_text(json.dumps({"repository": "them/evil", "tries": 1}))
    (directory / "other.json").write_text(json.dumps({"repository": "me/mismatch", "tries": 1}))
    (directory / "many.json").write_text(json.dumps({"repository": "me/many", "tries": 7}))
    assert clones.pending(host.config) == []
    assert list(directory.iterdir()) == []


def test_stale_staging_is_removed_by_a_retry_and_by_prune(host):
    from agent_host import record
    old = host.root / (clones.CLONE_DIR_PREFIX + "old")
    young = host.root / (clones.CLONE_DIR_PREFIX + "young")
    for d in (old, young):
        (d / "x").mkdir(parents=True)
    os.utime(old, (0, 0))
    clones.retry_pending(host.config, {})
    assert not old.exists() and young.exists()
    os.utime(young, (0, 0))
    removed = record.prune(host.config.state_dir, 90, staging_root=host.root)
    assert young in removed and not young.exists()


def test_another_owner_is_not_cloned(tmp_path, monkeypatch):
    host = CloneHost(tmp_path, own_owners=["someone-else"])
    host.agent(monkeypatch, [CREATE])
    asked = host.ask(prompt="make it")
    host.agent(monkeypatch, [CREATE])
    reply = host.answer(asked)
    assert reply["project"] == _not_ready("needs_desk")
    assert host.clones() == [] and clones.pending(host.config) == []


def test_an_explicit_project_list_is_not_cloned_into(tmp_path, monkeypatch):
    host = CloneHost(tmp_path, projects_root=None,
                     projects={"demo": str(tmp_path / "projects" / "demo")})
    reply = host.create_and_confirm(monkeypatch)
    assert reply["project"] == _not_ready("needs_desk")
    assert host.clones() == []


def test_an_excluded_name_is_not_cloned(tmp_path, monkeypatch):
    host = CloneHost(tmp_path, project_exclusions=["birdsong"])
    reply = host.create_and_confirm(monkeypatch)
    assert reply["project"] == _not_ready("needs_desk")
    assert host.clones() == []


def test_an_existing_checkout_of_that_repository_counts_as_ready(host, monkeypatch):
    from tests.gatekit import run_git
    git_init(host.root / "birdsong")
    run_git(host.root / "birdsong", "remote", "add", "origin", "git@github.com:me/birdsong.git")
    marker = host.root / "birdsong" / "mine.txt"
    marker.write_text("keep")
    reply = host.create_and_confirm(monkeypatch)
    assert reply["project"] == READY
    assert host.clones() == [] and marker.read_text() == "keep"


@pytest.mark.parametrize("origin", [None, "git@github.com:me/other.git",
                                    "git@github.com:them/birdsong.git"])
def test_a_different_folder_with_that_name_is_not_ready_and_not_touched(host, monkeypatch, origin):
    from tests.gatekit import run_git
    git_init(host.root / "birdsong")
    if origin:
        run_git(host.root / "birdsong", "remote", "add", "origin", origin)
    (host.root / "birdsong" / "notes.txt").write_text("keep")
    reply = host.create_and_confirm(monkeypatch)
    assert reply["project"] == _not_ready("name_taken")
    assert host.clones() == []
    assert (host.root / "birdsong" / "notes.txt").read_text() == "keep"


def test_a_plain_directory_with_that_name_is_not_touched(host, monkeypatch):
    (host.root / "birdsong").mkdir()
    reply = host.create_and_confirm(monkeypatch)
    assert reply["project"] == _not_ready("name_taken")


@pytest.mark.parametrize("command", [
    "sudo gh repo create me/birdsong --private",
])
def test_only_a_plain_repo_create_is_cloned(host, monkeypatch, command):
    # (`gh repo fork` is not confirmable by voice at all: it needs the desk.)
    host.agent(monkeypatch, [command])
    asked = host.ask(prompt="do it")
    assert asked["pending"] is True
    host.agent(monkeypatch, [command])
    reply = host.answer(asked)
    assert "project" not in reply
    assert host.clones() == []


def test_a_create_through_ssh_is_not_cloned():
    from types import SimpleNamespace

    from agent_host import naming
    named = naming.Named("Create the private repository birdsong.", frozenset({"create"}),
                         status=("create", "me/birdsong"))
    for wrappers in ((("ssh", "web.example.org"),), (("root", "root"),)):
        derived = SimpleNamespace(
            outcome=named,
            verdict=SimpleNamespace(classes=("create",),
                                    command=SimpleNamespace(wrappers=wrappers, env=())))
        assert wrapper._created_repository(derived) is None
    plain = SimpleNamespace(outcome=named, verdict=SimpleNamespace(
        classes=("create",), command=SimpleNamespace(wrappers=(), env=())))
    assert wrapper._created_repository(plain) == "me/birdsong"


@pytest.mark.parametrize("command", ["gh repo create me/birdsong --clone",
                                     "gh repo create me/birdsong --private -c"])
def test_a_create_with_clone_is_refused_with_how_to_ask_again(host, monkeypatch, command):
    host.agent(monkeypatch, [command, CREATE])
    reply = host.ask(prompt="make it")
    first, second = host.popens[-1].decisions
    assert not first.allow and first.reason.startswith("[create_clone]")
    assert "without --clone" in first.reason
    # Asked again without it, it is read back.
    assert reply["pending"] is True
    assert reply["readback"] == f"{CREATE_SENTENCE} Say yes to go ahead."


def test_the_clone_keys_have_defaults_and_bounds(tmp_path):
    host = Host(tmp_path)
    assert host.config.clone_command == ("gh", "repo", "clone")
    assert host.config.clone_timeout_s == 45
    base = {
        "projects_root": str(tmp_path), "state_dir": str(tmp_path / "s"),
        "environment_file": str(tmp_path / "e"), "turn_settings": str(tmp_path / "t"),
        "prompt_file": str(tmp_path / "p"), "session_identity": "jarvis",
        "user_settings_path": str(tmp_path / "u"),
    }
    assert config_from_dict({**base, "clone_timeout_s": 90}).clone_timeout_s == 90
    for bad in ({"clone_timeout_s": 0}, {"clone_timeout_s": 91}, {"clone_timeout_s": True},
                {"clone_command": []}, {"clone_command": [""]}):
        with pytest.raises(ConfigError):
            config_from_dict({**base, **bad})


def test_the_rename_never_replaces(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    target = tmp_path / "dst"
    target.mkdir()                                  # empty: a plain rename would replace it
    with pytest.raises(FileExistsError):
        clones.rename_noreplace(source, target)
    target.rmdir()
    clones.rename_noreplace(source, target)
    assert target.is_dir() and not source.exists()


def test_the_rename_without_renameat2_checks_first(tmp_path, monkeypatch):
    monkeypatch.setattr(clones, "_renameat2", lambda: None)
    source, target = tmp_path / "src", tmp_path / "dst"
    source.mkdir()
    target.mkdir()
    with pytest.raises(FileExistsError):
        clones.rename_noreplace(source, target)


def test_project_notices_are_gone_from_the_closed_list():
    assert {"project_ready", "project_not_ready"}.isdisjoint(wrapper.NOTICE_CODES)
