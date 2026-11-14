"""Logging for the bridge.

The full Claude Code reply and the recognized transcript are always logged
on the machine, even though only a short summary is ever spoken (D5,
Requirement: Spoken output).
"""
from __future__ import annotations

import logging
from pathlib import Path


def configure(log_path: Path) -> logging.Logger:
    # The log holds full replies, which can quote file contents: owner only.
    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_path.touch(mode=0o600, exist_ok=True)
    log_path.chmod(0o600)
    logger = logging.getLogger("bridge")
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    return logger
