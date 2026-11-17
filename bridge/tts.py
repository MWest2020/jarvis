"""Local text-to-speech (2.2): Kokoro, chosen for a natural voice (not Piper)."""
from __future__ import annotations

import io
import os
import re
from collections.abc import Iterator
from functools import lru_cache

# The voice when JARVIS_KOKORO_VOICE is not set: British male. The image bakes this one
# and af_heart (deploy/Dockerfile, KOKORO_VOICES).
DEFAULT_VOICE = "bm_george"

# Kokoro renders at this rate; the WAV and the streamed reply both carry it.
SAMPLE_RATE = 24000
# The streamed reply's format (jarvis-streaming-speech S2): 16-bit linear PCM, mono,
# big-endian as RFC 2586 defines audio/L16.
STREAM_CONTENT_TYPE = f"audio/L16;rate={SAMPLE_RATE};channels=1"
# S4: a streamed reply is rendered one sentence at a time. Kokoro's own split is at line
# breaks and then only at 510 phonemes, so a summary of 160 characters is one piece.
# This splits at a line break, or at the white space after ".", "!", "?" or "…" when
# the next word does not start in lower case ("e.g. this" stays one piece). The words
# themselves are never changed.
SENTENCE_SPLIT = r"\n+|(?<=[.!?…])\s+(?![a-z])"
# Kokoro's default split_pattern: what synthesize() uses, and a read-back (S5).
KOKORO_SPLIT = r"\n+"


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


def to_l16(audio) -> bytes:
    """Kokoro's float samples (-1..1) as big-endian 16-bit PCM."""
    import numpy as np

    samples = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    return (samples * 32767.0).round().astype(">i2").tobytes()


def stream_pcm(text: str, *, voice: str = DEFAULT_VOICE, whole: bool = False) -> Iterator[bytes]:
    """The spoken reply as big-endian L16 at SAMPLE_RATE, one piece per sentence, each
    rendered only when the previous one has been taken (S4).

    `whole`: Kokoro's own split, exactly the call synthesize() makes, so the audio is
    that of the WAV reply. For a read-back (S5), which must not be split by us.
    """
    pipeline = _pipeline(lang_code(voice))
    pattern = KOKORO_SPLIT if whole else SENTENCE_SPLIT
    for _graphemes, _phonemes, audio in pipeline(text, voice=voice, split_pattern=pattern):
        if audio is None:
            continue
        piece = to_l16(audio)
        if piece:
            yield piece


def sentences(text: str) -> list[str]:
    """How SENTENCE_SPLIT cuts a text, as Kokoro applies it (re.split of the stripped
    text, empty pieces skipped). For tests and the docs; Kokoro does the real split."""
    return [piece for piece in re.split(SENTENCE_SPLIT, text.strip()) if piece.strip()]


def warm_up(voice: str | None = None) -> None:
    """Load the pipeline and render a word, so the first voice turn does not pay for it.

    Without an argument it warms the voice the bridge will speak with
    (JARVIS_KOKORO_VOICE, as bridge.config reads it), so the right language is loaded.
    """
    synthesize("Ready.", voice=voice or os.environ.get("JARVIS_KOKORO_VOICE") or DEFAULT_VOICE)
