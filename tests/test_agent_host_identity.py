from pathlib import Path

from agent_host.identity import build_child_env, read_environment_file


def test_reads_key_value_pairs(tmp_path):
    path = tmp_path / "environment"
    path.write_text("PATH=/usr/bin:/bin\n# a comment\n\nHOME=/home/jarvis\n")
    assert read_environment_file(path) == {"PATH": "/usr/bin:/bin", "HOME": "/home/jarvis"}


def test_a_missing_environment_file_is_just_empty(tmp_path):
    assert read_environment_file(tmp_path / "nope") == {}


def test_adds_identity_and_turn_variables(tmp_path):
    path = tmp_path / "environment"
    path.write_text("PATH=/usr/bin\n")
    env = build_child_env(
        path, identity_env_var="IDENT", session_identity="jarvis", turn_id="turn-1",
        config_path=Path("/etc/jarvis-agent/config.toml"), checkout=Path("/srv/projects/demo"),
    )
    assert env["PATH"] == "/usr/bin"
    assert env["IDENT"] == "jarvis"
    assert env["JARVIS_TURN"] == "1"
    assert env["JARVIS_TURN_ID"] == "turn-1"


def test_never_sets_a_git_identity():
    env = build_child_env(
        Path("/nonexistent"),
        identity_env_var="IDENT", session_identity="jarvis", turn_id="turn-1",
        config_path=Path("/etc/jarvis-agent/config.toml"), checkout=Path("/srv/projects/demo"),
    )
    for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                "GIT_COMMITTER_EMAIL"):
        assert key not in env
    assert not [key for key in env if key.startswith(("GIT_AUTHOR", "GIT_COMMITTER"))]


def test_a_planted_lang_and_bash_env_never_reach_the_child(monkeypatch, tmp_path):
    """1.6b: a client-controlled or shell-rc-planted variable in the
    wrapper's own process environment must never reach the claude child -
    build_child_env never reads os.environ at all, only the env file."""
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("BASH_ENV", "/tmp/evil.sh")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    path = tmp_path / "environment"
    path.write_text("PATH=/usr/bin\n")

    env = build_child_env(
        path, identity_env_var="IDENT", session_identity="jarvis", turn_id="turn-1",
        config_path=Path("/etc/jarvis-agent/config.toml"), checkout=Path("/srv/projects/demo"),
    )

    assert "LANG" not in env
    assert "BASH_ENV" not in env
    assert "LC_ALL" not in env
    assert env == {"PATH": "/usr/bin", "IDENT": "jarvis", "JARVIS_TURN": "1",
                   "JARVIS_TURN_ID": "turn-1",
                   "JARVIS_AGENT_CONFIG": "/etc/jarvis-agent/config.toml",
                   "JARVIS_TURN_CHECKOUT": "/srv/projects/demo"}


def test_the_gate_is_told_where_the_configuration_and_the_turn_checkout_are(tmp_path):
    """1b1: the gate is a separate process started by Claude Code; what it
    cannot derive travels in the environment the wrapper builds."""
    env = build_child_env(
        tmp_path / "nope", identity_env_var="IDENT", session_identity="jarvis",
        turn_id="turn-1", config_path=tmp_path / "config.toml", checkout=tmp_path / "demo",
    )
    assert env["JARVIS_AGENT_CONFIG"] == str(tmp_path / "config.toml")
    assert env["JARVIS_TURN_CHECKOUT"] == str(tmp_path / "demo")


def test_the_environment_file_cannot_override_what_the_wrapper_sets(tmp_path):
    path = tmp_path / "environment"
    path.write_text(
        "JARVIS_AGENT_CONFIG=/elsewhere/config.toml\nJARVIS_TURN_CHECKOUT=/elsewhere\n"
        "JARVIS_TURN=0\n"
    )
    env = build_child_env(
        path, identity_env_var="IDENT", session_identity="jarvis", turn_id="turn-1",
        config_path=tmp_path / "config.toml", checkout=tmp_path / "demo",
    )
    assert env["JARVIS_AGENT_CONFIG"] == str(tmp_path / "config.toml")
    assert env["JARVIS_TURN_CHECKOUT"] == str(tmp_path / "demo")
    assert env["JARVIS_TURN"] == "1"


def test_an_ordinary_turn_never_starts_with_a_grant(tmp_path):
    """1b2: the grant of a confirmed call is added by the wrapper to a
    confirming turn only. Not even the root-owned environment file can
    plant one."""
    path = tmp_path / "environment"
    path.write_text("JARVIS_GRANT=" + "0" * 64 + "\nJARVIS_GRANT_BASE=" + "0" * 64 + "\n")
    env = build_child_env(
        path, identity_env_var="IDENT", session_identity="jarvis", turn_id="turn-1",
        config_path=tmp_path / "config.toml", checkout=tmp_path / "demo",
    )
    assert "JARVIS_GRANT" not in env and "JARVIS_GRANT_BASE" not in env
