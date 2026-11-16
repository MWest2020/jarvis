"""The checkout settings rule, as one function (tasks 1.7d, 1.14a; D36)."""
import json
import os

import pytest

from agent_host import checkout_settings
from agent_host.checkout_settings import SETTINGS_FILES, UNSAFE_KEYS, problem


def _checkout(tmp_path, name="demo"):
    path = tmp_path / name
    (path / ".git").mkdir(parents=True)
    return path


def _settings(directory, name, content):
    target = directory / ".claude" / name
    target.parent.mkdir(exist_ok=True)
    target.write_text(content if isinstance(content, str) else json.dumps(content))
    return target


def test_a_checkout_without_settings_is_safe(tmp_path):
    checkout = _checkout(tmp_path)
    assert problem(checkout) is None
    (checkout / ".claude").mkdir()
    assert problem(checkout) is None


@pytest.mark.parametrize("name", SETTINGS_FILES)
@pytest.mark.parametrize("key,value", [
    ("env", {"JARVIS_GRANT": "abc"}),
    ("env", {}),
    ("hooks", {"PreToolUse": []}),
    ("hooks", {}),
    ("disableAllHooks", True),
    ("disableAllHooks", False),  # the key is enough, whatever it says
])
def test_each_of_the_three_keys_in_either_file_is_unsafe(tmp_path, name, key, value):
    checkout = _checkout(tmp_path)
    _settings(checkout, name, {"permissions": {"allow": []}, key: value})
    found = problem(checkout)
    assert found is not None
    assert key in found and name in found


@pytest.mark.parametrize("name", SETTINGS_FILES)
@pytest.mark.parametrize("content", [
    {},
    {"model": "x", "includeCoAuthoredBy": False},
    {"$schema": "https://example.invalid/schema.json", "cleanupPeriodDays": 30, "outputStyle": "x"},
    {"permissions": {}},
    {"permissions": {"deny": ["Bash(rm:*)"], "ask": ["Bash(git push:*)"]}},
])
def test_settings_with_only_harmless_keys_are_safe(tmp_path, name, content):
    checkout = _checkout(tmp_path)
    _settings(checkout, name, content)
    assert problem(checkout) is None


@pytest.mark.parametrize("name", SETTINGS_FILES)
@pytest.mark.parametrize("content,said", [
    # Everything that runs a command, sets the environment, adds a server
    # or widens a permission, and every key nobody has thought of.
    ({"apiKeyHelper": "/tmp/x.sh"}, "apiKeyHelper"),
    ({"statusLine": {"type": "command", "command": "x"}}, "statusLine"),
    ({"awsAuthRefresh": "x"}, "awsAuthRefresh"),
    ({"otelHeadersHelper": "x"}, "otelHeadersHelper"),
    ({"mcpServers": {"x": {"command": "x"}}}, "mcpServers"),
    ({"enableAllProjectMcpServers": True}, "enableAllProjectMcpServers"),
    ({"enabledMcpjsonServers": ["x"]}, "enabledMcpjsonServers"),
    ({"model": "x", "somethingNew": 1}, "somethingNew"),
    ({"Env": {}}, "Env"),
    ({"permissions": {"allow": ["Bash(ls:*)"]}}, "permissions.allow"),
    ({"permissions": {"deny": [], "defaultMode": "bypassPermissions"}}, "permissions.defaultMode"),
    ({"permissions": {"additionalDirectories": ["/"]}}, "permissions.additionalDirectories"),
    ({"permissions": ["allow"]}, "permissions"),
    ({"permissions": {"env": 1}}, "permissions.env"),
])
def test_any_other_key_makes_the_checkout_unsafe(tmp_path, name, content, said):
    checkout = _checkout(tmp_path)
    _settings(checkout, name, content)
    found = problem(checkout)
    assert found == f"{name} sets {said}"


def test_a_settings_file_that_is_a_symlink_is_unsafe_whatever_it_points_at(tmp_path):
    checkout = _checkout(tmp_path)
    harmless = tmp_path / "harmless.json"
    harmless.write_text("{}")
    (checkout / ".claude").mkdir()
    (checkout / ".claude" / "settings.json").symlink_to(harmless)
    assert problem(checkout) == "settings.json is a symlink"
    (checkout / ".claude" / "settings.json").unlink()
    assert problem(checkout) is None
    # ... and so is a `.claude` that is itself a symlink.
    other = tmp_path / "other-claude"
    other.mkdir()
    (other / "settings.json").write_text("{}")
    (checkout / ".claude").rmdir()
    (checkout / ".claude").symlink_to(other)
    assert problem(checkout) == ".claude is a symlink"


