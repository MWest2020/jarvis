"""The project sync (deploy/sync-projects.sh) and its repository list
(deploy/list_repos.py), add-project-list. The script runs for real against local
git repositories; the GitHub API is replaced by a fake lister or a fake `get`.
"""
import importlib.util
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "sync-projects.sh"

_spec = importlib.util.spec_from_file_location("list_repos", ROOT / "deploy" / "list_repos.py")
list_repos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(list_repos)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _repo(owner="Owner", name="one", *, archived=False, fork=False, size=100):
    return {"owner": {"login": owner}, "name": name, "archived": archived, "fork": fork,
            "size": size}


# --- the list ---------------------------------------------------------------

def test_select_keeps_only_the_owners_own_live_repositories(capsys):
    repos = [
        _repo(name="keep"),
        _repo(name="old", archived=True),
        _repo(name="copy", fork=True),
        _repo(owner="SomeEmployer", name="secret"),
        _repo(name="huge", size=600 * 1024),
        _repo(name="private-notes"),
        _repo(owner="owner", name="also"),          # logins are case-insensitive
    ]
    kept = list_repos.select(repos, "Owner", max_kb=500 * 1024, exclude=["private-notes"])
    assert kept == ["owner/also", "Owner/keep"]
    log = capsys.readouterr().err
    assert "Owner/old: archived" in log
    assert "Owner/copy: a fork" in log
    assert "Owner/huge: 600 MB is over the limit of 500 MB" in log
    assert "Owner/private-notes: in JARVIS_SYNC_EXCLUDE" in log
    assert "SomeEmployer" not in log and "secret" not in log


@pytest.mark.parametrize("name", ["..", ".hidden", "a/b", "", "x y"])
def test_select_refuses_unusable_names(name):
    assert list_repos.select([_repo(name=name)], "Owner", max_kb=10**9) == []


def test_fetch_all_follows_the_pages():
    pages = {
        "https://api.github.com/user/repos?affiliation=owner&per_page=100":
            ([_repo(name="a")], "https://api.github.com/user/repos?page=2"),
        "https://api.github.com/user/repos?page=2": ([_repo(name="b")], None),
    }
    seen_tokens = []

    def get(url, token):
        seen_tokens.append(token)
        return pages[url]

    repos = list_repos.fetch_all("t0ken", get=get)
    assert [r["name"] for r in repos] == ["a", "b"]
    assert seen_tokens == ["t0ken", "t0ken"]


def test_fetch_all_never_follows_a_link_off_the_api():
    def get(url, token):
        return [_repo()], "https://evil.example/steal"

    with pytest.raises(list_repos.ListError):
        list_repos.fetch_all("t0ken", get=get)


def test_main_needs_an_owner_and_a_token(monkeypatch):
    monkeypatch.delenv("JARVIS_SYNC_OWNER", raising=False)
    monkeypatch.setenv("GIT_PAT", "x")
    assert list_repos.main() == 2
    monkeypatch.setenv("JARVIS_SYNC_OWNER", "Owner")
    monkeypatch.delenv("GIT_PAT")
    assert list_repos.main() == 2


def test_main_prints_nothing_when_the_list_fails(monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_SYNC_OWNER", "Owner")
    monkeypatch.setenv("GIT_PAT", "s3cret")

    def broken(token):
        raise list_repos.ListError("GitHub answered 401 for the repository list")

    monkeypatch.setattr(list_repos, "fetch_all", broken)
    assert list_repos.main() == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "401" in err and "s3cret" not in err


def test_main_prints_the_selection(monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_SYNC_OWNER", "Owner")
    monkeypatch.setenv("GIT_PAT", "s3cret")
    monkeypatch.setenv("JARVIS_SYNC_EXCLUDE", "b, c")
    monkeypatch.setattr(list_repos, "fetch_all", lambda token: [
        _repo(name="a", size=2048), _repo(name="b"), _repo(name="c"), _repo(name="d", size=1024)])
    assert list_repos.main() == 0
    out, err = capsys.readouterr()
    assert out == "Owner/a\nOwner/d\n"
    assert "2 repositories of Owner to sync, 3 MB" in err
    assert "s3cret" not in out + err


# --- the script -------------------------------------------------------------

def _git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})


def _origin(base: Path, owner: str, name: str) -> Path:
    """A repository at <base>/<owner>/<name>.git with one commit."""
    work = base / "src" / name
    work.mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=work)
    (work / "README").write_text(name)
    _git("add", ".", cwd=work)
    _git("commit", "-q", "-m", "first", cwd=work)
    bare = base / owner / f"{name}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    _git("clone", "-q", "--bare", str(work), str(bare))
    return bare


