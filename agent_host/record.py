"""The audit trail on the agent host (1.12a, 1.12b, D38).

Under the wrapper's state directory, mode 0700, files 0600:

- `records/<turn id>.json`: one record per turn. Time, project, session id,
  the prompt as received, the full reply, duration and outcome, and every
  gated action of the turn with its read-back and what became of it
  (read back, confirmed, cancelled, refused, not confirmed because
  confirmation is off).
- `actions/<turn id>.jsonl`: one line per tool call that can change
  something, written by the PostToolUse hook `agent_host/action-log`: the
  tool, the command or target, the exit status.
- kept for `retention_days` (90 by default). `prune()` removes what is
  older; the timer that calls it is phase 4 (tasks 4.1, 4.2).

Values shaped like secrets are redacted with the pattern the bridge's log
uses (bridge/logging_setup). The records are written by the agent's own
user: an account of what an agent that follows its rules did, not
tamper-proof evidence (D38, D40).
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agent_host import gatehash

REDACTED = "[REDACTED]"
# Values shaped like the credentials a turn on this host can meet: an
# Anthropic key, GitHub tokens, Slack tokens, AWS access key ids, and
# whatever follows "Bearer" or stands as the password of a URL.
_SECRETS = re.compile(
    r"sk-ant-[A-Za-z0-9_-]+"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|xox[abeoprs]-[A-Za-z0-9-]{8,}"
    r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
)
_BEARER = re.compile(r"(?i)\b(bearer|token)(\s+)[A-Za-z0-9._~+/=-]{8,}")
_URL_PASSWORD = re.compile(r"(://[^/\s:@]+:)[^@\s/]+@")


def redact(text: str) -> str:
    text = _SECRETS.sub(REDACTED, text)
    text = _BEARER.sub(lambda m: m.group(1) + m.group(2) + REDACTED, text)
    return _URL_PASSWORD.sub(lambda m: m.group(1) + REDACTED + "@", text)


@dataclass(frozen=True)
class TurnRecord:
    turn_id: str
    time: str
    project: str
    session_id: str | None
    prompt: str
    reply: str
    duration_s: float
    outcome: str
    gated_actions: list[dict] = field(default_factory=list)
    # Why the checkout's own settings were unsafe when the turn ended (1.7d,
    # D36), or None. They were safe when it started, or it would not have.
    settings_changed: str | None = None

    def redacted(self) -> "TurnRecord":
        return TurnRecord(
            turn_id=self.turn_id,
            time=self.time,
            project=self.project,
            session_id=self.session_id,
            prompt=redact(self.prompt),
            reply=redact(self.reply),
            duration_s=self.duration_s,
            outcome=self.outcome,
            gated_actions=self.gated_actions,
            settings_changed=self.settings_changed,
        )


def write_record(records_dir: Path, record: TurnRecord) -> Path:
    records_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = records_dir / f"{record.turn_id}.json"
    body = json.dumps(asdict(record.redacted()))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(body)
    return path


# --- the action log (1.12b) ---------------------------------------------------------

# The read-only file and search tools: everything else can change something.
READ_ONLY_TOOLS = frozenset({
    "Read", "Glob", "Grep", "LS", "NotebookRead", "TodoRead", "WebSearch", "WebFetch",
    "ToolSearch", "ListMcpResourcesTool", "ReadMcpResourceTool",
})
_MAX_LOGGED_CHARS = 2000


_EXIT_CODE = re.compile(r"\AExit code (\d{1,3})\b")
# The status of a line that says the call ran and failed.
FAILED_STATUSES = frozenset({"failed", "error", "interrupted"})


def action_line(payload: dict, now: str) -> dict | None:
    """The log line for one PostToolUse or PostToolUseFailure hook input, or
    None for a tool that only reads. Holds the tool, the command or target,
    and the exit status, never the tool's output.

    PostToolUseFailure carries `error` (text) and `is_interrupt` instead of
    a `tool_response`: the status is the exit code when the text starts with
    Claude Code's "Exit code N", else "interrupted" or "failed". The error
    text itself is output, and is not logged."""
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool, str) or not tool or tool in READ_ONLY_TOOLS:
        return None
    if not isinstance(tool_input, dict):
        tool_input = {}
    if tool == "Bash":
        what = tool_input.get("command")
    else:
        what = (tool_input.get("file_path") or tool_input.get("notebook_path")
                or tool_input.get("url") or tool_input.get("to"))
    if not isinstance(what, str):
        what = json.dumps(sorted(tool_input)) if tool_input else ""
    response = payload.get("tool_response")
    status: object = "ok"
    if payload.get("hook_event_name") == "PostToolUseFailure":
        error = payload.get("error")
        found = _EXIT_CODE.match(error) if isinstance(error, str) else None
        if found is not None and int(found.group(1)) != 0:
            status = int(found.group(1))
        elif payload.get("is_interrupt") is True:
            status = "interrupted"
        else:
            status = "failed"
    elif isinstance(response, dict):
        # The exit code of a Bash call where Claude Code gives one.
        for key in ("exit_code", "exitCode", "returncode", "returnCode", "code"):
            if isinstance(response.get(key), int):
                status = response[key]
                break
        else:
            if response.get("interrupted") is True:
                status = "interrupted"
            elif response.get("is_error") is True or response.get("error"):
                status = "error"
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    # The hash of the call as the gate computes it (without a push's
    # resolved address): the wrapper finds a confirmed call in this log by it.
    call_hash = None
    if cwd is not None and os.path.isabs(cwd):
        try:
            call_hash = gatehash.call_hash(tool, os.path.realpath(cwd), tool_input)
        except (ValueError, TypeError):
            call_hash = None
    return {
        "time": now,
        "tool": tool,
        "what": redact(what)[:_MAX_LOGGED_CHARS],
        "cwd": cwd,
        "status": status,
        "hash": call_hash,
    }


def failed_status(status: object) -> bool:
    """Does a logged status say that the call ran and failed?"""
    if isinstance(status, bool):
        return False
    if isinstance(status, int):
        return status != 0
    return status in FAILED_STATUSES


def ran(actions_dir: Path, turn_id: str, base_hash: str) -> str:
    """Did the call with this hash run in this turn, by the action log:
    "ran", "ran, failed" (it ran and exited non-zero, was interrupted or
    failed: the PostToolUseFailure line), "not run", or "unknown" when the
    log cannot say (it is not there, or cannot be read)."""
    try:
        text = (actions_dir / f"{turn_id}.jsonl").read_text()
    except OSError:
        return "unknown"
    for line in text.splitlines():
        try:
            entry = json.loads(line)
            if entry.get("hash") == base_hash:
                return "ran, failed" if failed_status(entry.get("status")) else "ran"
        except (ValueError, AttributeError):
            return "unknown"
    return "not run"


def append_action(actions_dir: Path, turn_id: str, line: dict) -> Path:
    actions_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = actions_dir / f"{turn_id}.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as fh:
        fh.write(json.dumps(line) + "\n")
    return path


# --- retention (1.12b; the timer is 4.1 and 4.2) ---------------------------------------

def prune(state_dir: Path, retention_days: int, now: float | None = None) -> list[Path]:
    """Remove records, action logs and gate state older than
    `retention_days`. Returns what was removed. Session ids, the lock and a
    pending call are not touched."""
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")
    cutoff = (time.time() if now is None else now) - retention_days * 86400
    removed: list[Path] = []
    for name in ("records", "actions", "gate"):
        directory = state_dir / name
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for path in entries:
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed.append(path)
            except OSError:
                continue
    return removed
