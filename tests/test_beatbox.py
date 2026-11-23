"""Beatboxing, the second easter egg (jarvis-beatbox): the pattern table, the tag and when
to beatbox, the kit and the renderer with a fake voice (no Kokoro), and the server: a beat
only when asked, never a read-back, spoken when switched off or when it fails."""
import dataclasses
import random
import re
import urllib.parse
from pathlib import Path

import numpy as np
import pytest

from bridge import beatbox, claude_adapter, singing, summary, tts
from bridge.beatbox import KIT, PATTERNS, STEPS_PER_BAR
from bridge.full_mode import FRAMING_SENTENCES, PROJECT_READY
from bridge.remote_adapter import NOTICE_SENTENCES
from tests.test_server_full_mode import Bridge
from tests.test_singing import Singer, fake_voice
from tests.test_streaming import STREAM_ACCEPT, _say

SR = beatbox.SAMPLE_RATE


def centroid(x) -> float:
    spectrum = np.abs(np.fft.rfft(x))
    f = np.fft.rfftfreq(len(x), 1.0 / SR)
    return float((spectrum * f).sum() / spectrum.sum())


# --- the table -----------------------------------------------------------------------------

def test_every_pattern_is_a_grid_of_known_sounds():
    for pattern in PATTERNS.values():
        assert pattern.bars
        for bar in pattern.bars:
            tokens = bar.split()
            assert len(tokens) == STEPS_PER_BAR, (pattern.id, bar)
            assert set(tokens) <= set(KIT) | {"."}, (pattern.id, bar)
        # Every beat starts on a kick.
        assert pattern.bars[0].split()[0] == "B"
        assert pattern.spoken


def test_every_pattern_lasts_five_to_fifteen_seconds():
    for pattern in PATTERNS.values():
        assert beatbox.MIN_SECONDS <= pattern.seconds <= beatbox.MAX_SECONDS, pattern.id


def test_the_three_beats_asked_for():
    assert set(PATTERNS) == {"boots-and-cats", "hip-hop", "jarvis-hook"}
    # Boots and cats: kick, hat, snare, hat, in eighths.
    assert PATTERNS["boots-and-cats"].bars[0].split()[:8] == list("B.t.K.t.")
    # The hip-hop beat swings and uses the "Pf" snare.
    assert PATTERNS["hip-hop"].swing > 0 and "P" in PATTERNS["hip-hop"].bars[0]
    # Only the hook pattern hums and sings, and its hook stays inside the beat.
    hook = PATTERNS["jarvis-hook"]
    assert hook.hum and hook.hook
    assert not any(p.hum or p.hook for p in PATTERNS.values() if p is not hook)
    for h in hook.hook:
        assert len(h.word.split("-")) == len(h.notes)
        assert h.step + sum(steps for _semi, steps in h.notes) <= STEPS_PER_BAR
        assert h.bar < len(hook.bars)


def test_the_agent_is_told_exactly_the_beats_the_bridge_has():
    assert set(summary.BEATBOX_PATTERNS) == set(PATTERNS)
    prompt = (Path(__file__).parent.parent / "agent_host" / "prompt.md").read_text()
    for name in PATTERNS:
        assert f"[beatbox: {name}]" in prompt
        assert f"[beatbox: {name}]" in summary.SYSTEM_PROMPT_ADDITION
    assert set(re.findall(r"\[beatbox: ([a-z-]+)\]", prompt)) == set(PATTERNS)
    assert "[beatbox]" in prompt and "[beatbox]" in summary.SYSTEM_PROMPT_ADDITION


def test_hits_follow_the_grid_and_the_swing():
    pattern = PATTERNS["hip-hop"]
    hits = beatbox.hits(pattern)
    step = pattern.step_seconds
    assert hits[0] == beatbox.Hit(0.0, "B")
    assert [h.start_s for h in hits] == sorted(h.start_s for h in hits)
    # The hat on the third sixteenth is on the grid; the kick on the eighth is late.
    assert beatbox.Hit(2 * step, "t") in hits
    assert beatbox.Hit((7 + pattern.swing) * step, "B") in hits
    assert len(hits) == sum(1 for bar in pattern.bars for t in bar.split() if t != ".")


