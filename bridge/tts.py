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
# The look-ahead also refuses white space, so a run of spaces cannot backtrack to a
# shorter match that ends before a lower-case word ("e.g.  this" stays one piece).
SENTENCE_SPLIT = r"\n+|(?<=[.!?…])\s+(?!\s|[a-z])"
# Kokoro's default split_pattern: what synthesize() uses, and a read-back (S5).
KOKORO_SPLIT = r"\n+"
# A sentence shorter than this is rendered together with the next one. Kokoro renders
# at about 0.045 s per character and speaks at about 0.078 s per character (measured on
# agent-lxc, 2026-11-17), so the next piece is ready before the previous one has been
# spoken unless it is much longer: "Done." alone (0.6 s of speech) followed by a
# sentence of 70 characters (3 s to render) left a gap of over two seconds.
MIN_PIECE_CHARS = 30


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


def word_audio(word: str, *, voice: str = DEFAULT_VOICE):
    """A word (or a sentence) as Kokoro's float samples at SAMPLE_RATE, for singing."""
    import numpy as np

    pipeline = _pipeline(lang_code(voice))
    chunks = [np.asarray(audio, dtype=np.float32)
              for _graphemes, _phonemes, audio in pipeline(word, voice=voice)
              if audio is not None]
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


# The pause between a spoken sentence and the song, in seconds.
SONG_PAUSE_S = 0.3


def sing(song, *, voice: str = DEFAULT_VOICE):
    """The song (bridge.singing.SongRequest) rendered with the bridge's own voice:
    float samples at SAMPLE_RATE. The song itself is at most singing.MAX_SECONDS long;
    the bridge's sentences around it (`before`, `after`, a notice say) are spoken
    before and after it, as in a spoken reply."""
    import numpy as np

    from bridge import singing

    sung = singing.render(song.tune, song.lyrics,
                          lambda word: word_audio(word, voice=voice)).samples
    pause = np.zeros(int(SONG_PAUSE_S * SAMPLE_RATE), dtype=np.float32)
    parts = []
    if song.before:
        parts += [word_audio(song.before, voice=voice), pause]
    parts.append(np.asarray(sung, dtype=np.float32))
    if song.after:
        parts += [pause, word_audio(song.after, voice=voice)]
    return np.concatenate(parts)


def beatbox(beat, *, voice: str = DEFAULT_VOICE):
    """The beat (bridge.beatbox.BeatRequest) with the bridge's own voice: the agent's
    line said first, then the beat (at most beatbox.MAX_SECONDS), with the bridge's
    sentences around them as in a sung reply. Float samples at SAMPLE_RATE."""
    import numpy as np

    from bridge import beatbox as beats

    beaten = beats.render(beat.pattern, lambda word: word_audio(word, voice=voice)).samples
    pause = np.zeros(int(SONG_PAUSE_S * SAMPLE_RATE), dtype=np.float32)
    parts = []
    for line in (beat.before, beat.intro):
        if line:
            parts += [word_audio(line, voice=voice), pause]
    parts.append(np.asarray(beaten, dtype=np.float32))
    if beat.after:
        parts += [pause, word_audio(beat.after, voice=voice)]
    return np.concatenate(parts)


def wav_bytes(samples) -> bytes:
    """Float samples as the WAV synthesize() returns."""
    import soundfile as sf

    buffer = io.BytesIO()
    sf.write(buffer, samples, samplerate=SAMPLE_RATE, format="WAV")
    return buffer.getvalue()


# A sung reply is rendered whole and then sent in pieces of this many samples.
SONG_PIECE_SAMPLES = SAMPLE_RATE // 2


def pcm_pieces(samples) -> list[bytes]:
    """Float samples as big-endian L16, in pieces of half a second."""
    return [to_l16(samples[i:i + SONG_PIECE_SAMPLES])
            for i in range(0, len(samples), SONG_PIECE_SAMPLES)]


def stream_pcm(text: str, *, voice: str = DEFAULT_VOICE, whole: bool = False) -> Iterator[bytes]:
    """The spoken reply as big-endian L16 at SAMPLE_RATE, one piece per sentence (see
    pieces()), each rendered only when the previous one has been taken (S4).

    `whole`: exactly the call synthesize() makes, Kokoro's own split, so the audio is
    that of the WAV reply. For a read-back (S5), which must not be split by us.
    """
    pipeline = _pipeline(lang_code(voice))
    # A list is rendered element by element; a string is split by Kokoro (KOKORO_SPLIT).
    results = pipeline(text, voice=voice) if whole else pipeline(pieces(text), voice=voice)
    for _graphemes, _phonemes, audio in results:
        if audio is None:
            continue
        piece = to_l16(audio)
        if piece:
            yield piece


def sentences(text: str) -> list[str]:
    """The text cut at SENTENCE_SPLIT, empty pieces skipped."""
    return [piece.strip() for piece in re.split(SENTENCE_SPLIT, text.strip()) if piece.strip()]


def pieces(text: str) -> list[str]:
    """What a streamed reply is rendered in: its sentences, a sentence shorter than
    MIN_PIECE_CHARS joined to the next with one space. The words and their order are
    the text's; only the white space between sentences can differ."""
    out: list[str] = []
    pending = ""
    for sentence in sentences(text):
        pending = f"{pending} {sentence}" if pending else sentence
        if len(pending) >= MIN_PIECE_CHARS:
            out.append(pending)
            pending = ""
    if pending:
        out.append(pending)
    return out


def warm_up(voice: str | None = None) -> None:
    """Load the pipeline and render a word, so the first voice turn does not pay for it.

    Without an argument it warms the voice the bridge will speak with
    (JARVIS_KOKORO_VOICE, as bridge.config reads it), so the right language is loaded.
    """
    synthesize("Ready.", voice=voice or os.environ.get("JARVIS_KOKORO_VOICE") or DEFAULT_VOICE)
