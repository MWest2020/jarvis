"""The per-turn audit record on the agent host (1.12a, D38).

One JSON file per turn under the state directory's `records/` (mode 0700 /
0600 like the session store): time, project, session id, the prompt as
received, the full reply, duration and outcome. Phase 1a has no gate yet, so
`gated_actions` is always empty here; phase 1b appends to it. Redacted with
the same pattern the bridge's log uses (bridge/logging_setup), should a
reply ever quote something shaped like an Anthropic key.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

_ANTHROPIC_KEY = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
REDACTED = "[REDACTED]"


def redact(text: str) -> str:
    return _ANTHROPIC_KEY.sub(REDACTED, text)


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
        )


def write_record(records_dir: Path, record: TurnRecord) -> Path:
    records_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = records_dir / f"{record.turn_id}.json"
    body = json.dumps(asdict(record.redacted()))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(body)
    return path