# --- the tag and when to beatbox -----------------------------------------------------------

def test_a_tagged_reply_the_driver_asked_for_is_beatboxed():
    text, beat = beatbox.choose("[beatbox: hip-hop] Here's a beat for the road.",
                                transcript="Jarvis, can you beatbox?", enabled=True,
                                read_back=False)
    assert beat is not None and beat.pattern.id == "hip-hop"
    assert beat.intro == "Here's a beat for the road."
    assert text == "Here's a beat for the road."


@pytest.mark.parametrize("tag,expected", [
    ("[beatbox: boots-and-cats]", "boots-and-cats"),
    ("[beatbox: Boots and Cats]", "boots-and-cats"),
    ("[Beatbox: hip hop]", "hip-hop"),
    ("[beatbox: boom bap]", "hip-hop"),
    ("[beat-box: jarvis_hook]", "jarvis-hook"),
    ("[beatboxing: hook]", "jarvis-hook"),
    ("[beat box: hip-hop]", "hip-hop"),
])
def test_a_named_pattern_answers_to_its_aliases(tag, expected):
    _, beat = beatbox.choose(f"{tag} Here we go.", transcript="beatbox", enabled=True,
                             read_back=False)
    assert beat is not None and beat.pattern.id == expected


def test_no_name_or_an_unknown_one_picks_a_beat_at_random():
    seen = set()
    for seed in range(40):
        for tag in ("[beatbox]", "[beatbox:]", "[beatbox: dubstep]"):
            _, beat = beatbox.choose(f"{tag} Here we go.", transcript="drop a beat",
                                     enabled=True, read_back=False, rng=random.Random(seed))
            assert beat is not None
            seen.add(beat.pattern.id)
    assert seen == set(PATTERNS)


def test_the_choice_is_the_rngs():
    picks = [beatbox.choose("[beatbox] Go.", transcript="beatbox", enabled=True,
                            read_back=False, rng=random.Random(7))[1].pattern.id
             for _ in range(3)]
    assert len(set(picks)) == 1


@pytest.mark.parametrize("transcript", [
    "beatbox", "Jarvis, can you beatbox?", "doe eens een beat", "drop a beat",
    "give me some beats", "kun je beatboxen", "do boots and cats", "Beat-box for me",
    "doe eens een beatje", "can you beat box"])
def test_the_driver_asks_in_english_or_dutch(transcript):
    assert beatbox.asked_for_a_beat(transcript)


@pytest.mark.parametrize("transcript", [
    "what is open?", "beta release", "the beetle", "sing a song", "a beaten path", ""])
def test_other_words_do_not_ask(transcript):
    assert not beatbox.asked_for_a_beat(transcript)


def test_without_the_drivers_request_the_line_is_spoken():
    text, beat = beatbox.choose("[beatbox] Here we go.", transcript="what is open?",
                                enabled=True, read_back=False)
    assert beat is None and text == "Here we go."


def test_switched_off_the_line_is_spoken():
    text, beat = beatbox.choose("[beatbox: hip-hop] Here we go.", transcript="beatbox",
                                enabled=False, read_back=False)
    assert beat is None and text == "Here we go."


def test_a_read_back_is_never_beatboxed_or_changed():
    readback = "[beatbox] Merge pull request 21 on jarvis. Say yes to go ahead."
    assert beatbox.choose(readback, transcript="beatbox", enabled=True,
                          read_back=True) == (readback, None)


def test_a_reply_without_a_tag_is_never_touched():
    for spoken in ("The beat goes on: the build is green.",
                   "[sing: twinkle] Lit-tle build"):
        assert beatbox.choose(spoken, transcript="beatbox", enabled=True,
                              read_back=False) == (spoken, None)


