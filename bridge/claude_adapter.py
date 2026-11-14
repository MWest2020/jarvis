"""Calls headless Claude Code for one turn (D1), with a fixed, read-only
tool allow-list (1.3, limited for the weekend slice) and session resume
(D1, session continuity).
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from bridge.summary import SYSTEM_PROMPT_ADDITION

logger = logging.getLogger("bridge.claude")


class ClaudeTurnError(RuntimeError):
    """Raised when a claude -p turn fails or returns something unexpected."""


def run_turn(
    *,
    prompt: str,
    working_dir: Path,
    session_id: str | None,
    allowed_tools: tuple[str, ...],
    timeout_s: int,
) -> tuple[str, str]:
    """Run one turn of headless Claude Code. Returns (reply_text, session_id)."""
    command = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--append-system-prompt",
        SYSTEM_PROMPT_ADDITION,
        "--allowedTools",
        ",".join(allowed_tools),
    ]
    if session_id:
        command += ["--resume", session_id]

    try:
        completed = subprocess.run(
            command,
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeTurnError(f"claude -p timed out after {timeout_s}s") from exc

    if completed.returncode != 0:
        raise ClaudeTurnError(
            f"claude -p exited {completed.returncode}: {completed.stderr.strip()}"
        )

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeTurnError(f"could not parse claude -p output: {exc}") from exc

    reply_text = payload.get("result")
    new_session_id = payload.get("session_id")
    if reply_text is None or new_session_id is None:
        raise ClaudeTurnError(f"unexpected claude -p JSON shape: {payload!r}")

    return reply_text, new_session_id
