"""Per-project Claude Code session id storage (D1, session continuity).

Stored as a small JSON file outside git so a restart of the bridge does not
lose the running conversation for each project.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path


class SessionStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    def get(self, project: str) -> str | None:
        with self._lock:
            return self._read().get(project)

    def set(self, project: str, session_id: str) -> None:
        with self._lock:
            data = self._read()
            data[project] = session_id
            self._write(data)

    def _read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self, data: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(self._path)