@pytest.mark.parametrize("text,beaten", [
    ("[beatbox] Here we go.", True),
    ("[beatbox: hip-hop] Here we go.", True),
    ("[beatbox: hip-hop Here we go.", False),
    ("Sure. [beatbox] Here we go.", False),
    ("[beatbox: drum & bass!] Here we go.", False),
    ("[beatbox: a very long name that goes on and on and on and on and on] Go.", False),
])
def test_a_malformed_or_misplaced_tag_is_never_spoken_aloud(text, beaten):
    said, beat = beatbox.choose(text, transcript="beatbox", enabled=True, read_back=False)
    assert "[" not in said and "beatbox" not in said.lower()
    assert (beat is not None) == beaten


def test_a_sing_tag_after_the_beatbox_tag_is_never_said():
    # Review of #86: "[beatbox] [sing: twinkle] Here we go." showed and said the sing tag.
    for spoken in ("[beatbox] [sing: twinkle] Here we go.",
                   "[beatbox: hip-hop] [sing: Iron Man!] Here we go.",
                   "[beat box] [sing: twinkle Here we go."):
        text, beat = beatbox.choose(spoken, transcript="drop a beat", enabled=True,
                                    read_back=False)
        assert beat is not None and beat.intro == "Here we go." and text == "Here we go."


def test_an_unclosed_tag_never_swallows_the_line():
    pattern, rest, tagged = beatbox.split_tag("[beatbox: hip-hop Here we go, mind the bump")
    assert tagged and pattern is None
    assert rest == "Here we go, mind the bump"


def test_without_a_line_the_phone_shows_the_patterns_own():
    text, beat = beatbox.choose("[beatbox: boots-and-cats]", transcript="beatbox",
                                enabled=True, read_back=False)
    assert beat is not None and beat.intro == ""
    assert text == "Boots and cats and boots and cats."


def test_after_a_notice_the_beat_still_comes_and_news_after_it():
    sentence = NOTICE_SENTENCES["new_session"]
    spoken = f"{sentence} [beatbox: hip-hop] Here we go. {PROJECT_READY}"
    text, beat = beatbox.choose(spoken, transcript="drop a beat", enabled=True,
                                read_back=False, framing=FRAMING_SENTENCES)
    assert (beat.before, beat.intro, beat.after) == (sentence, "Here we go.", PROJECT_READY)
    assert text == f"{sentence} Here we go. {PROJECT_READY}"
    # Anything else before the tag: not first, not beatboxed.
    text, beat = beatbox.choose("Sure thing. [beatbox] Go.", transcript="beatbox",
                                enabled=True, read_back=False, framing=FRAMING_SENTENCES)
    assert beat is None and text == "Sure thing. Go."


def test_the_tag_passes_the_spoken_filter():
    line = f"{summary.SUMMARY_MARKER} [beatbox: hip-hop] Here's a beat for the road."
    assert summary.extract_summary(line) == "[beatbox: hip-hop] Here's a beat for the road."


# --- the kit and the beat, with a fake voice ----------------------------------------------

def test_the_kit_sounds_like_a_kit():
    kit = beatbox.build_kit(fake_voice)
    assert set(kit) == set(KIT)
    # The kick is low, the snares in the middle, the hats high.
    assert centroid(kit["B"]) < 600
    assert 1500 < centroid(kit["K"]) < 6000 and 1500 < centroid(kit["P"]) < 6000
    assert centroid(kit["t"]) > 5000 and centroid(kit["T"]) > 5000
    # Short sounds, peaking at their own loudness.
    for symbol, sound in kit.items():
        assert len(sound) <= 0.3 * SR
        assert float(np.abs(sound).max()) == pytest.approx(KIT[symbol], rel=0.02)
    assert len(kit["t"]) < len(kit["T"])


def test_jarviss_voice_is_in_the_kit():
    silent = beatbox.build_kit(lambda word: np.zeros(0, dtype=np.float32))
    voiced = beatbox.build_kit(fake_voice)
    for symbol in KIT:
        assert not np.allclose(silent[symbol], voiced[symbol]), symbol


