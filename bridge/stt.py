"""Local speech-to-text (2.1): faster-whisper, CPU-friendly for the weekend test."""
from __future__ import annotations

import io
from functools import lru_cache


@lru_cache(maxsize=1)
def _model(model_size: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device="cpu", compute_type="int8")


def transcribe(audio_bytes: bytes, *, model_size: str = "base") -> str:
    model = _model(model_size)
    segments, _info = model.transcribe(io.BytesIO(audio_bytes))
    return " ".join(segment.text.strip() for segment in segments).strip()


def warm_up(model_size: str = "base") -> None:
    """Load the model now, so the first voice turn does not pay for it."""
    _model(model_size)
