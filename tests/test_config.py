import pytest

from bridge.config import ConfigError, load_config


def _set_minimal_env(monkeypatch, tmp_path, **overrides):
    env = {
        "JARVIS_BIND_HOST": "100.64.0.1",
        "JARVIS_TOKEN": "secret",
        "JARVIS_PROJECTS": f"demo={tmp_path}",
    }
    env.update(overrides)
    # Whatever the machine running the tests has set for `full` mode is not part
    # of a test.
    import os
    for key in list(os.environ):
        if key.startswith(("JARVIS_AGENT_", "JARVIS_CONFIRM_", "JARVIS_RECAP_", "JARVIS_NOTE_",
                           "JARVIS_DRIVE_", "JARVIS_JOURNAL_", "JARVIS_STATE_")):
            monkeypatch.delenv(key)
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


@pytest.mark.parametrize("host", ["100.101.102.103", "fd7a:115c:a1e0::1", "127.0.0.1"])
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


# jarvis-full-agent: JARVIS_AGENT_MODE and the settings of `full` (D30, D32).

def _full_env(monkeypatch, tmp_path, **overrides):
    secrets = tmp_path / "secrets"
    secrets.mkdir(exist_ok=True)
    for name in ("key", "known_hosts"):
        path = secrets / name
        if not path.exists():
            path.write_text("x\n")
        path.chmod(0o400)
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    env = {
        "JARVIS_PROJECTS": f"demo={project}",
        "JARVIS_AGENT_MODE": "full",
        "JARVIS_AGENT_HOST": "agent.example",
        "JARVIS_AGENT_USER": "mark",
        "JARVIS_AGENT_KEY_FILE": str(secrets / "key"),
        "JARVIS_AGENT_KNOWN_HOSTS_FILE": str(secrets / "known_hosts"),
    }
    env.update(overrides)
    _set_minimal_env(monkeypatch, tmp_path, **env)
    return secrets, project


@pytest.mark.parametrize("value", [None, "", "readonly"])
def test_readonly_is_the_default_mode_and_reads_no_agent_setting(monkeypatch, tmp_path, value):
    _set_minimal_env(monkeypatch, tmp_path)
    if value is not None:
        monkeypatch.setenv("JARVIS_AGENT_MODE", value)
    # Settings of `full` mode, even broken ones, are not looked at.
    monkeypatch.setenv("JARVIS_AGENT_KEY_FILE", "/nonexistent/key")
    monkeypatch.setenv("JARVIS_CONFIRM_MIN_SPEECH_S", "nonsense")
    config = load_config()
    assert config.agent_mode == "readonly" and config.agent_host is None


@pytest.mark.parametrize("value", ["FULL", "Full", "write", "1", "full "])
def test_rejects_an_unknown_agent_mode(monkeypatch, tmp_path, value):
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_AGENT_MODE=value)
    with pytest.raises(ConfigError, match="JARVIS_AGENT_MODE"):
        load_config()


def test_loads_full_mode_with_its_defaults(monkeypatch, tmp_path):
    secrets, _ = _full_env(monkeypatch, tmp_path)
    config = load_config()
    agent = config.agent_host
    assert config.agent_mode == "full"
    assert (agent.host, agent.port, agent.user, agent.timeout_s) == ("agent.example", 22, "mark", 170)
    assert agent.key_file == secrets / "key"
    assert agent.known_hosts_file == secrets / "known_hosts"
    assert config.confirm_yes == ("yes", "ja") and config.confirm_no == ("no", "nee")
    assert config.confirm_min_speech_s == 0.3


@pytest.mark.parametrize("missing", ["JARVIS_AGENT_HOST", "JARVIS_AGENT_USER",
                                     "JARVIS_AGENT_KEY_FILE", "JARVIS_AGENT_KNOWN_HOSTS_FILE"])
