import pytest

from agent_host.config import config_from_dict
from agent_host.projects import ProjectError, resolve_project


def _config(tmp_path, **overrides):
    data = {
        "state_dir": str(tmp_path / "state"),
        "environment_file": str(tmp_path / "environment"),
        "turn_settings": str(tmp_path / "turn-settings.json"),
        "prompt_file": str(tmp_path / "prompt.md"),
        "session_identity": "jarvis",
        "user_settings_path": str(tmp_path / "user-settings.json"),
    }
    data.update(overrides)
    return config_from_dict(data)


def _checkout(root, name):
    path = root / name
    (path / ".git").mkdir(parents=True)
    return path


def test_resolves_a_project_directly_under_the_root(tmp_path):
    root = tmp_path / "work"
    _checkout(root, "alpha")
    config = _config(tmp_path, projects_root=str(root))

    assert resolve_project(config, "alpha") == (root / "alpha").resolve()


def test_refuses_an_unknown_project_under_the_root(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    config = _config(tmp_path, projects_root=str(root))

    with pytest.raises(ProjectError):
        resolve_project(config, "ghost")


def test_refuses_a_name_that_is_a_path(tmp_path):
    root = tmp_path / "work"
    _checkout(root, "alpha")
    config = _config(tmp_path, projects_root=str(root))

    with pytest.raises(ProjectError):
        resolve_project(config, "../work/alpha")
    with pytest.raises(ProjectError):
        resolve_project(config, "a/b")


def test_refuses_a_symlinked_project(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    outside = _checkout(tmp_path, "outside")
    (root / "link").symlink_to(outside)
    config = _config(tmp_path, projects_root=str(root))

    with pytest.raises(ProjectError):
        resolve_project(config, "link")


def test_refuses_a_bare_repository(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    (root / "bare").mkdir()  # no .git entry: no work tree
    config = _config(tmp_path, projects_root=str(root))

    with pytest.raises(ProjectError):
        resolve_project(config, "bare")


def test_respects_exclusions(tmp_path):
    root = tmp_path / "work"
    _checkout(root, "chat")
    config = _config(tmp_path, projects_root=str(root), project_exclusions=["chat"])

    with pytest.raises(ProjectError):
        resolve_project(config, "chat")


def test_explicit_list_mode(tmp_path):
    repo = _checkout(tmp_path, "jarvis")
    config = _config(tmp_path, projects={"jarvis": str(repo)})

    assert resolve_project(config, "jarvis") == repo.resolve()


def test_explicit_list_mode_refuses_a_bare_repository(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    config = _config(tmp_path, projects={"bare": str(bare)})

    with pytest.raises(ProjectError):
        resolve_project(config, "bare")


def test_explicit_list_mode_refuses_an_unlisted_name(tmp_path):
    repo = _checkout(tmp_path, "jarvis")
    config = _config(tmp_path, projects={"jarvis": str(repo)})

    with pytest.raises(ProjectError):
        resolve_project(config, "other")
