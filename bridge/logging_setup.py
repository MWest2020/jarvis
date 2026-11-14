"""Logging for the bridge.

The full Claude Code reply and the recognized transcript are always logged
on the machine, even though only a short summary is ever spoken (D5,
Requirement: Spoken output). They go to the log file only, never to stdout:
in a cluster stdout is the container log, readable by anyone with
`kubectl logs`. Records marked `extra=FILE_ONLY` stay out of the stream.

Every handler redacts the bridge's own secrets (the phone's token, the Claude
credentials) and anything shaped like an Anthropic key, should a reply ever
quote them (review of run-bridge-in-cluster).
"""
from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

FILE_ONLY = {"file_only": True}
REDACTED = "[REDACTED]"
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 3

_ANTHROPIC_KEY = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
_MIN_SECRET_LEN = 8   # never "redact" an empty or trivial value


class RedactingFilter(logging.Filter):
    """Replaces known secret values (and sk-ant-… keys) in the formatted message."""

    def __init__(self, secrets: tuple[str | None, ...] = ()):
        super().__init__()
        self._secrets = tuple(s for s in secrets if s and len(s) >= _MIN_SECRET_LEN)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = message
        for secret in self._secrets:
            redacted = redacted.replace(secret, REDACTED)
        redacted = _ANTHROPIC_KEY.sub(REDACTED, redacted)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


class StreamFilter(logging.Filter):
    """Keeps FILE_ONLY records (transcripts, full replies) off stdout."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not getattr(record, "file_only", False)


class _PrivateRotatingFileHandler(RotatingFileHandler):
    """A rotating log whose files, rotated ones included, are created 0600."""

    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        return os.fdopen(fd, "a", encoding=self.encoding, errors=self.errors)


def configure(log_path: Path, *, secrets: tuple[str | None, ...] = ()) -> logging.Logger:
    # The log holds full replies, which can quote file contents: owner only.
    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_path.touch(mode=0o600, exist_ok=True)
    log_path.chmod(0o600)
    redact = RedactingFilter(secrets)
    logger = logging.getLogger("bridge")
    logger.setLevel(logging.INFO)

    file_handler = _PrivateRotatingFileHandler(
        log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    file_handler.addFilter(redact)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.addFilter(StreamFilter())
    stream_handler.addFilter(redact)
    logger.addHandler(stream_handler)
    return logger
