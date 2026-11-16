"""The voice defaults and the language a voice is spoken in (no model is loaded here)."""
import re
from pathlib import Path

from bridge import tts


def test_the_default_voice_is_british_male(monkeypatch):
    from bridge import config

    assert tts.DEFAULT_VOICE == "bm_george"
    # bridge/config.py spells the default out; the two must agree.
    monkeypatch.setenv("JARVIS_BIND_HOST", "100.64.0.1")
    monkeypatch.setenv("JARVIS_TOKEN", "secret")
    monkeypatch.setenv("JARVIS_PROJECTS", "demo=/tmp")
    monkeypatch.delenv("JARVIS_KOKORO_VOICE", raising=False)
    assert config.load_config().kokoro_voice == tts.DEFAULT_VOICE
    assert tts.lang_code(tts.DEFAULT_VOICE) == "b"


def test_a_british_voice_gets_british_english_and_the_rest_american():
    assert tts.lang_code("bf_emma") == "b"
    assert tts.lang_code("af_heart") == "a"
    assert tts.lang_code("am_michael") == "a"
    assert tts.lang_code("") == "a"


def test_warm_up_uses_the_configured_voice(monkeypatch):
    seen = []
    monkeypatch.setattr(tts, "synthesize", lambda text, voice: seen.append(voice) or b"")
    monkeypatch.delenv("JARVIS_KOKORO_VOICE", raising=False)
    tts.warm_up()
    monkeypatch.setenv("JARVIS_KOKORO_VOICE", "af_heart")
    tts.warm_up()
    tts.warm_up("bf_emma")
    assert seen == ["bm_george", "af_heart", "bf_emma"]


def test_the_image_bakes_the_default_voice_and_the_previous_one():
    dockerfile = (Path(__file__).parent.parent / "deploy" / "Dockerfile").read_text()
    baked = re.search(r'^ARG KOKORO_VOICES="?([^"\n]+)"?$', dockerfile, re.M).group(1).split()
    assert tts.DEFAULT_VOICE in baked
    assert "af_heart" in baked