def _lister(tmp_path: Path, lines: str | None) -> Path:
    """A fake jarvis-list-repos: prints `lines`, or fails when None."""
    script = tmp_path / "lister"
    if lines is None:
        script.write_text("#!/bin/sh\necho 'sync: ERROR GitHub answered 500' >&2\nexit 1\n")
    else:
        script.write_text(f"#!/bin/sh\nprintf '%s' '{lines}'\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _sync(tmp_path, repos, *, lister=None, base=None):
    env = {k: v for k, v in os.environ.items() if k != "GIT_PAT"}
    env.update({
        "JARVIS_SYNC_REPOS": repos,
        "JARVIS_SYNC_DIR": str(tmp_path / "work"),
        "JARVIS_SYNC_BASE_URL": f"file://{base or tmp_path / 'remote'}",
        "HOME": str(tmp_path),
        "GIT_CONFIG_NOSYSTEM": "1",
    })
    if lister is not None:
        env["JARVIS_SYNC_LISTER"] = str(lister)
    return subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True)


@needs_git
def test_wildcard_clones_every_listed_repository(tmp_path):
    remote = tmp_path / "remote"
    _origin(remote, "Owner", "one")
    _origin(remote, "Owner", "two")
    result = _sync(tmp_path, "*", lister=_lister(tmp_path, "Owner/one\nOwner/two\n"))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "work" / "one" / "README").read_text() == "one"
    assert (tmp_path / "work" / "two" / ".git").is_dir()
    shallow = subprocess.run(["git", "-C", str(tmp_path / "work" / "one"), "rev-parse",
                              "--is-shallow-repository"], capture_output=True, text=True)
    assert shallow.stdout.strip() == "true"
    assert "2 repositories available" in result.stdout


@needs_git
def test_wildcard_goes_on_when_one_clone_fails(tmp_path):
    _origin(tmp_path / "remote", "Owner", "one")
    result = _sync(tmp_path, "*", lister=_lister(tmp_path, "Owner/one\nOwner/missing\n"))
    assert result.returncode == 0, result.stderr
    assert "Owner/missing could not be cloned" in result.stderr
    assert not (tmp_path / "work" / "missing").exists()
    assert "1 repositories available" in result.stdout


@needs_git
def test_wildcard_fails_when_nothing_is_available(tmp_path):
    (tmp_path / "remote").mkdir()
    result = _sync(tmp_path, "*", lister=_lister(tmp_path, "Owner/missing\n"))
    assert result.returncode == 1
    assert "no repository is available" in result.stderr


@needs_git
def test_wildcard_keeps_the_copies_when_the_list_fails(tmp_path):
    _origin(tmp_path / "remote", "Owner", "one")
    assert _sync(tmp_path, "*", lister=_lister(tmp_path, "Owner/one\n")).returncode == 0
    result = _sync(tmp_path, "*", lister=_lister(tmp_path, None))
    assert result.returncode == 0, result.stderr
    assert "could not be read" in result.stderr
    assert (tmp_path / "work" / "one" / "README").exists()


@needs_git
def test_wildcard_fails_when_the_list_fails_and_there_is_no_copy(tmp_path):
    result = _sync(tmp_path, "*", lister=_lister(tmp_path, None))
    assert result.returncode == 1


@needs_git
def test_wildcard_keeps_a_copy_whose_refresh_fails(tmp_path):
    remote = tmp_path / "remote"
    _origin(remote, "Owner", "one")
    assert _sync(tmp_path, "*", lister=_lister(tmp_path, "Owner/one\n")).returncode == 0
    shutil.rmtree(remote / "Owner" / "one.git")
    result = _sync(tmp_path, "*", lister=_lister(tmp_path, "Owner/one\n"))
    assert result.returncode == 0
    assert "could not be refreshed" in result.stderr
    assert (tmp_path / "work" / "one" / "README").exists()


@needs_git
def test_wildcard_removes_a_copy_that_left_the_list(tmp_path):
    remote = tmp_path / "remote"
    _origin(remote, "Owner", "one")
    _origin(remote, "Owner", "two")
    assert _sync(tmp_path, "*", lister=_lister(tmp_path, "Owner/one\nOwner/two\n")).returncode == 0
    (tmp_path / "work" / "not-a-copy").mkdir()
    result = _sync(tmp_path, "*", lister=_lister(tmp_path, "Owner/one\n"))
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "work" / "two").exists()
    assert "removed two" in result.stdout
    assert (tmp_path / "work" / "not-a-copy").exists()   # only git copies are removed


@needs_git
def test_an_explicit_list_still_fails_on_a_missing_repository(tmp_path):
    _origin(tmp_path / "remote", "Owner", "one")
    result = _sync(tmp_path, "Owner/one Owner/missing")
    assert result.returncode == 1
    assert (tmp_path / "work" / "one" / "README").exists()