def test_full_mode_requires_every_setting(monkeypatch, tmp_path, missing):
    _full_env(monkeypatch, tmp_path)
    monkeypatch.delenv(missing)
    with pytest.raises(ConfigError, match=missing):
        load_config()


@pytest.mark.parametrize("which", ["JARVIS_AGENT_KEY_FILE", "JARVIS_AGENT_KNOWN_HOSTS_FILE"])
def test_full_mode_refuses_a_missing_file(monkeypatch, tmp_path, which):
    _full_env(monkeypatch, tmp_path, **{which: str(tmp_path / "secrets" / "nope")})
    with pytest.raises(ConfigError, match=which):
        load_config()


@pytest.mark.parametrize("which", ["key", "known_hosts"])
@pytest.mark.parametrize("mode", [0o440, 0o404, 0o644, 0o420, 0o402, 0o401])
def test_full_mode_refuses_a_file_others_can_use(monkeypatch, tmp_path, which, mode):
    secrets, _ = _full_env(monkeypatch, tmp_path)
    (secrets / which).chmod(mode)
    with pytest.raises(ConfigError, match="group or others"):
        load_config()


def test_full_mode_accepts_0600(monkeypatch, tmp_path):
    secrets, _ = _full_env(monkeypatch, tmp_path)
    (secrets / "key").chmod(0o600)
    assert load_config().agent_mode == "full"


@pytest.mark.parametrize("which", ["JARVIS_AGENT_KEY_FILE", "JARVIS_AGENT_KNOWN_HOSTS_FILE"])
def test_full_mode_refuses_a_file_inside_a_project(monkeypatch, tmp_path, which):
    _, project = _full_env(monkeypatch, tmp_path)
    inside = project / "deploy" / "key"
    inside.parent.mkdir()
    inside.write_text("x\n")
    inside.chmod(0o400)
    monkeypatch.setenv(which, str(inside))
    with pytest.raises(ConfigError, match="inside the project directory"):
        load_config()


def test_full_mode_refuses_a_link_that_leads_into_a_project(monkeypatch, tmp_path):
    secrets, project = _full_env(monkeypatch, tmp_path)
    inside = project / "key"
    inside.write_text("x\n")
    inside.chmod(0o400)
    link = secrets / "link"
    link.symlink_to(inside)
    monkeypatch.setenv("JARVIS_AGENT_KEY_FILE", str(link))
    with pytest.raises(ConfigError, match="inside the project directory"):
        load_config()


def test_full_mode_refuses_a_file_under_the_projects_root(monkeypatch, tmp_path):
    root = tmp_path / "work"
    (root / "one" / ".git").mkdir(parents=True)
    key = root / "key"
    key.write_text("x\n")
    key.chmod(0o400)
    _full_env(monkeypatch, tmp_path, JARVIS_PROJECTS="*", JARVIS_PROJECTS_ROOT=str(root),
              JARVIS_AGENT_KEY_FILE=str(key))
    with pytest.raises(ConfigError, match="inside the project directory"):
        load_config()


def test_full_mode_follows_a_mounted_secrets_links(monkeypatch, tmp_path):
    # A Kubernetes volume mounts each file as a link into a hidden directory.
    secrets, _ = _full_env(monkeypatch, tmp_path)
    data = secrets / "..data"
    data.mkdir()
    (secrets / "key").rename(data / "key")
    (secrets / "key").symlink_to(data / "key")
    assert load_config().agent_host.key_file == secrets / "key"


