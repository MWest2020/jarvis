"""Local speech-to-text (2.1): faster-whisper, CPU-friendly for the weekend test."""
from __future__ import annotations

import io
from functools import lru_cache

SAMPLE_RATE = 16000


@lru_cache(maxsize=1)
def _model(model_size: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device="cpu", compute_type="int8")


def transcribe(audio_bytes, *, model_size: str = "base") -> str:
    """The recording as text. Takes the uploaded bytes, or what decode() made of them."""
    model = _model(model_size)
    segments, _info = model.transcribe(_source(audio_bytes))
    return " ".join(segment.text.strip() for segment in segments).strip()


def _source(audio):
    return io.BytesIO(audio) if isinstance(audio, (bytes, bytearray, memoryview)) else audio


def decode(audio_bytes: bytes):
    """The recording as the samples Whisper works on (16 kHz, mono), decoded once.

    `full` mode needs them twice, for the transcript and for the detected speech;
    faster-whisper does exactly this decoding itself when it is handed bytes.
    """
    from faster_whisper.audio import decode_audio

    return decode_audio(io.BytesIO(audio_bytes), sampling_rate=SAMPLE_RATE)


def warm_up(model_size: str = "base") -> None:
    """Load the model now, so the first voice turn does not pay for it."""
    _model(model_size)


def speech_seconds(audio) -> float:
    """Seconds of detected speech in a recording (jarvis-full-agent D36, step 5).

    Whisper writes short words ("yes") from noise, so a spoken confirmation only
    counts when the recording really holds speech. Measured with the voice
    activity detector that ships inside faster-whisper (Silero, a file in the
    package: nothing is downloaded), without padding around what it finds.
    Takes the uploaded bytes, or what decode() made of them.
    """
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    if isinstance(audio, (bytes, bytearray, memoryview)):
        audio = decode(bytes(audio))
    chunks = get_speech_timestamps(
        audio, VadOptions(speech_pad_ms=0, min_silence_duration_ms=100),
        sampling_rate=SAMPLE_RATE,
    )
    return sum(chunk["end"] - chunk["start"] for chunk in chunks) / SAMPLE_RATE
