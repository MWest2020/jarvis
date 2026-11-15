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


SAMPLE_RATE = 16000


def speech_seconds(audio_bytes: bytes) -> float:
    """Seconds of detected speech in a recording (jarvis-full-agent D36, step 5).

    Whisper writes short words ("yes") from noise, so a spoken confirmation only
    counts when the recording really holds speech. Measured with the voice
    activity detector that ships inside faster-whisper (Silero, a file in the
    package: nothing is downloaded), without padding around what it finds.
    """
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    audio = decode_audio(io.BytesIO(audio_bytes), sampling_rate=SAMPLE_RATE)
    chunks = get_speech_timestamps(
        audio, VadOptions(speech_pad_ms=0, min_silence_duration_ms=100),
        sampling_rate=SAMPLE_RATE,
    )
    return sum(chunk["end"] - chunk["start"] for chunk in chunks) / SAMPLE_RATE
