from agent_host.sessions import SessionStore, is_canonical_uuid

VALID = "11111111-1111-1111-1111-111111111111"


def test_a_canonical_uuid_is_recognized():
    assert is_canonical_uuid(VALID)
    assert is_canonical_uuid(VALID.upper())


def test_not_a_uuid_is_rejected():
    assert not is_canonical_uuid("--dangerously-skip-permissions")
    assert not is_canonical_uuid("--version")
    assert not is_canonical_uuid("not-a-uuid")
    assert not is_canonical_uuid(VALID + "-extra")


def test_a_fresh_project_has_no_session_and_is_not_discarded(tmp_path):
    store = SessionStore(tmp_path / "sessions.json")
    session_id, discarded = store.get("jarvis")
    assert session_id is None
    assert discarded is False


def test_round_trips_a_valid_session_id(tmp_path):
    store = SessionStore(tmp_path / "sessions.json")
    store.set("jarvis", VALID)
    session_id, discarded = store.get("jarvis")
    assert session_id == VALID
    assert discarded is False


def test_an_invalid_stored_value_is_discarded_never_resumed(tmp_path):
    """1.3, D33: a stored id of --dangerously-skip-permissions or --version
    must never reach argv. The store itself is the backstop: get() refuses
    to hand back anything that is not a canonical UUID."""
    store = SessionStore(tmp_path / "sessions.json")
    for planted in ("--dangerously-skip-permissions", "--version"):
        store.set("jarvis", planted)
        session_id, discarded = store.get("jarvis")
        assert session_id is None
        assert discarded is True
