"""Local text-to-speech (2.2): Kokoro, chosen for a natural voice (not Piper)."""
from __future__ import annotations

import io
from functools import lru_cache


@lru_cache(maxsize=1)
def _pipeline():
    from kokoro import KPipeline

    return KPipeline(lang_code="a")


def synthesize(text: str, *, voice: str = "af_heart") -> bytes:
    import numpy as np
    import soundfile as sf

    pipeline = _pipeline()
    chunks = [audio for _graphemes, _phonemes, audio in pipeline(text, voice=voice)]
    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype="float32")

    buffer = io.BytesIO()
    sf.write(buffer, audio, samplerate=24000, format="WAV")
    return buffer.getvalue()


def warm_up() -> None:
    """Load the pipeline and render a word, so the first voice turn does not pay for it."""
    synthesize("Ready.")
