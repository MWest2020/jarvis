"""Reads that cannot block and cannot grow without a bound (D36, host check 12).

A hook that runs into its timeout fails open in Claude Code (host check 12,
2.1.322), so no step of the gate may wait without a bound. A file the
agent's user can write (its own Claude Code settings, the gate's state) can
be replaced by a FIFO, which blocks an ordinary open, or by something huge.
Every file the gate reads goes through `read_regular`: opened with
`O_NONBLOCK`, accepted only when it is a regular file, and read up to a cap.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path


class TooLarge(OSError):
    """The file is larger than the reader allows."""


def read_regular(path: Path | str, cap: int, *, follow: bool = True,
                 owner: int | None = None) -> bytes:
    """The bytes of a regular file of at most `cap` bytes. Never blocks on a
    FIFO or a device. With `follow=False` a symlink is refused; with `owner`
    a file owned by another uid is refused. Raises OSError otherwise."""
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    if not follow:
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(f"{path} is not a regular file")
        if owner is not None and info.st_uid != owner:
            raise OSError(f"{path} belongs to another user")
        if info.st_size > cap:
            raise TooLarge(f"{path} is larger than {cap} bytes")
        chunks: list[bytes] = []
        size = 0
        # /proc files report a size of 0: read until EOF, never past the cap.
        while True:
            chunk = os.read(fd, min(65536, cap + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > cap:
                raise TooLarge(f"{path} is larger than {cap} bytes")
        return b"".join(chunks)
    finally:
        os.close(fd)


def read_text(path: Path | str, cap: int, **kwargs) -> str:
    return read_regular(path, cap, **kwargs).decode("utf-8")


# A Claude Code settings file (a checkout's, the user's, the turn settings):
# small, and read on every gated call.
MAX_SETTINGS_BYTES = 1024 * 1024
# One hook input (a tool call: a Write may carry a file's content).
MAX_HOOK_INPUT_BYTES = 16 * 1024 * 1024


def read_stdin(cap: int = MAX_HOOK_INPUT_BYTES) -> bytes | None:
    """All of stdin, or None when it is longer than `cap`. A stdin that never
    ends is the caller's deadline's to cut (agent_host/gate)."""
    chunks: list[bytes] = []
    size = 0
    while size <= cap:
        chunk = os.read(0, min(1 << 20, cap + 1 - size))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
    return None


def open_append(path: Path | str, mode: int = 0o600) -> int:
    """A file descriptor for appending to a regular file: a symlink or a FIFO
    planted in its place fails at once instead of redirecting or blocking."""
    flags = (os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK
             | getattr(os, "O_CLOEXEC", 0))
    fd = os.open(path, flags, mode)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path} is not a regular file")
    except BaseException:
        os.close(fd)
        raise
    return fd


class BudgetExhausted(OSError):
    """The time budget for waiting (git lookups, locks) is spent."""


class Budget:
    """One time budget shared by every step of a decision that waits on
    something outside it, so that together they stay inside the gate's
    deadline (host check 12)."""

    def __init__(self, seconds: float):
        import time
        self._clock = time.monotonic
        self._end = self._clock() + seconds

    def remaining(self) -> float:
        return max(0.0, self._end - self._clock())

    def take(self, wanted: float) -> float:
        """How long one step may wait: `wanted`, or what is left. Raises when
        nothing is left."""
        left = self.remaining()
        if left <= 0:
            raise BudgetExhausted("the time budget is spent")
        return min(wanted, left)
