from bridge.sessions import SessionStore


def test_round_trips_a_session_id(tmp_path):
    store = SessionStore(tmp_path / "sessions.json")
    assert store.get("jarvis") is None

    store.set("jarvis", "session-1")
    assert store.get("jarvis") == "session-1"

    store.set("jarvis", "session-2")
    assert store.get("jarvis") == "session-2"


def test_keeps_separate_sessions_per_project(tmp_path):
    store = SessionStore(tmp_path / "sessions.json")
    store.set("project-a", "session-a")
    store.set("project-b", "session-b")

    assert store.get("project-a") == "session-a"
    assert store.get("project-b") == "session-b"


def test_survives_a_corrupt_store_file(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text("not json")

    store = SessionStore(path)
    assert store.get("jarvis") is None
    store.set("jarvis", "session-1")
    assert store.get("jarvis") == "session-1"