def test_the_kit_asks_for_each_word_once():
    asked = []
    beatbox.render(PATTERNS["boots-and-cats"], lambda w: asked.append(w) or fake_voice(w))
    assert sorted(asked) == sorted(set(asked)) and set(asked) <= {"boots", "cats", "puh", "tss"}


def test_a_beat_is_deterministic_bounded_and_not_clipped():
    for pattern in PATTERNS.values():
        a = beatbox.render(pattern, fake_voice)
        b = beatbox.render(pattern, fake_voice)
        assert np.array_equal(a.samples, b.samples)
        assert a.samples.dtype == np.float32
        assert 0.05 < np.abs(a.samples).max() <= beatbox.MAX_PEAK + 1e-6
        assert beatbox.MIN_SECONDS <= a.seconds <= beatbox.MAX_SECONDS


def test_every_hit_is_heard_where_the_grid_puts_it():
    pattern = PATTERNS["boots-and-cats"]
    beat = beatbox.render(pattern, fake_voice)
    x = beat.samples
    for hit in beat.hits:
        a = int(hit.start_s * SR)
        before = x[max(0, a - int(0.012 * SR)):a]
        after = x[a:a + int(0.03 * SR)]
        loud = np.sqrt(np.mean(after ** 2))
        assert loud > 0.03, hit
        if hit.sound in "BK" and len(before):
            assert loud > 1.5 * np.sqrt(np.mean(before ** 2)), hit


def test_a_voice_that_says_nothing_still_gives_a_beat():
    beat = beatbox.render(PATTERNS["jarvis-hook"], lambda w: np.zeros(0, dtype=np.float32))
    assert 0.05 < np.abs(beat.samples).max() <= beatbox.MAX_PEAK + 1e-6
    # At Kokoro's speaking level, BEAT_LIFT_DB above it.
    lift = 20 * np.log10(rms(beat.samples) / beatbox.DEFAULT_SPEECH_RMS)
    assert lift == pytest.approx(beatbox.BEAT_LIFT_DB, abs=0.1)


def rms(x) -> float:
    return float(np.sqrt(np.mean(np.square(x))))


def kokoro_like(word: str) -> np.ndarray:
    """The fake voice at about Kokoro's level (-25 dBFS RMS, peaks near 0.3), with the
    silence Kokoro leaves around a word."""
    voice = fake_voice(word) * 0.22
    pad = np.zeros(int(0.2 * SR), dtype=np.float32)
    return np.concatenate([pad, voice, pad])


@pytest.mark.parametrize("pattern", sorted(PATTERNS))
def test_the_beat_is_no_louder_than_the_speech_around_it(monkeypatch, pattern):
    # Review of #86: the beat came out 9 to 10 dB above the intro; now at most 3 dB.
    monkeypatch.setattr(tts, "word_audio", lambda text, *, voice: kokoro_like(text))
    beat = beatbox.BeatRequest(PATTERNS[pattern], "Alright, here's a beat for the road.")
    samples = tts.beatbox(beat, voice="bm_george")
    intro = kokoro_like(beat.intro)
    pause = int(tts.SONG_PAUSE_S * SR)
    beaten = samples[len(intro) + pause:]
    lift = 20 * np.log10(rms(beaten) / rms(intro))
    assert 0 < lift <= 3.0, lift
    assert np.abs(beaten).max() <= beatbox.MAX_PEAK + 1e-6


def test_the_hum_and_the_hook_are_sung_at_their_pitch():
    pattern = PATTERNS["jarvis-hook"]
    beat = beatbox.render(pattern, fake_voice, drums=False)
    hook = [n for n in beat.notes if n.part == "hook"]
    hum = [n for n in beat.notes if n.part == "hum"]
    assert len(hook) == sum(len(h.notes) for h in pattern.hook)
    assert len(hum) == len(pattern.hum)
    # The fake voice speaks at 120 Hz: the hook sits on it, the hum an octave below.
    voice = singing.hz_to_midi(120.0)
    assert min(singing.hz_to_midi(n.hz) for n in hum) == pytest.approx(voice - 12, abs=0.5)
    sung_alone = [n for n in beat.notes if not any(
        o is not n and o.start_s < n.start_s + n.seconds and n.start_s < o.start_s + o.seconds
        for o in beat.notes)]
    assert {n.part for n in sung_alone} == {"hum", "hook"}
    for note in sung_alone:
        a = int((note.start_s + 0.04) * SR)
        b = int((note.start_s + note.seconds - 0.04) * SR)
        f0 = singing.f0_track(beat.samples[a:b])
        voiced = f0[f0 > 0]
        assert voiced.size, note
        # A falling last syllable sits half its fall below the note, on the median.
        off = 12 * np.log2(float(np.median(voiced)) / note.hz) + note.fall / 2
        assert abs(off) < 0.5, (note, off)
    assert any(n.fall for n in sung_alone)


