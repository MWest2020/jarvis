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


# --- the languages the driver speaks (JARVIS_WHISPER_LANGUAGES), with a fake model -------

class FakeWhisper:
    """Detects German first, as base did for Mark's English on 26 Nov 2026."""

    def __init__(self):
        self.calls = []

    def detect_language(self, audio):
        self.calls.append(("detect", type(audio).__name__))
        return "de", 0.41, [("de", 0.41), ("en", 0.33), ("nl", 0.12), ("fr", 0.02)]

    def transcribe(self, audio, *, beam_size, language):
        self.calls.append(("transcribe", language))
        return [type("Segment", (), {"text": f" said in {language} "})()], None


def test_detection_picks_the_likeliest_of_the_drivers_languages(monkeypatch):
    model = FakeWhisper()
    monkeypatch.setattr(stt, "_model", lambda size: model)
    samples = stt.decode(_wav([0] * 16000))
    assert stt.transcribe(samples, languages=("nl", "en")) == "said in en"
    assert model.calls == [("detect", "ndarray"), ("transcribe", "en")]


def test_the_bytes_are_decoded_once_for_the_detection(monkeypatch):
    model = FakeWhisper()
    monkeypatch.setattr(stt, "_model", lambda size: model)
    assert stt.transcribe(_wav([0] * 16000), languages=("nl", "en")) == "said in en"
    assert model.calls[0] == ("detect", "ndarray")


def test_without_languages_whisper_detects_as_before(monkeypatch):
    model = FakeWhisper()
    monkeypatch.setattr(stt, "_model", lambda size: model)
    assert stt.transcribe(_wav([0] * 16000)) == "said in None"
    assert stt.transcribe(_wav([0] * 16000), language="nl", languages=("nl", "en")) == (
        "said in nl")
    assert [call for call in model.calls if call[0] == "detect"] == []


def test_pick_language():
    probs = [("de", 0.5), ("nl", 0.2), ("en", 0.2)]
    assert stt.pick_language(probs, ("nl", "en")) == "nl"       # a tie: the first named
    assert stt.pick_language([("de", 0.9)], ("en", "nl")) == "en"
    assert stt.pick_language([("en", 0.1), ("nl", 0.3)], ("en", "nl")) == "nl"