@pytest.mark.parametrize("name,value", [
    ("JARVIS_AGENT_KEY_FILE", "relative/key"),
    ("JARVIS_AGENT_KNOWN_HOSTS_FILE", "/secrets/known hosts"),
    ("JARVIS_AGENT_KEY_FILE", "/secrets/%d/key"),
    ("JARVIS_AGENT_KEY_FILE", "/secrets/${HOME}/key"),
    ("JARVIS_AGENT_KEY_FILE", "/secrets/~/key"),
    ("JARVIS_AGENT_KEY_FILE", '/secrets/"key"'),
    ("JARVIS_AGENT_KEY_FILE", "/secrets/'key'"),
    ("JARVIS_AGENT_KEY_FILE", "/secrets/k\\ey"),
    ("JARVIS_AGENT_KEY_FILE", "/secrets/key\t"),
    ("JARVIS_AGENT_KNOWN_HOSTS_FILE", "/secrets/%h"),
    ("JARVIS_AGENT_KNOWN_HOSTS_FILE", "/secrets/$x"),
    ("JARVIS_AGENT_HOST", "-oProxyCommand=sh"),
    ("JARVIS_AGENT_HOST", "agent.example evil"),
    ("JARVIS_AGENT_HOST", "mark@agent"),
    ("JARVIS_AGENT_USER", "-l"),
    ("JARVIS_AGENT_USER", "mark@other"),
    ("JARVIS_AGENT_PORT", "0"),
    ("JARVIS_AGENT_PORT", "70000"),
    ("JARVIS_AGENT_PORT", "ssh"),
    ("JARVIS_AGENT_TIMEOUT_S", "-5"),
    ("JARVIS_AGENT_TIMEOUT_S", "176"),      # the phone gives up at 180 s
    ("JARVIS_AGENT_TIMEOUT_S", "660"),
    ("JARVIS_CONFIRM_MIN_SPEECH_S", "0"),
    ("JARVIS_CONFIRM_MIN_SPEECH_S", "-1"),
    ("JARVIS_CONFIRM_MIN_SPEECH_S", "nan"),
    ("JARVIS_CONFIRM_MIN_SPEECH_S", "a bit"),
    ("JARVIS_CONFIRM_YES", "Yes"),
    ("JARVIS_CONFIRM_YES", "yes!"),
    ("JARVIS_CONFIRM_YES", " , "),
    ("JARVIS_CONFIRM_YES", "yes,no"),
    ("JARVIS_CONFIRM_NO", "no,ja"),
])
def test_full_mode_refuses_a_setting_that_is_not_what_it_should_be(monkeypatch, tmp_path,
                                                                    name, value):
    _full_env(monkeypatch, tmp_path, **{name: value})
    with pytest.raises(ConfigError):
        load_config()


def test_full_mode_takes_its_own_words_port_and_minimum(monkeypatch, tmp_path):
    _full_env(monkeypatch, tmp_path, JARVIS_CONFIRM_YES="yes, ja, go ahead",
              JARVIS_CONFIRM_NO="no", JARVIS_CONFIRM_MIN_SPEECH_S="0.5",
              JARVIS_AGENT_PORT="2222", JARVIS_AGENT_HOST="100.64.0.7",
              JARVIS_AGENT_TIMEOUT_S="175")
    config = load_config()
    assert config.confirm_yes == ("yes", "ja", "go ahead") and config.confirm_no == ("no",)
    assert config.confirm_min_speech_s == 0.5
    assert (config.agent_host.port, config.agent_host.host, config.agent_host.timeout_s) == (
        2222, "100.64.0.7", 175)


def test_a_confirm_word_is_kept_in_one_unicode_form(monkeypatch, tmp_path):
    # "sí" written with a combining accent in the configuration: Whisper writes the
    # single character, and the two must meet.
    _full_env(monkeypatch, tmp_path, JARVIS_CONFIRM_YES="yes,si\u0301")
    assert load_config().confirm_yes == ("yes", "s\u00ed")


def test_the_default_voice_is_the_male_one(monkeypatch, tmp_path):
    _set_minimal_env(monkeypatch, tmp_path)
    monkeypatch.delenv("JARVIS_KOKORO_VOICE", raising=False)
    assert load_config().kokoro_voice == "bm_george"


def test_the_voice_can_be_switched_back_by_env(monkeypatch, tmp_path):
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_KOKORO_VOICE="af_heart")
    assert load_config().kokoro_voice == "af_heart"


