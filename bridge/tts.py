"""Local text-to-speech (2.2): Kokoro, chosen for a natural voice (not Piper)."""
from __future__ import annotations

import io
import os
from functools import lru_cache

# The voice when JARVIS_KOKORO_VOICE is not set: British male. The image bakes this one
# and af_heart (deploy/Dockerfile, KOKORO_VOICES).
DEFAULT_VOICE = "bm_george"


def lang_code(voice: str) -> str:
    """Kokoro's language code for a voice: British English for a `b…` voice (bm_george,
    bf_emma), American English for everything else, as before."""
    return "b" if voice[:1].lower() == "b" else "a"


# One pipeline (one loaded model) at a time: the bridge speaks with one voice.
@lru_cache(maxsize=1)
def _pipeline(code: str):
    from kokoro import KPipeline

    return KPipeline(lang_code=code, repo_id="hexgrad/Kokoro-82M")


def synthesize(text: str, *, voice: str = DEFAULT_VOICE) -> bytes:
    import numpy as np
    import soundfile as sf

    pipeline = _pipeline(lang_code(voice))
    chunks = [audio for _graphemes, _phonemes, audio in pipeline(text, voice=voice)]
    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype="float32")

    buffer = io.BytesIO()
    sf.write(buffer, audio, samplerate=24000, format="WAV")
    return buffer.getvalue()


def warm_up(voice: str | None = None) -> None:
    """Load the pipeline and render a word, so the first voice turn does not pay for it.

    Without an argument it warms the voice the bridge will speak with
    (JARVIS_KOKORO_VOICE, as bridge.config reads it), so the right language is loaded.
    """
    synthesize("Ready.", voice=voice or os.environ.get("JARVIS_KOKORO_VOICE") or DEFAULT_VOICE)
