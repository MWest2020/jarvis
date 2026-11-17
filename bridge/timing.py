"""Per-stage timing of one voice turn on the bridge (voice-and-speed).

A turn is: the upload is read, the recording is transcribed (`stt`), in
`full` mode the detected speech is measured (`vad`), the agent answers
(`agent`: the ssh round trip to the agent host, or the read-only turn), the
summary is spoken (`tts`), and the WAV is written back. `total` runs from the
request's head being parsed to the reply being written.

One INFO line per turn goes to the log (the file and stdout), and the same
numbers, minus the write, go to the phone in `X-Jarvis-Timing`. No text of
the turn is in either: only milliseconds, seconds of audio and a count of
characters.
"""
from __future__ import annotations

import io
import time
import wave
from collections.abc import Callable
from contextlib import contextmanager

# Stages in the order a turn runs them; the log line and the header use it.
STAGES = ("read", "stt", "vad", "agent", "tts", "write")
HEADER = "X-Jarvis-Timing"


def wav_seconds(data: bytes) -> float | None:
    """The length of a WAV recording in seconds, from its header; None when the
    bytes are not a WAV file this module can read."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            rate = wav.getframerate()
            return wav.getnframes() / rate if rate > 0 else None
    except (wave.Error, EOFError, OSError, ValueError):
        return None


class TurnTimer:
    """Milliseconds per stage, plus a few sizes. Not thread-safe: one per request."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.started = clock()
        self.ms: dict[str, int] = {}
        self.audio_in_s: float | None = None
        self.audio_out_s: float | None = None
        self.spoken_chars: int | None = None

    @contextmanager
    def stage(self, name: str):
        begun = self._clock()
        try:
            yield
        finally:
            self.add(name, self._clock() - begun)

    def add(self, name: str, seconds: float) -> None:
        self.ms[name] = self.ms.get(name, 0) + int(round(seconds * 1000))

    def wrap(self, name: str, func: Callable[[], float]) -> Callable[[], float]:
        """`func`, timed as stage `name` each time it is called."""
        def timed():
            with self.stage(name):
                return func()
        return timed

    def total_ms(self) -> int:
        return int(round((self._clock() - self.started) * 1000))

    def header_value(self) -> str:
        """`stt=812;agent=7604;tts=5410;total=13901`: the stages so far, and the
        total up to now (the reply's body is not written yet)."""
        parts = [f"{name}={self.ms[name]}" for name in STAGES if name in self.ms]
        parts.append(f"total={self.total_ms()}")
        return ";".join(parts)

    def log_fields(self) -> str:
        """`stt_ms=812 agent_ms=7604 ... total_ms=14020 audio_in_s=5.5 ...`."""
        parts = [f"{name}_ms={self.ms[name]}" for name in STAGES if name in self.ms]
        parts.append(f"total_ms={self.total_ms()}")
        if self.audio_in_s is not None:
            parts.append(f"audio_in_s={self.audio_in_s:.1f}")
        if self.audio_out_s is not None:
            parts.append(f"audio_out_s={self.audio_out_s:.1f}")
        if self.spoken_chars is not None:
            parts.append(f"spoken_chars={self.spoken_chars}")
        return " ".join(parts)
