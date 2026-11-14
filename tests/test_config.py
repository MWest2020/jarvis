import pytest

from bridge.config import ConfigError, load_config


def _set_minimal_env(monkeypatch, tmp_path, **overrides):
    env = {
        "JARVIS_BIND_HOST": "100.64.0.1",
        "JARVIS_TOKEN": "secret",
        "JARVIS_PROJECTS": f"demo={tmp_path}",
    }
    env.update(overrides)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_loads_a_minimal_config(monkeypatch, tmp_path):
    _set_minimal_env(monkeypatch, tmp_path)
    config = load_config()
    assert config.bind_host == "100.64.0.1"
    assert config.token == "secret"
    assert config.project("demo").working_dir == tmp_path
    assert config.allowed_tools == ("Read", "Glob", "Grep")


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "::0", "0:0:0:0:0:0:0:0"])
def test_rejects_a_wildcard_bind_host(monkeypatch, tmp_path, host):
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_BIND_HOST=host)
    with pytest.raises(ConfigError):
        load_config()


def test_rejects_a_non_ip_bind_host(monkeypatch, tmp_path):
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_BIND_HOST="not-an-ip")
    with pytest.raises(ConfigError):
        load_config()


def test_requires_a_token(monkeypatch, tmp_path):
    _set_minimal_env(monkeypatch, tmp_path)
    monkeypatch.delenv("JARVIS_TOKEN")
    with pytest.raises(ConfigError):
        load_config()


def test_requires_at_least_one_project(monkeypatch, tmp_path):
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_PROJECTS="")
    with pytest.raises(ConfigError):
        load_config()


def test_unknown_project_raises(monkeypatch, tmp_path):
    _set_minimal_env(monkeypatch, tmp_path)
    config = load_config()
    with pytest.raises(ConfigError):
        config.project("does-not-exist")


@pytest.mark.parametrize("host", ["8.8.8.8", "192.168.1.10", "2001:db8::1"])
def test_rejects_a_non_tailscale_bind_host(monkeypatch, tmp_path, host):
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_BIND_HOST=host)
    with pytest.raises(ConfigError):
        load_config()


@pytest.mark.parametrize("host", ["100.90.159.10", "fd7a:115c:a1e0::1", "127.0.0.1"])
def test_accepts_tailscale_and_loopback(monkeypatch, tmp_path, host):
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_BIND_HOST=host)
    assert load_config().bind_host == host
