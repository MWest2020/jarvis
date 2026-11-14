"""Redaction, the stdout split and rotation in bridge.logging_setup
(review of run-bridge-in-cluster)."""
import logging
import stat

import pytest

from bridge import logging_setup
from bridge.logging_setup import FILE_ONLY, REDACTED, RedactingFilter, StreamFilter

TOKEN = "f" * 64
OAUTH = "sk-ant-oat01-" + "x" * 40


def _record(msg, *args, **extra):
    record = logging.LogRecord("bridge.server", logging.INFO, __file__, 1, msg, args, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_redacts_known_secrets_in_message_and_args():
    f = RedactingFilter((TOKEN, OAUTH))
    record = _record("reply=%r and %s", f"JARVIS_TOKEN={TOKEN}", OAUTH)
    assert f.filter(record) is True
    message = record.getMessage()
    assert TOKEN not in message and OAUTH not in message
    assert message.count(REDACTED) == 2


def test_redacts_any_anthropic_key_even_if_unknown():
    record = _record("leaked sk-ant-api03-SomethingElse_123 here")
    RedactingFilter(()).filter(record)
    assert "sk-ant-" not in record.getMessage()


def test_ignores_empty_or_trivial_secrets():
    record = _record("a b c")
    RedactingFilter(("", None, "abc")).filter(record)
    assert record.getMessage() == "a b c"


def test_file_only_records_stay_off_the_stream():
    f = StreamFilter()
    assert f.filter(_record("summary")) is True
    assert f.filter(_record("full reply", **FILE_ONLY)) is False


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_setup, "LOG_MAX_BYTES", 2000)
    log_path = tmp_path / "state" / "bridge.log"
    logger = logging_setup.configure(log_path, secrets=(TOKEN, OAUTH))
    added = logger.handlers[-2:]
    yield logger, log_path
    for handler in added:
        logger.removeHandler(handler)
        handler.close()


def test_configured_logger_redacts_splits_and_rotates(capsys, configured):
    # capsys first, so the StreamHandler binds to the captured stderr.
    logger, log_path = configured
    child = logging.getLogger("bridge.server")
    child.info("project=demo full_reply=%r", f"the token is {TOKEN}", extra=FILE_ONLY)
    child.info("project=demo summary=%r", "All good.")

    text = log_path.read_text()
    assert "full_reply" in text and REDACTED in text and TOKEN not in text
    err = capsys.readouterr().err
    assert "summary" in err and "full_reply" not in err and TOKEN not in err

    for _ in range(100):
        child.info("filler line %s", "y" * 80, extra=FILE_ONLY)
    rotated = sorted(log_path.parent.glob("bridge.log*"))
    assert len(rotated) == logging_setup.LOG_BACKUPS + 1
    for path in rotated:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path
