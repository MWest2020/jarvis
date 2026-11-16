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

from agent_host import projects, request, wrapper
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

def test_a_confirmed_create_that_ran_becomes_a_project(host, monkeypatch):
    reply = host.create_and_confirm(monkeypatch)

    assert reply["status"] == "ok" and reply["error"] is None
    assert reply["notice"] == "project_ready"
    assert reply["spoken"] == "SPOKEN SUMMARY: I made birdsong."
    assert projects.resolve_project(host.config, "birdsong") == (host.root / "birdsong").resolve()
    assert "birdsong" in _list(host.config)["projects"]
    [clone] = host.clones()
    assert clone["argv"][0] == "me/birdsong"
    assert clone["argv"][1].startswith(str(host.root / wrapper.CLONE_DIR_PREFIX))
    assert clone["argv"][1].endswith("/birdsong")
    assert clone["cwd"] == str(host.root)
    assert clone["ident"] == "jarvis"          # the turn's identity
    assert clone["grant"] is None              # never the grant
    assert host.leftovers() == []
    confirmed = [a for a in host.records()[-1]["gated_actions"] if a["outcome"] == "confirmed"]
    assert confirmed[0]["project"] == "ready"


def test_the_wrapper_clones_after_the_agent_turn_not_the_agent(host, monkeypatch):
    host.create_and_confirm(monkeypatch)
    # The agent's own calls in the confirming turn: only the confirmed create.
    assert [d.allow for d in host.popens[-1].decisions] == [True]


def test_a_create_that_did_not_run_clones_nothing(host, monkeypatch):
    reply = host.create_and_confirm(monkeypatch, ran=False)
    assert reply["notice"] is None
    assert host.clones() == [] and not (host.root / "birdsong").exists()


def test_a_create_that_ran_and_failed_clones_nothing(host, monkeypatch, tmp_path):
    from agent_host import record
    real = record.ran
    monkeypatch.setattr(wrapper.record_mod, "ran", lambda *a: "ran, failed")
    reply = host.create_and_confirm(monkeypatch)
    monkeypatch.setattr(wrapper.record_mod, "ran", real)
    assert reply["notice"] is None
    assert host.clones() == []


@pytest.mark.parametrize("mode,why", [
    ("fail", "the clone exited 128"),
    ("notree", "the clone is not a work tree"),
])
def test_a_failed_clone_leaves_nothing_and_needs_the_desk(host, monkeypatch, mode, why):
    host.clone_mode.write_text(mode)
    reply = host.create_and_confirm(monkeypatch)
    assert reply["notice"] == "project_not_ready"
    assert not (host.root / "birdsong").exists()
    assert host.leftovers() == []
    confirmed = [a for a in host.records()[-1]["gated_actions"] if a["outcome"] == "confirmed"]
    assert confirmed[0]["project"] == f"not ready: {why}"


def test_a_clone_that_takes_too_long_is_stopped_and_removed(tmp_path, monkeypatch):
    host = CloneHost(tmp_path, clone_timeout_s=1)
    host.clone_mode.write_text("slow")
    reply = host.create_and_confirm(monkeypatch)
    assert reply["notice"] == "project_not_ready"
    assert not (host.root / "birdsong").exists()
    assert host.leftovers() == []


def test_another_owner_is_not_cloned(tmp_path, monkeypatch):
    host = CloneHost(tmp_path, own_owners=["someone-else"])
    host.agent(monkeypatch, [CREATE])
    asked = host.ask(prompt="make it")
    host.agent(monkeypatch, [CREATE])
    reply = host.answer(asked)
    assert reply["notice"] == "project_not_ready"
    assert host.clones() == []


def test_an_explicit_project_list_is_not_cloned_into(tmp_path, monkeypatch):
    host = CloneHost(tmp_path, projects_root=None,
                     projects={"demo": str(tmp_path / "projects" / "demo")})
    reply = host.create_and_confirm(monkeypatch)
    assert reply["notice"] == "project_not_ready"
    assert host.clones() == []


def test_an_excluded_name_is_not_cloned(tmp_path, monkeypatch):
    host = CloneHost(tmp_path, project_exclusions=["birdsong"])
    reply = host.create_and_confirm(monkeypatch)
    assert reply["notice"] == "project_not_ready"
    assert host.clones() == []


def test_an_existing_project_counts_as_ready_and_is_not_touched(host, monkeypatch):
    git_init(host.root / "birdsong")
    marker = host.root / "birdsong" / "mine.txt"
    marker.write_text("keep")
    reply = host.create_and_confirm(monkeypatch)
    assert reply["notice"] == "project_ready"
    assert host.clones() == [] and marker.read_text() == "keep"


def test_an_existing_directory_that_is_not_a_project_is_not_touched(host, monkeypatch):
    (host.root / "birdsong").mkdir()
    (host.root / "birdsong" / "notes.txt").write_text("keep")
    reply = host.create_and_confirm(monkeypatch)
    assert reply["notice"] == "project_not_ready"
    assert host.clones() == []
    assert (host.root / "birdsong" / "notes.txt").read_text() == "keep"


@pytest.mark.parametrize("command", ["sudo gh repo create me/birdsong --private"])
def test_only_a_plain_repo_create_is_cloned(host, monkeypatch, command):
    # (`gh repo fork` is not confirmable by voice at all: it needs the desk.)
    host.agent(monkeypatch, [command])
    asked = host.ask(prompt="do it")
    assert asked["pending"] is True
    host.agent(monkeypatch, [command])
    reply = host.answer(asked)
    assert reply["notice"] is None
    assert host.clones() == []


def test_the_clone_keys_have_defaults_and_bounds(tmp_path):
    host = Host(tmp_path)
    assert host.config.clone_command == ("gh", "repo", "clone")
    assert host.config.clone_timeout_s == 120
    base = {
        "projects_root": str(tmp_path), "state_dir": str(tmp_path / "s"),
        "environment_file": str(tmp_path / "e"), "turn_settings": str(tmp_path / "t"),
        "prompt_file": str(tmp_path / "p"), "session_identity": "jarvis",
        "user_settings_path": str(tmp_path / "u"),
    }
    for bad in ({"clone_timeout_s": 0}, {"clone_timeout_s": 601}, {"clone_timeout_s": True},
                {"clone_command": []}, {"clone_command": [""]}):
        with pytest.raises(ConfigError):
            config_from_dict({**base, **bad})


def test_the_notices_are_ordered(host):
    assert wrapper.most_important_notice(["project_ready", "new_session"]) == "project_ready"
    assert wrapper.most_important_notice(["project_ready", "settings_changed"]) == "settings_changed"
    assert os.path.basename(wrapper.CLONE_DIR_PREFIX).startswith(".")