def test_drums_alone_have_no_voice_notes_mixed_in():
    pattern = PATTERNS["jarvis-hook"]
    dry = beatbox.render(pattern, fake_voice, voices=False)
    wet = beatbox.render(pattern, fake_voice)
    assert len(dry.samples) == len(wet.samples) and not np.array_equal(dry.samples, wet.samples)
    assert dry.notes == []


def test_the_line_is_said_before_the_beat_and_news_after_it(monkeypatch):
    said = []

    def word_audio(text, *, voice):
        said.append(text)
        return np.full(100, 0.1, dtype=np.float32)
    monkeypatch.setattr(tts, "word_audio", word_audio)
    beat = beatbox.BeatRequest(PATTERNS["boots-and-cats"], "Here we go.", before="Notice.",
                               after="News.")
    samples = tts.beatbox(beat, voice="bm_george")
    # The lines first (the beat is levelled to them), then the beat's words.
    assert said[:3] == ["Notice.", "Here we go.", "News."]
    pause = int(tts.SONG_PAUSE_S * SR)
    assert np.all(samples[:100] == np.float32(0.1))
    assert np.all(samples[100:100 + pause] == 0)
    assert np.all(samples[-100:] == np.float32(0.1))
    rendered = beatbox.render(PATTERNS["boots-and-cats"], lambda w: word_audio(w, voice=""))
    assert len(samples) == 300 + 3 * pause + len(rendered.samples)


# --- the server ----------------------------------------------------------------------------

