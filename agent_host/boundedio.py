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
