"""Local speech-to-text (2.1): faster-whisper, CPU-friendly for the weekend test."""
from __future__ import annotations

import io
from functools import lru_cache

SAMPLE_RATE = 16000


@lru_cache(maxsize=1)
def _model(model_size: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device="cpu", compute_type="int8")


def transcribe(audio_bytes, *, model_size: str = "base", beam_size: int = 5,
               language: str | None = None, languages: tuple[str, ...] = ()) -> str:
    """The recording as text. Takes the uploaded bytes, or what decode() made of them.

    `beam_size` and `language` are faster-whisper's own (defaults 5 and detection).
    A fixed language skips the detection pass, measured on agent-lxc for base/int8
    on 4 to 9 s recordings: 1.7-1.9 s down to 0.9-1.1 s; beam 1 takes another
    0.05-0.2 s off (docs/how-to/bridge-service.md, "Where a turn's time goes").

    `languages` (with no fixed `language`): detection picks the likeliest of these only
    (pick_language), then the recording is transcribed in it. The same one detection
    pass as faster-whisper's own, so no slower.
    """
    model = _model(model_size)
    source = _source(audio_bytes)
    if language is None and languages:
        if isinstance(audio_bytes, (bytes, bytearray, memoryview)):
            source = decode(bytes(audio_bytes))
        _top, _p, probabilities = model.detect_language(source)
        language = pick_language(probabilities, languages)
    segments, _info = model.transcribe(source, beam_size=beam_size, language=language)
    return " ".join(segment.text.strip() for segment in segments).strip()


def pick_language(probabilities, languages: tuple[str, ...]) -> str:
    """Of `languages`, the one Whisper's detection finds likeliest. `probabilities`:
    (code, probability) pairs as faster-whisper's detect_language gives them. The
    first of `languages` when none of them is in the list."""
    scores = dict(probabilities)
    return max(languages, key=lambda code: (scores.get(code, 0.0), -languages.index(code)))


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
