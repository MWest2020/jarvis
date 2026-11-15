"""One turn at a time on the whole agent host, and a memory floor (1.5, D34).

A small host (Mark's: 4 GB, about 1.3 GB free while the coding session
runs) must not be pushed into swap by a second concurrent turn; the lock and
the floor answer `busy` / `low_memory` instead.
"""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


class Busy(Exception):
    """Another turn already holds the lock."""


@contextmanager
def acquire_turn_lock(lock_path: Path):
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy(f"another turn holds {lock_path}") from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def available_memory_mb(meminfo_path: Path = Path("/proc/meminfo")) -> float:
    """`MemAvailable` from /proc/meminfo, in MB. +inf where it cannot be read.

    Failing open here (never refusing a turn just because the metric is
    unavailable, e.g. in a container without /proc/meminfo) is intentional:
    the floor is a safety margin, not the only thing standing between the
    host and swapping.
    """
    try:
        text = meminfo_path.read_text()
    except OSError:
        return float("inf")
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1]) / 1024
                except ValueError:
                    return float("inf")
    return float("inf")
