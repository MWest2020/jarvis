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


@pytest.mark.parametrize(
    "host", ["8.8.8.8", "192.168.1.10", "2001:db8::1", "::ffff:10.0.0.1", "169.254.1.1"]
)
def test_rejects_a_non_tailscale_bind_host(monkeypatch, tmp_path, host):
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_BIND_HOST=host)
    with pytest.raises(ConfigError):
        load_config()


@pytest.mark.parametrize("host", ["100.90.159.10", "fd7a:115c:a1e0::1", "127.0.0.1"])
def test_accepts_tailscale_and_loopback(monkeypatch, tmp_path, host):
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_BIND_HOST=host)
    assert load_config().bind_host == host


# run-bridge-in-cluster: the explicit opt-in for a pod IP behind a Tailscale proxy.

@pytest.mark.parametrize("host", ["10.200.3.17", "172.16.0.5", "192.168.178.50"])
def test_rejects_a_pod_ip_without_the_opt_in(monkeypatch, tmp_path, host):
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_BIND_HOST=host)
    with pytest.raises(ConfigError):
        load_config()


@pytest.mark.parametrize("host", ["10.200.3.17", "172.31.255.1", "192.168.1.10", "100.64.0.1"])
def test_opt_in_accepts_a_private_address(monkeypatch, tmp_path, host):
    _set_minimal_env(
        monkeypatch, tmp_path, JARVIS_BIND_HOST=host, JARVIS_BEHIND_TAILSCALE_PROXY="1"
    )
    config = load_config()
    assert config.bind_host == host
    assert config.behind_tailscale_proxy is True


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "8.8.8.8", "172.32.0.1", "11.0.0.1", "2001:db8::1", "fd00::1",
     "::ffff:10.0.0.1", "169.254.1.1", "169.254.169.254"],
)
def test_opt_in_still_rejects_wildcard_and_public(monkeypatch, tmp_path, host):
    _set_minimal_env(
        monkeypatch, tmp_path, JARVIS_BIND_HOST=host, JARVIS_BEHIND_TAILSCALE_PROXY="1"
    )
    with pytest.raises(ConfigError):
        load_config()


@pytest.mark.parametrize("value", ["true", "yes", "2", " 1"])
def test_opt_in_must_be_exactly_1(monkeypatch, tmp_path, value):
    _set_minimal_env(
        monkeypatch, tmp_path, JARVIS_BIND_HOST="10.0.0.1", JARVIS_BEHIND_TAILSCALE_PROXY=value
    )
    with pytest.raises(ConfigError):
        load_config()


def test_opt_in_is_logged_loudly(monkeypatch, tmp_path, caplog):
    _set_minimal_env(
        monkeypatch, tmp_path, JARVIS_BIND_HOST="10.200.3.17", JARVIS_BEHIND_TAILSCALE_PROXY="1"
    )
    with caplog.at_level("WARNING", logger="bridge.config"):
        load_config()
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, "the opt-in must log a warning"
    assert "10.200.3.17" in warnings[0].getMessage()
    assert "NOT on a Tailscale interface" in warnings[0].getMessage()


def test_without_opt_in_nothing_is_logged(monkeypatch, tmp_path, caplog):
    _set_minimal_env(monkeypatch, tmp_path)
    with caplog.at_level("WARNING", logger="bridge.config"):
        config = load_config()
    assert config.behind_tailscale_proxy is False
    assert not [r for r in caplog.records if r.name == "bridge.config"]


def test_opt_in_0_means_off(monkeypatch, tmp_path):
    _set_minimal_env(
        monkeypatch, tmp_path, JARVIS_BIND_HOST="10.0.0.1", JARVIS_BEHIND_TAILSCALE_PROXY="0"
    )
    with pytest.raises(ConfigError):
        load_config()


# add-project-list: JARVIS_PROJECTS=* with JARVIS_PROJECTS_ROOT.

def _checkout(root, name):
    (root / name / ".git").mkdir(parents=True)
    return root / name


def test_wildcard_finds_every_git_checkout_under_the_root(monkeypatch, tmp_path):
    root = tmp_path / "work"
    _checkout(root, "bravo")
    _checkout(root, "alpha")
    (root / "not-a-repo").mkdir()
    (root / "a-file").write_text("x")
    _checkout(root, ".hidden")
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_PROJECTS="*", JARVIS_PROJECTS_ROOT=str(root))
    config = load_config()
    assert config.project_names() == ["alpha", "bravo"]
    assert config.project("alpha").working_dir == root / "alpha"
    with pytest.raises(ConfigError):
        config.project("not-a-repo")
    with pytest.raises(ConfigError):
        config.project("..")


def test_wildcard_skips_symlinks(monkeypatch, tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    outside = _checkout(tmp_path, "outside")
    (root / "link").symlink_to(outside)
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_PROJECTS="*", JARVIS_PROJECTS_ROOT=str(root))
    assert load_config().project_names() == []


def test_wildcard_needs_a_root(monkeypatch, tmp_path):
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_PROJECTS="*")
    monkeypatch.delenv("JARVIS_PROJECTS_ROOT", raising=False)
    with pytest.raises(ConfigError, match="JARVIS_PROJECTS_ROOT"):
        load_config()


@pytest.mark.parametrize("root", ["relative/path", "/does/not/exist"])
def test_wildcard_root_must_be_an_existing_absolute_directory(monkeypatch, tmp_path, root):
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_PROJECTS="*", JARVIS_PROJECTS_ROOT=root)
    with pytest.raises(ConfigError):
        load_config()


def test_root_without_the_wildcard_is_refused(monkeypatch, tmp_path):
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_PROJECTS_ROOT=str(tmp_path))
    with pytest.raises(ConfigError):
        load_config()


def test_wildcard_with_an_empty_root_starts_and_warns(monkeypatch, tmp_path, caplog):
    root = tmp_path / "work"
    root.mkdir()
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_PROJECTS="*", JARVIS_PROJECTS_ROOT=str(root))
    with caplog.at_level("WARNING", logger="bridge.config"):
        config = load_config()
    assert config.project_names() == []
    assert any("no git checkouts" in r.getMessage() for r in caplog.records)


def test_wildcard_rescans_after_the_cache_expires(tmp_path):
    from bridge.config import ProjectScanner
    clock = [100.0]
    scanner = ProjectScanner(tmp_path, ttl_s=10, clock=lambda: clock[0])
    assert scanner.projects() == {}
    _checkout(tmp_path, "new")
    clock[0] = 105.0
    assert scanner.projects() == {}
    clock[0] = 110.0
    assert list(scanner.projects()) == ["new"]


def test_explicit_projects_are_listed_sorted(monkeypatch, tmp_path):
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_PROJECTS=f"zed={tmp_path},abc={tmp_path}")
    assert load_config().project_names() == ["abc", "zed"]
