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
    )
    assert env["PATH"] == "/usr/bin"
    assert env["IDENT"] == "jarvis"
    assert env["JARVIS_TURN"] == "1"
    assert env["JARVIS_TURN_ID"] == "turn-1"


def test_never_sets_a_git_identity():
    env = build_child_env(
        Path("/nonexistent"),
        identity_env_var="IDENT", session_identity="jarvis", turn_id="turn-1",
    )
    for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                "GIT_COMMITTER_EMAIL"):
        assert key not in env


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
    )

    assert "LANG" not in env
    assert "BASH_ENV" not in env
    assert "LC_ALL" not in env
    assert env == {"PATH": "/usr/bin", "IDENT": "jarvis", "JARVIS_TURN": "1",
                   "JARVIS_TURN_ID": "turn-1"}
