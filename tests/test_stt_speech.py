"""Detected speech in a recording (jarvis-full-agent D36, step 5): the detector
that ships inside faster-whisper, run for real on generated audio. No model is
downloaded and Whisper itself is not loaded."""
import io
import math
import struct
import wave

from bridge import stt


def _wav(samples: list[int]) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buffer.getvalue()


def test_silence_holds_no_speech():
    assert stt.speech_seconds(_wav([0] * 32000)) == 0.0


def test_a_click_is_not_three_tenths_of_a_second_of_speech():
    click = [0] * 16000 + [20000, -20000] * 80 + [0] * 16000
    assert stt.speech_seconds(_wav(click)) < 0.3


def test_a_steady_tone_is_not_speech():
    tone = [int(8000 * math.sin(2 * math.pi * 440 * n / 16000)) for n in range(32000)]
    assert stt.speech_seconds(_wav(tone)) < 0.3


def test_the_detector_takes_the_decoded_samples_as_well_as_the_bytes():
    click = _wav([0] * 16000 + [20000, -20000] * 80 + [0] * 16000)
    samples = stt.decode(click)
    assert len(samples) == 32160
    assert stt.speech_seconds(samples) == stt.speech_seconds(click)
