"""Per-project session id storage on the agent host (1.3, D33).

The state directory is writable by the agent itself, so a stored id is never
trusted as-is: it is read back only when it is a canonical UUID. Anything
else (including a planted `--dangerously-skip-permissions` or `--version`)
is discarded, and the caller is told a new session started (notice
`new_session`), never passed anywhere near argv.

How long a session lives (an hour idle, or until the agent closes the
conversation) is agent_host.lifetime.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def is_canonical_uuid(value: str) -> bool:
    return bool(_UUID_RE.fullmatch(value))


class KeyValueStore:
    """One small JSON file under the state directory, mode 0700 / 0600.

    Used directly for the session-rotation fallback's day marker (D34), and
    wrapped by SessionStore below, which adds the UUID check on read.
    """

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._read().get(key)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            data = self._read()
            data[key] = value
            self._write(data)

    def delete(self, key: str) -> None:
        with self._lock:
            data = self._read()
            if key in data:
                del data[key]
                self._write(data)

    def _read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        # The agent can write this file (D40): anything but an object is empty.
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, str]) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(data))
        tmp.replace(self._path)


class SessionStore:
    """One JSON file under the state directory, mode 0700 / 0600."""

    def __init__(self, path: Path):
        self._store = KeyValueStore(path)

    def get(self, project: str) -> tuple[str | None, bool]:
        """(a resumable session id, or None; True if a stored value was discarded)."""
        raw = self._store.get(project)
        if raw is None:
            return None, False
        if is_canonical_uuid(raw):
            return raw, False
        return None, True

    def set(self, project: str, session_id: str) -> None:
        self._store.set(project, session_id)

    def drop(self, project: str) -> None:
        """Forget the project's session: its next turn starts a new one."""
        self._store.delete(project)

