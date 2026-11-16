"""The configuration keys the gate adds (phase 1b1): each has a default,
and none of them is a constant of anyone's own setup."""
from pathlib import Path

import pytest

from agent_host.config import ConfigError, config_from_dict, load_config

BASE = {
    "projects_root": "/srv/projects",
    "state_dir": "/var/lib/jarvis-agent",
    "environment_file": "/etc/jarvis-agent/environment",
    "turn_settings": "/usr/local/lib/jarvis-agent/turn-settings.json",
    "prompt_file": "/usr/local/lib/jarvis-agent/prompt.md",
    "session_identity": "jarvis",
    "user_settings_path": "/home/someone/.claude/settings.json",
}


def test_the_gates_keys_have_defaults():
    config = config_from_dict(dict(BASE))
    assert config.rules_dir == Path("/etc/jarvis-agent/rules.d")
    assert config.managed_settings_path == Path("/etc/claude-code/managed-settings.json")
    assert config.own_owners == ()
    assert config.config_path == Path("/etc/jarvis-agent/config.toml")


def test_the_gates_keys_can_be_set():
    config = config_from_dict(dict(
        BASE, rules_dir="/srv/rules.d", managed_settings_path="/srv/managed.json",
        own_owners=["me", "my-org"],
    ))
    assert config.rules_dir == Path("/srv/rules.d")
    assert config.managed_settings_path == Path("/srv/managed.json")
    assert config.own_owners == ("me", "my-org")


@pytest.mark.parametrize("value", ["me", [1], [""], {"me": True}])
def test_own_owners_must_be_a_list_of_names(value):
    with pytest.raises(ConfigError):
        config_from_dict(dict(BASE, own_owners=value))


def test_a_loaded_configuration_knows_the_file_it_came_from(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("\n".join(f'{key} = "{value}"' for key, value in BASE.items()) + "\n")
    assert load_config(path).config_path == path