class Beatboxer:
    """A fake tts.beatbox: remembers the beat, returns a short float signal."""

    def __init__(self, fail=False):
        self.beats = []
        self.fail = fail

    def __call__(self, beat, *, voice):
        self.beats.append(beat)
        if self.fail:
            raise RuntimeError("no beat today")
        return np.full(SR // 5, 0.25, dtype=np.float32)


@pytest.fixture
def full(tmp_path, monkeypatch):
    b = Bridge(tmp_path, monkeypatch)
    b.beatboxer = Beatboxer()
    b.singer = Singer()
    monkeypatch.setattr(tts, "beatbox", b.beatboxer)
    monkeypatch.setattr(tts, "sing", b.singer)
    monkeypatch.setattr(tts, "wav_bytes", lambda samples: b"BEAT:%d" % len(samples))
    yield b
    b.stop()


TAGGED = "SPOKEN SUMMARY: [beatbox: hip-hop] Here's a beat for the road."


def test_a_full_reply_the_driver_asked_to_be_beatboxed_is(full):
    full.host.scenario(mode="wrapper", spoken=TAGGED)
    response, said = full.say("Jarvis, drop a beat")
    assert response.status == 200
    assert said == "BEAT:%d" % (SR // 5)
    assert [(b.pattern.id, b.intro) for b in full.beatboxer.beats] == [
        ("hip-hop", "Here's a beat for the road.")]
    assert full.singer.songs == []
    assert urllib.parse.unquote(response.getheader("X-Jarvis-Summary")) == (
        "Here's a beat for the road.")


def test_a_beat_is_streamed_whole_in_pcm(full):
    full.host.scenario(mode="wrapper", spoken=TAGGED)
    response, data = _say(full, "beatbox please")
    assert response.getheader("Content-Type") == STREAM_ACCEPT
    assert data == tts.to_l16(np.full(SR // 5, 0.25, dtype=np.float32))


def test_a_tagged_reply_nobody_asked_for_is_spoken(full):
    full.host.scenario(mode="wrapper", spoken=TAGGED)
    response, said = full.say("what is open?")
    assert said == "WAV:Here's a beat for the road."
    assert full.beatboxer.beats == []


def test_a_read_back_is_never_beatboxed(full):
    full.host.scenario(mode="wrapper", gate=True, spoken=TAGGED)
    response, said = full.say("drop a beat, then merge the pull request")
    assert response.getheader("X-Jarvis-Needs-Confirmation") == "1"
    assert said.startswith("WAV:") and "[beatbox" not in said
    assert full.beatboxer.beats == []


def test_switched_off_nothing_is_beatboxed(full):
    full.stop()
    full.config = dataclasses.replace(full.config, beatbox=False)
    full.start()
    full.host.scenario(mode="wrapper", spoken=TAGGED)
    response, said = full.say("beatbox")
    assert said == "WAV:Here's a beat for the road."
    assert full.beatboxer.beats == []


def test_a_beat_that_fails_to_render_is_spoken(full):
    full.beatboxer.fail = True
    full.host.scenario(mode="wrapper", spoken=TAGGED)
    response, said = full.say("beatbox")
    assert response.status == 200
    assert said == "WAV:Here's a beat for the road."


def test_singing_still_sings(full):
    full.host.scenario(mode="wrapper", spoken="SPOKEN SUMMARY: [sing: twinkle] Lit-tle build")
    response, said = full.say("sing a song")
    assert said == "BEAT:%d" % (SR // 4)        # the fake singer's signal
    assert full.singer.songs and full.beatboxer.beats == []


@pytest.mark.parametrize("code", sorted(NOTICE_SENTENCES))
def test_the_server_beatboxes_after_each_notice(full, code):
    full.host.scenario(mode="wrapper", notice=code, spoken=TAGGED)
    response, said = full.say("doe eens een beat")
    assert said == "BEAT:%d" % (SR // 5)
    assert full.beatboxer.beats[-1].before == NOTICE_SENTENCES[code]


def test_a_readonly_reply_is_beatboxed_too(tmp_path, monkeypatch):
    b = Bridge(tmp_path, monkeypatch, mode="readonly")
    try:
        beatboxer = Beatboxer()
        monkeypatch.setattr(tts, "beatbox", beatboxer)
        monkeypatch.setattr(tts, "wav_bytes", lambda samples: b"BEAT")
        monkeypatch.setattr(claude_adapter, "run_turn", lambda **kwargs: (
            "Here you go.\n" + TAGGED, "session-ro"))
        response, said = b.say("can you beatbox")
        assert said == "BEAT"
        assert beatboxer.beats[0].pattern.id == "hip-hop"
    finally:
        b.stop()


def test_the_switch_reads_0_1_or_unset(monkeypatch):
    from bridge import config
    from bridge.config import ConfigError
    monkeypatch.setenv("JARVIS_BIND_HOST", "100.64.0.1")
    monkeypatch.setenv("JARVIS_TOKEN", "secret")
    monkeypatch.setenv("JARVIS_PROJECTS", "demo=/tmp")
    monkeypatch.delenv("JARVIS_BEATBOX", raising=False)
    assert config.load_config().beatbox is True
    monkeypatch.setenv("JARVIS_BEATBOX", "0")
    loaded = config.load_config()
    assert loaded.beatbox is False and loaded.sing is True
    monkeypatch.setenv("JARVIS_BEATBOX", "1")
    assert config.load_config().beatbox is True
    monkeypatch.setenv("JARVIS_BEATBOX", "loud")
    with pytest.raises(ConfigError):
        config.load_config()


# --- Mark, 23 Nov 2026: "Jarvis says it will beatbox, says the line, and then nothing" ---
# Turn c55ad21e: Whisper heard "Jarvis, drop a beatbox" as "Jawers, troop en peedbox!".
# The agent understood and tagged its line; the bridge did not find a word asking for a
# beat in the transcript, dropped the tag and said only the line. The exact reply:

MARKS_REPLY = ("Here's a beat for you.\n\n"
               "SPOKEN SUMMARY: [beatbox] Here's a beat for the road, Mark.")
MARKS_WORDS = "Jawers, troop en peedbox!"
MARKS_LINE = "Here's a beat for the road, Mark."


@pytest.mark.parametrize("transcript", [
    MARKS_WORDS, "peedbox", "peetbox", "beadbox", "beat-box", "beet box", "bietbox",
    "biedbox", "Pete box", "peat box please", "kun je peedboxen", "beatbax",
    "beatboks", "bietboks", "peat bocks", "bait box", "baitbox", "bijtbox"])
def test_the_driver_asks_even_when_whisper_mishears_beatbox(transcript):
    assert beatbox.asked_for_a_beat(transcript)


@pytest.mark.parametrize("transcript", [
    "a pizza box", "check the postbox", "my inbox", "the toolbox", "an Xbox",
    "a bento box", "the beta box", "put it in the box", "a beaten box",
    "Dropbox", "een boks", "het bad box", "de lunchbox", "speed box"])
def test_other_boxes_do_not_ask(transcript):
    assert not beatbox.asked_for_a_beat(transcript)


def test_marks_reply_is_beatboxed_after_the_new_session_notice():
    spoken = summary.extract_summary(MARKS_REPLY)
    text, beat = beatbox.choose(f"{NOTICE_SENTENCES['new_session']} {spoken}",
                                transcript=MARKS_WORDS, enabled=True, read_back=False,
                                framing=FRAMING_SENTENCES, rng=random.Random(1))
    assert beat is not None and beat.pattern.id in PATTERNS
    assert beat.intro == MARKS_LINE
    assert beat.before == NOTICE_SENTENCES["new_session"]
    assert text == f"{NOTICE_SENTENCES['new_session']} {MARKS_LINE}"


def test_marks_turn_is_beatboxed_as_a_wav(full):
    full.host.scenario(mode="wrapper", notice="new_session", spoken=MARKS_REPLY)
    response, said = full.say(MARKS_WORDS)
    assert response.status == 200
    assert said == "BEAT:%d" % (SR // 5)
    assert [(b.intro, b.before) for b in full.beatboxer.beats] == [
        (MARKS_LINE, NOTICE_SENTENCES["new_session"])]


def test_marks_turn_is_beatboxed_in_the_stream(full):
    full.host.scenario(mode="wrapper", notice="new_session", spoken=MARKS_REPLY)
    response, data = _say(full, MARKS_WORDS)
    assert response.getheader("Content-Type") == STREAM_ACCEPT
    assert data == tts.to_l16(np.full(SR // 5, 0.25, dtype=np.float32))
    assert [b.intro for b in full.beatboxer.beats] == [MARKS_LINE]


def test_marks_reply_is_beatboxed_in_readonly_mode_too(tmp_path, monkeypatch):
    b = Bridge(tmp_path, monkeypatch, mode="readonly")
    try:
        beatboxer = Beatboxer()
        monkeypatch.setattr(tts, "beatbox", beatboxer)
        monkeypatch.setattr(tts, "wav_bytes", lambda samples: b"BEAT")
        monkeypatch.setattr(claude_adapter, "run_turn",
                            lambda **kwargs: (MARKS_REPLY, "session-ro"))
        response, said = b.say(MARKS_WORDS)
        assert said == "BEAT"
        assert [beat.intro for beat in beatboxer.beats] == [MARKS_LINE]
    finally:
        b.stop()


def test_a_tag_dropped_because_nobody_asked_is_logged(full, caplog):
    full.host.scenario(mode="wrapper", spoken=TAGGED)
    with caplog.at_level("INFO", logger="bridge.server"):
        full.say("what is open?")
    dropped = [r.getMessage() for r in caplog.records if "beatbox tag dropped" in r.getMessage()]
    assert dropped and "tag_first=True" in dropped[0]
    assert "what is open" not in dropped[0]  # never the transcript