# --- Whisper's decoding (voice-and-speed) ---------------------------------------------

def test_whisper_decoding_is_faster_whispers_default_when_unset(monkeypatch, tmp_path):
    _set_minimal_env(monkeypatch, tmp_path)
    monkeypatch.delenv("JARVIS_WHISPER_BEAM_SIZE", raising=False)
    monkeypatch.delenv("JARVIS_WHISPER_LANGUAGE", raising=False)
    config = load_config()
    assert (config.whisper_beam_size, config.whisper_language) == (5, None)
    assert config.whisper_options == {}


def test_whisper_beam_and_language_can_be_set(monkeypatch, tmp_path):
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_WHISPER_BEAM_SIZE="1",
                     JARVIS_WHISPER_LANGUAGE="en")
    assert load_config().whisper_options == {"beam_size": 1, "language": "en"}


@pytest.mark.parametrize("name,value", [
    ("JARVIS_WHISPER_BEAM_SIZE", "0"), ("JARVIS_WHISPER_BEAM_SIZE", "11"),
    ("JARVIS_WHISPER_BEAM_SIZE", "+1"), ("JARVIS_WHISPER_BEAM_SIZE", "one"),
    ("JARVIS_WHISPER_LANGUAGE", "English"), ("JARVIS_WHISPER_LANGUAGE", "EN"),
    ("JARVIS_WHISPER_LANGUAGE", "e"),
])
def test_bad_whisper_settings_are_refused_at_start(monkeypatch, tmp_path, name, value):
    _set_minimal_env(monkeypatch, tmp_path, **{name: value})
    with pytest.raises(ConfigError):
        load_config()


# add-drive-mode phase 1: the journal and the drive's own words.

def test_the_drive_defaults(monkeypatch, tmp_path):
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_STATE_DIR=str(tmp_path / "state"))
    config = load_config()
    assert config.journal_path == tmp_path / "state" / "journal"
    assert (config.drive_gap_minutes, config.journal_days) == (30, 30)
    assert config.recap_words == ("where was i", "what was i doing")
    assert config.note_words == ("note", "make a note", "take a note")


def test_the_drive_settings_from_the_environment(monkeypatch, tmp_path):
    _set_minimal_env(monkeypatch, tmp_path, JARVIS_DRIVE_GAP_MINUTES="45",
                     JARVIS_JOURNAL_DAYS="7", JARVIS_RECAP_WORDS="where was i, recap",
                     JARVIS_NOTE_WORDS="note,memo")
    config = load_config()
    assert (config.drive_gap_minutes, config.journal_days) == (45, 7)
    assert config.recap_words == ("where was i", "recap")
    assert config.note_words == ("note", "memo")


@pytest.mark.parametrize("name, value", [
    ("JARVIS_DRIVE_GAP_MINUTES", "0"),
    ("JARVIS_DRIVE_GAP_MINUTES", "1441"),
    ("JARVIS_DRIVE_GAP_MINUTES", "half an hour"),
    ("JARVIS_JOURNAL_DAYS", "0"),
    ("JARVIS_JOURNAL_DAYS", "-1"),
    ("JARVIS_RECAP_WORDS", "Where was I?"),
    ("JARVIS_NOTE_WORDS", " , "),
    ("JARVIS_NOTE_WORDS", "where was i"),          # also a recap word
])
def test_bad_drive_settings_are_refused(monkeypatch, tmp_path, name, value):
    _set_minimal_env(monkeypatch, tmp_path, **{name: value})
    with pytest.raises(ConfigError):
        load_config()


def test_a_control_word_cannot_be_a_confirmation_word(monkeypatch, tmp_path):
    _full_env(monkeypatch, tmp_path, JARVIS_NOTE_WORDS="note,yes")
    with pytest.raises(ConfigError, match="control word and a confirmation word"):
        load_config()
