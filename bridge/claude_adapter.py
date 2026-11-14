"""Calls headless Claude Code for one turn (D1), with a fixed, read-only
tool allow-list (1.3, limited for the weekend slice) and session resume
(D1, session continuity).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
from pathlib import Path

from bridge.summary import SYSTEM_PROMPT_ADDITION

logger = logging.getLogger("bridge.claude")


class ClaudeTurnError(RuntimeError):
    """Raised when a claude -p turn fails or returns something unexpected."""


# Tools that must never run from a voice turn in the weekend slice, denied on top
# of --tools as a second layer. Read(//proc/**) also binds Grep and Glob (they follow
# Read deny rules): Grep on /proc/self/environ, or on a committed symlink to it,
# returned the process environment, credentials included (review of
# run-bridge-in-cluster, reproduced with Claude Code 2.1.322).
DENIED_TOOLS: tuple[str, ...] = (
    "Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Agent",
    "Read(//proc/**)",
)


def build_command(*, allowed_tools: tuple[str, ...], session_id: str | None) -> list[str]:
    """argv for one headless turn (D1, D6).

    --allowedTools alone only pre-approves; it does not restrict. User and project
    settings (an "auto" permission mode, allowed Bash rules) and MCP servers would
    still apply. So: --tools limits which tools exist at all, dontAsk refuses
    anything not pre-approved instead of asking, and --strict-mcp-config without an
    --mcp-config loads no MCP servers. --restricted confines the file tools to the
    working directory and ignores user, project and local settings files, so a
    cloned repo's .claude/settings.json (hooks, permission rules) has no effect.
    """
    tools = ",".join(allowed_tools)
    command = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--append-system-prompt",
        SYSTEM_PROMPT_ADDITION,
        "--tools",
        tools,
        "--allowedTools",
        tools,
        "--disallowedTools",
        ",".join(DENIED_TOOLS),
        "--permission-mode",
        "dontAsk",
        "--strict-mcp-config",
        "--restricted",
    ]
    if session_id:
        command += ["--resume", session_id]
    return command


def run_turn(
    *,
    prompt: str,
    working_dir: Path,
    session_id: str | None,
    allowed_tools: tuple[str, ...],
    timeout_s: int,
) -> tuple[str, str]:
    """Run one turn of headless Claude Code. Returns (reply_text, session_id)."""
    command = build_command(allowed_tools=allowed_tools, session_id=session_id)
    # The prompt goes over stdin, never argv: a transcript starting with "-" would
    # otherwise be parsed as options. The JARVIS_* variables (the phone's token) are
    # not passed to the child. That alone does not hide them: the bridge's own
    # /proc/<pid>/environ still holds them, and the child's environment holds its
    # Claude credentials. What keeps the file tools away from /proc is --restricted
    # (cwd only) plus the Read(//proc/**) deny rule in build_command; the log and
    # the spoken summary are redacted on top (logging_setup, summary).
    env = {k: v for k, v in os.environ.items() if not k.startswith("JARVIS_")}
    proc = subprocess.Popen(
        command,
        cwd=working_dir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(prompt, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        # Kill the whole group, so tool children (ripgrep and the like) die too.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise ClaudeTurnError(f"claude -p timed out after {timeout_s}s") from exc

    completed = subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
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