def test_a_fifo_where_the_settings_should_be_is_unsafe_and_does_not_hang(tmp_path):
    checkout = _checkout(tmp_path)
    (checkout / ".claude").mkdir()
    os.mkfifo(checkout / ".claude" / "settings.local.json")
    # Nobody ever writes to it: a plain open() would wait for ever.
    assert problem(checkout) == "settings.local.json cannot be read"


def test_a_settings_file_that_is_too_large_is_unsafe(tmp_path):
    checkout = _checkout(tmp_path)
    _settings(checkout, "settings.json", '{"model": "' + "x" * (checkout_settings.MAX_SETTINGS_BYTES) + '"}')
    assert problem(checkout) == "settings.json cannot be read"


@pytest.mark.parametrize("content", ["", "{not json", "[]", '"env"', "null", "\xff\xfe"])
def test_a_settings_file_that_is_not_a_json_object_is_unsafe(tmp_path, content):
    checkout = _checkout(tmp_path)
    _settings(checkout, "settings.json", content)
    assert problem(checkout) is not None


def test_a_settings_file_that_cannot_be_read_is_unsafe(tmp_path):
    checkout = _checkout(tmp_path)
    (checkout / ".claude").mkdir()
    # A dangling symlink, a directory, and (when not root) a file without read permission.
    (checkout / ".claude" / "settings.json").symlink_to(tmp_path / "nowhere")
    assert problem(checkout) is not None
    (checkout / ".claude" / "settings.json").unlink()
    (checkout / ".claude" / "settings.json").mkdir()
    assert problem(checkout) is not None
    (checkout / ".claude" / "settings.json").rmdir()
    locked = _settings(checkout, "settings.local.json", {})
    assert problem(checkout) is None
    if os.geteuid() != 0:
        locked.chmod(0)
        try:
            assert problem(checkout) is not None
        finally:
            locked.chmod(0o600)


def test_the_top_level_of_the_calls_own_worktree_is_checked_too(tmp_path):
    checkout = _checkout(tmp_path)
    worktree = tmp_path / "worktrees" / "feature"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: elsewhere\n")  # a worktree has a .git file
    deep = worktree / "src" / "pkg"
    deep.mkdir(parents=True)

    assert problem(checkout, deep) is None
    planted = _settings(worktree, "settings.json", {"env": {"X": "1"}})
    assert problem(checkout, deep) is not None
    assert problem(checkout, worktree) is not None
    assert problem(checkout) is None  # the turn checkout itself is clean
    planted.unlink()
    assert problem(checkout, deep) is None  # removing the key lifts it


def test_settings_below_the_top_level_and_outside_any_checkout_are_not_project_settings(tmp_path):
    checkout = _checkout(tmp_path)
    sub = checkout / "sub"
    sub.mkdir()
    _settings(sub, "settings.json", {"hooks": {}})
    assert problem(checkout, sub) is None
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    _settings(lonely, "settings.json", {"hooks": {}})
    assert problem(checkout, lonely) is None


def test_the_users_own_settings_are_not_a_projects_when_home_is_a_checkout(tmp_path):
    checkout = _checkout(tmp_path)
    home = _checkout(tmp_path, "home")
    user_settings = _settings(home, "settings.json", {"hooks": {"PreToolUse": []}})
    assert problem(checkout, home) is not None
    assert problem(checkout, home, user_settings_path=user_settings) is None
    # ... and only that directory is excused.
    _settings(checkout, "settings.json", {"hooks": {}})
    assert problem(checkout, home, user_settings_path=user_settings) is not None


def test_the_top_level_is_found_without_running_git(tmp_path):
    checkout = _checkout(tmp_path)
    deep = checkout / "a" / "b"
    deep.mkdir(parents=True)
    assert checkout_settings.git_top_level(deep) == checkout.resolve()
    assert checkout_settings.git_top_level(checkout) == checkout.resolve()
    assert checkout_settings.git_top_level(checkout / "does" / "not" / "exist") == checkout.resolve()


def test_the_three_keys_are_exactly_those_of_d36():
    assert set(UNSAFE_KEYS) == {"env", "hooks", "disableAllHooks"}
    assert set(SETTINGS_FILES) == {"settings.json", "settings.local.json"}
