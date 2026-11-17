"""Singing, the easter egg (jarvis-sing): the note math, the tune table, the tag and who
may sing, the renderer with a fake voice (a sine, no Kokoro), and the server: a sung
reply only when asked, never a read-back, spoken when switched off or when it fails."""
import dataclasses
import math
import urllib.parse
import re
from pathlib import Path

import numpy as np
import pytest

from bridge import claude_adapter, singing, summary, tts
from bridge.full_mode import FALLBACK_SENTENCES, FRAMING_SENTENCES, PROJECT_READY
from bridge.remote_adapter import NOTICE_SENTENCES
from bridge.singing import TUNES, Note, Tune
from tests.test_server_full_mode import Bridge
from tests.test_streaming import STREAM_ACCEPT, _say

SR = singing.SAMPLE_RATE
BIRTHDAY = ("Hap-py build-day to you, hap-py build-day to you, hap-py build-day dear "
            "Jar-vis, hap-py build-day to you")


def fake_voice(word: str, hz: float = 120.0) -> np.ndarray:
    """A deterministic "voice": a harmonic tone with a slight wobble, 0.09 s per
    letter, a soft start and end. Stands for Kokoro in every test here."""
    n = int(0.09 * SR * len(word))
    t = np.arange(n) / SR
    phase = 2 * np.pi * np.cumsum(hz * (1 + 0.04 * np.sin(2 * np.pi * 2 * t))) / SR
    x = np.sin(phase) + 0.5 * np.sin(2 * phase) + 0.3 * np.sin(3 * phase)
    envelope = np.minimum(1.0, np.minimum(t / 0.02, (t[-1] - t) / 0.02 + 1e-9))
    return (0.3 * x * envelope).astype(np.float32)


def sung_semitones_off(song) -> list[float]:
    """Per scheduled note, how far the sung pitch is from the note, in semitones."""
    out = []
    for note in song.schedule:
        a = int((note.start_s + 0.05) * SR)
        b = int((note.start_s + note.seconds - 0.05) * SR)
        f0 = singing.f0_track(song.samples[a:b])
        voiced = f0[f0 > 0]
        out.append(12 * math.log2(float(np.median(voiced)) / note.hz) if voiced.size else 99.0)
    return out


# --- note -> pitch and duration ------------------------------------------------------------

def test_midi_notes_and_frequencies():
    assert singing.midi_to_hz(69) == pytest.approx(440.0)
    assert singing.midi_to_hz(60) == pytest.approx(261.63, abs=0.01)
    assert singing.midi_to_hz(57) == pytest.approx(220.0)
    for midi in (40, 52.5, 69, 81):
        assert singing.hz_to_midi(singing.midi_to_hz(midi)) == pytest.approx(midi)


def test_beats_become_seconds_at_the_tempo():
    assert singing.note_seconds(1, 120) == pytest.approx(0.5)
    assert singing.note_seconds(0.75, 110) == pytest.approx(0.75 * 60 / 110)


def test_a_tune_that_would_be_too_long_is_sung_faster():
    tune = TUNES["frere-jacques"]          # 32 beats
    assert singing.tempo(tune) == tune.bpm  # 16 s at 120: fits in 20
    assert singing.tempo(tune, max_seconds=8) == pytest.approx(240)


def test_every_tune_fits_in_twenty_seconds_and_starts_with_a_chord():
    for tune in TUNES.values():
        assert tune.beats * 60 / singing.tempo(tune) <= singing.MAX_SECONDS
        assert tune.notes[0].chord is not None
        assert all(n.beats > 0 for n in tune.notes)


def test_the_key_follows_the_voice():
    tune = TUNES["happy-birthday"]          # steps -5 to 7: the middle is 1
    for hz in (100.0, 146.0, 220.0):
        tonic = singing.tonic_midi(tune, hz)
        assert tonic + 1 == pytest.approx(singing.hz_to_midi(hz) + singing.KEY_LIFT, abs=0.5)


def test_the_agent_is_told_exactly_the_tunes_the_bridge_has():
    table = {t.id: t.syllables for t in TUNES.values()}
    assert dict(summary.SING_TUNES) == table
    prompt = (Path(__file__).parent.parent / "agent_host" / "prompt.md").read_text()
    for name, lines in table.items():
        listed = f"[sing: {name}] {sum(lines)} syllables ({', '.join(map(str, lines))})"
        assert listed in prompt
        assert listed in summary.SYSTEM_PROMPT_ADDITION
    assert set(re.findall(r"\[sing: ([a-z-]+)\]", prompt)) == set(table)


# --- syllables -----------------------------------------------------------------------------

def test_hyphens_mark_syllables_and_the_rest_is_guessed():
    assert singing.words("Hap-py build-day to you") == [
        ("Happy", 2), ("buildday", 2), ("to", 1), ("you", 1)]
    assert singing.words("code, little leven, Jarvis!") == [
        ("code", 1), ("little", 2), ("leven", 2), ("Jarvis", 2)]
    assert singing.words("you're") == [("you're", 1)]
    assert singing.words("1, 2, 3 -- !") == []


def test_syllables_are_assigned_to_notes():
    assert singing.assign(3, 3) == [[(0, 1.0)], [(1, 1.0)], [(2, 1.0)]]
    # Fewer syllables: one holds two notes.
    assert singing.assign(2, 3) == [[(0, 1.0)], [(1, 1.0), (2, 1.0)]]
    # More syllables: a note is shared.
    assert singing.assign(4, 3) == [[(0, 0.5)], [(0, 0.5)], [(1, 1.0)], [(2, 1.0)]]
    assert singing.assign(0, 3) == []


# --- the tag and who may sing --------------------------------------------------------------

def test_a_tagged_reply_the_driver_asked_for_is_sung():
    text, song = singing.choose(f"[sing: happy-birthday] {BIRTHDAY}",
                                transcript="Jarvis, sing a song", enabled=True, read_back=False)
    assert song is not None and song.tune.id == "happy-birthday"
    assert song.lyrics == BIRTHDAY
    assert text == song.text and text.startswith("Happy buildday to you")


@pytest.mark.parametrize("transcript", [
    "sing happy birthday", "Zing iets voor me", "a song please", "zing eens een liedje"])
def test_the_driver_asks_in_english_or_dutch(transcript):
    assert singing.asked_to_sing(transcript)


@pytest.mark.parametrize("transcript", ["what is open?", "single sign-on", "singapore", ""])
def test_other_words_do_not_ask(transcript):
    assert not singing.asked_to_sing(transcript)


def test_without_the_drivers_request_the_lyrics_are_spoken():
    text, song = singing.choose("[sing: twinkle] Lit-tle build, you are green",
                                transcript="what is the build doing?", enabled=True,
                                read_back=False)
    assert song is None and text == "Little build, you are green"


def test_switched_off_the_lyrics_are_spoken():
    text, song = singing.choose("[sing: twinkle] Lit-tle build", transcript="sing",
                                enabled=False, read_back=False)
    assert song is None and text == "Little build"


def test_a_tag_that_is_not_first_or_names_no_tune_is_removed_and_spoken():
    text, song = singing.choose("I passed it on. [sing: twinkle] la la", transcript="sing",
                                enabled=True, read_back=False)
    assert song is None and text == "I passed it on. la la"
    text, song = singing.choose("[sing: bohemian-rhapsody] Is this the real life",
                                transcript="sing", enabled=True, read_back=False)
    assert song is None and text == "Is this the real life"
    text, song = singing.choose("[SING: Twinkle] Lit-tle", transcript="Sing!",
                                enabled=True, read_back=False)
    assert song is not None and song.tune.id == "twinkle"


TROLLS = ("In the hall the trolls a-wake, they creep, they wait, they stomp, "
          "a-round the moun-tain, down they go, fast-er, fast-er, run")   # 7, 6, 8, 5


@pytest.mark.parametrize("name", [
    "mountain-king", "mountain king", "Mountain_King", "hall-of-the-mountain-king",
    "Hall of the Mountain King", "in the hall of the mountain king"])
def test_the_mountain_king_answers_to_its_aliases(name):
    text, song = singing.choose(f"[sing: {name}] {TROLLS}", transcript="sing something metal",
                                enabled=True, read_back=False)
    assert song is not None and song.tune.id == "mountain-king"
    assert text.startswith("In the hall the trolls awake")


def test_ode_to_joy_is_in_the_table():
    _, song = singing.choose("[sing: ode-to-joy] Joy-ful build, the tests are green",
                             transcript="sing", enabled=True, read_back=False)
    assert song is not None and song.tune.id == "ode-to-joy"


def test_a_copyrighted_song_is_not_in_the_table():
    # The agent is told to decline these and offer mountain-king or iron-man
    # (agent_host/prompt.md).
    text, song = singing.choose("[sing: paranoid] Fin-ished with my build", transcript="sing",
                                enabled=True, read_back=False)
    assert song is None and text == "Finished with my build"


BUTLER = "Hel-lo, I'm Jar-vis, the but-ler in your car, mind the speed bump"   # 5, 6, 4


@pytest.mark.parametrize("name", ["iron-man", "iron man", "Iron_Man", "black sabbath",
                                  "Black-Sabbath"])
def test_the_iron_man_parody_answers_to_its_aliases(name):
    _, song = singing.choose(f"[sing: {name}] {BUTLER}", transcript="sing iron man",
                             enabled=True, read_back=False)
    assert song is not None and song.tune.id == "iron-man" and song.tune.parody


def test_the_parody_switch_takes_only_the_parody_tunes_out():
    assert [t.id for t in TUNES.values() if t.parody] == ["iron-man"]
    assert set(singing.tunes(parody=False)) == set(TUNES) - {"iron-man"}
    text, song = singing.choose(f"[sing: iron man] {BUTLER}", transcript="sing iron man",
                                enabled=True, read_back=False, parody=False)
    assert song is None and text == singing.plain(BUTLER)
    _, song = singing.choose(f"[sing: mountain-king] {TROLLS}", transcript="sing",
                             enabled=True, read_back=False, parody=False)
    assert song is not None and song.tune.id == "mountain-king"


def test_the_iron_man_parody_is_only_the_riff_once_and_short():
    tune = TUNES["iron-man"]
    assert tune.syllables == (5, 6, 4)
    # B, D-D, E-E, G-F#-G-F#-G-F#, D-D, E-E: one pass of the riff and nothing else.
    assert [n.step for n in tune.notes] == [0, 3, 3, 5, 5, 8, 7, 8, 7, 8, 7, 3, 3, 5, 5]
    assert all(len(n.chord) == 2 and n.chord[1] - n.chord[0] == 7
               for n in tune.notes if n.chord)
    seconds = tune.beats * 60 / singing.tempo(tune)
    assert 8 <= seconds <= 12


def test_the_mountain_king_builds_and_stays_short():
    tune = TUNES["mountain-king"]
    assert tune.syllables == (7, 6, 8, 5) and len(tune.notes) == 26
    # The opening theme in B minor: B C# D E F# D F#, then E# C# E#, E C E.
    assert [n.step for n in tune.lines[0]] == [0, 2, 3, 5, 7, 3, 7]
    assert [n.step for n in tune.lines[1]] == [6, 2, 6, 5, 1, 5]
    # The second phrase repeats the first bar's run, a quarter faster.
    first, second = tune.lines[0], tune.lines[2]
    assert [n.step for n in second[:7]] == [n.step for n in first]
    assert all(b.beats == pytest.approx(0.8 * a.beats) for a, b in zip(first[:6], second))
    seconds = tune.beats * 60 / singing.tempo(tune)
    assert singing.tempo(tune) == tune.bpm and 8 < seconds <= singing.MAX_SECONDS


def test_a_read_back_is_never_sung_or_changed():
    readback = "[sing: twinkle] Merge pull request 21 on jarvis. Say yes to go ahead."
    assert singing.choose(readback, transcript="sing it", enabled=True, read_back=True) == (
        readback, None)


def test_a_reply_without_a_tag_is_never_touched():
    spoken = "The roll-out is done, and the follow-up is filed."
    assert singing.choose(spoken, transcript="sing", enabled=True, read_back=False) == (
        spoken, None)


def test_a_tag_with_no_words_is_not_sung():
    assert singing.choose("[sing: twinkle]", transcript="sing", enabled=True,
                          read_back=False) == ("", None)


# --- the spoken line -----------------------------------------------------------------------

def test_a_sung_line_may_be_longer_than_a_spoken_one():
    lyrics = "la-la " * 35                      # 210 characters
    sung = summary.extract_summary(f"{summary.SUMMARY_MARKER} [sing: frere-jacques] {lyrics}")
    assert sung.startswith("[sing: frere-jacques] la-la") and 160 < len(sung) <= 240
    spoken = summary.extract_summary(f"{summary.SUMMARY_MARKER} {lyrics}")
    assert len(spoken) <= summary.MAX_SPOKEN_CHARS


def test_the_tag_passes_the_spoken_filter():
    line = f"{summary.SUMMARY_MARKER} [sing: happy-birthday] {BIRTHDAY}"
    assert summary.extract_summary(line) == f"[sing: happy-birthday] {BIRTHDAY}"


# --- the renderer, with a fake voice -------------------------------------------------------

def test_psola_moves_a_tone_to_the_pitch_and_length_asked():
    x = fake_voice("abcdefgh", hz=150.0)
    for target in (110.0, 196.0, 262.0):
        out = singing.psola(x, lambda t: target, int(0.6 * SR))
        assert len(out) == int(0.6 * SR)
        f0 = singing.f0_track(out[int(0.05 * SR):int(0.55 * SR)])
        assert 12 * math.log2(np.median(f0[f0 > 0]) / target) == pytest.approx(0, abs=0.3)


def test_every_note_is_sung_at_its_pitch():
    for tune_id, lyrics in [("happy-birthday", BIRTHDAY),
                            ("twinkle", "Lit-tle build, you're green at last, all the "
                                        "tests have fi-nal-ly passed"),
                            ("mountain-king", TROLLS), ("iron-man", BUTLER)]:
        song = singing.render(TUNES[tune_id], lyrics, fake_voice, with_accompaniment=False)
        assert song.schedule and song.seconds <= singing.MAX_SECONDS
        assert max(abs(off) for off in sung_semitones_off(song)) < 0.4, tune_id


def test_the_schedule_follows_the_tune():
    tune = TUNES["happy-birthday"]
    song = singing.render(tune, BIRTHDAY, fake_voice, with_accompaniment=False)
    assert song.bpm == tune.bpm
    # 25 syllables on 25 notes: one each, in order, at the tune's own steps.
    assert [n.syllable for n in song.schedule] == list(range(25))
    for scheduled, note in zip(song.schedule, tune.notes):
        assert scheduled.hz == pytest.approx(singing.midi_to_hz(song.tonic_midi + note.step))
        assert scheduled.seconds == pytest.approx(singing.note_seconds(note.beats, song.bpm))
    starts = [n.start_s for n in song.schedule]
    assert starts == sorted(starts) and starts[0] == 0.0


def test_a_song_is_deterministic_bounded_and_not_clipped():
    a = singing.render(TUNES["frere-jacques"], "Are you build-ing " * 8, fake_voice)
    b = singing.render(TUNES["frere-jacques"], "Are you build-ing " * 8, fake_voice)
    assert np.array_equal(a.samples, b.samples)
    assert a.samples.dtype == np.float32
    assert 0.5 < np.abs(a.samples).max() <= 0.9 + 1e-6
    assert a.seconds <= singing.MAX_SECONDS


def test_a_shorter_cap_makes_the_song_faster_not_cut():
    song = singing.render(TUNES["frere-jacques"], "la " * 32, fake_voice, max_seconds=6.0)
    assert song.seconds <= 6.0
    assert song.schedule[-1].start_s + song.schedule[-1].seconds <= 6.0


def test_each_word_is_rendered_once():
    asked = []
    singing.render(TUNES["twinkle"], "la la la la la la la, la la la la la la la",
                   lambda w: asked.append(w) or fake_voice(w))
    assert asked == ["la"]


def test_the_accompaniment_sits_under_the_voice():
    tune = TUNES["twinkle"]
    lyrics = "Lit-tle build, you're green at last, all the tests have fi-nal-ly passed"
    dry = singing.render(tune, lyrics, fake_voice, with_accompaniment=False)
    wet = singing.render(tune, lyrics, fake_voice)
    assert len(dry.samples) == len(wet.samples)
    assert not np.array_equal(dry.samples, wet.samples)
    # Still sung on pitch with the organ under it.
    assert max(abs(off) for off in sung_semitones_off(wet)) < 0.5


def test_the_song_goes_out_as_wav_or_as_half_second_pcm_pieces():
    samples = np.linspace(-0.5, 0.5, SR + 100, dtype=np.float32)
    pieces = tts.pcm_pieces(samples)
    assert [len(p) for p in pieces] == [SR, SR, 200]
    assert b"".join(pieces) == tts.to_l16(samples)
    assert tts.wav_bytes(samples)[:4] == b"RIFF"


# --- the server ----------------------------------------------------------------------------

class Singer:
    """A fake tts.sing: remembers the song, returns a short float signal."""

    def __init__(self, fail=False):
        self.songs = []
        self.requests = []
        self.fail = fail

    def __call__(self, song, *, voice):
        self.songs.append((song.tune.id, song.lyrics, voice))
        self.requests.append(song)
        if self.fail:
            raise RuntimeError("no song today")
        return np.full(SR // 4, 0.25, dtype=np.float32)


@pytest.fixture
def full(tmp_path, monkeypatch):
    b = Bridge(tmp_path, monkeypatch)
    b.singer = Singer()
    monkeypatch.setattr(tts, "sing", b.singer)
    monkeypatch.setattr(tts, "wav_bytes", lambda samples: b"SUNG:%d" % len(samples))
    yield b
    b.stop()


TAGGED = "SPOKEN SUMMARY: [sing: twinkle] Lit-tle build, you're green at last"


def test_a_full_reply_the_driver_asked_to_be_sung_is_sung(full):
    full.host.scenario(mode="wrapper", spoken=TAGGED)
    response, said = full.say("Jarvis, sing me a song")
    assert response.status == 200
    assert said == "SUNG:%d" % (SR // 4)
    assert full.singer.songs == [("twinkle", "Lit-tle build, you're green at last", "af_heart")]
    assert response.getheader("X-Jarvis-Summary") == "Little%20build%2C%20you%27re%20green%20at%20last"


def test_a_sung_reply_is_streamed_whole_in_pcm(full):
    full.host.scenario(mode="wrapper", spoken=TAGGED)
    response, data = _say(full, "sing something")
    assert response.getheader("Content-Type") == STREAM_ACCEPT
    assert data == tts.to_l16(np.full(SR // 4, 0.25, dtype=np.float32))
    assert "tts=" in response.getheader("X-Jarvis-Timing")


def test_a_tagged_reply_nobody_asked_to_hear_sung_is_spoken(full):
    full.host.scenario(mode="wrapper", spoken=TAGGED)
    response, said = full.say("what is open?")
    assert said == "WAV:Little build, you're green at last"
    assert full.singer.songs == []


def test_a_read_back_is_never_sung(full):
    full.host.scenario(mode="wrapper", gate=True, spoken=TAGGED)
    response, said = full.say("sing a song, then merge the pull request")
    assert response.getheader("X-Jarvis-Needs-Confirmation") == "1"
    assert said.startswith("WAV:") and "[sing" not in said
    assert full.singer.songs == []


def test_switched_off_nothing_is_sung(full):
    full.stop()
    full.config = dataclasses.replace(full.config, sing=False)
    full.start()
    full.host.scenario(mode="wrapper", spoken=TAGGED)
    response, said = full.say("sing a song")
    assert said == "WAV:Little build, you're green at last"
    assert full.singer.songs == []


def test_a_song_that_fails_to_render_is_spoken(full, monkeypatch):
    full.singer.fail = True
    full.host.scenario(mode="wrapper", spoken=TAGGED)
    response, said = full.say("sing a song")
    assert response.status == 200
    assert said == "WAV:Little build, you're green at last"


def test_a_readonly_reply_is_sung_too(tmp_path, monkeypatch):
    b = Bridge(tmp_path, monkeypatch, mode="readonly")
    try:
        singer = Singer()
        monkeypatch.setattr(tts, "sing", singer)
        monkeypatch.setattr(tts, "wav_bytes", lambda samples: b"SUNG")
        monkeypatch.setattr(claude_adapter, "run_turn", lambda **kwargs: (
            "Here you go.\n" + TAGGED, "session-ro"))
        response, said = b.say("zing een liedje")
        assert said == "SUNG"
        assert singer.songs[0][0] == "twinkle"
    finally:
        b.stop()


def test_the_switch_reads_0_1_or_unset(monkeypatch):
    from bridge import config
    from bridge.config import ConfigError
    monkeypatch.setenv("JARVIS_BIND_HOST", "100.64.0.1")
    monkeypatch.setenv("JARVIS_TOKEN", "secret")
    monkeypatch.setenv("JARVIS_PROJECTS", "demo=/tmp")
    monkeypatch.delenv("JARVIS_SING", raising=False)
    assert config.load_config().sing is True
    monkeypatch.setenv("JARVIS_SING", "0")
    assert config.load_config().sing is False
    monkeypatch.setenv("JARVIS_SING", "1")
    assert config.load_config().sing is True
    monkeypatch.setenv("JARVIS_SING", "loud")
    with pytest.raises(ConfigError):
        config.load_config()


def test_the_parody_switch_reads_0_1_or_unset(monkeypatch):
    from bridge import config
    from bridge.config import ConfigError
    monkeypatch.setenv("JARVIS_BIND_HOST", "100.64.0.1")
    monkeypatch.setenv("JARVIS_TOKEN", "secret")
    monkeypatch.setenv("JARVIS_PROJECTS", "demo=/tmp")
    monkeypatch.delenv("JARVIS_SING", raising=False)
    monkeypatch.delenv("JARVIS_SING_PARODY", raising=False)
    assert config.load_config().sing_parody is True
    monkeypatch.setenv("JARVIS_SING_PARODY", "0")
    loaded = config.load_config()
    assert loaded.sing_parody is False and loaded.sing is True
    monkeypatch.setenv("JARVIS_SING_PARODY", "1")
    assert config.load_config().sing_parody is True
    monkeypatch.setenv("JARVIS_SING_PARODY", "metal")
    with pytest.raises(ConfigError):
        config.load_config()


def test_with_the_parody_switched_off_iron_man_is_spoken(full):
    full.stop()
    full.config = dataclasses.replace(full.config, sing_parody=False)
    full.start()
    full.host.scenario(mode="wrapper", spoken=f"SPOKEN SUMMARY: [sing: iron-man] {BUTLER}")
    response, said = full.say("sing iron man")
    assert said == f"WAV:{singing.plain(BUTLER)}"
    assert full.singer.songs == []


def test_tunes_are_plain_data():
    tune = Tune("x", "X", 60, ((Note(0, 1, (0, 4, 7)), Note(2, 2)),))
    assert tune.syllables == (2,) and tune.beats == 3 and len(tune.notes) == 2



@pytest.mark.parametrize("text,sung", [
    ("[sing: happy birthday] Hap-py day to you", True),
    ("[sing: happy_birthday] Hap-py day to you", True),
    ("[sing twinkle] Lit-tle star", True),
    ("[sing: twinkle Lit-tle star", False),
    ("Sure. [sing: twinkle] Lit-tle star", False),
    ("[sing: no-such-tune] la la", False),
])
def test_a_malformed_or_misplaced_tag_is_never_spoken_aloud(text, sung):
    # review of #78
    said, song = singing.choose(text, transcript="please sing", enabled=True, read_back=False)
    assert "[" not in said and "sing:" not in said.lower()
    assert (song is not None) == sung


# --- after a notice, the fallback or project news (jarvis#78 follow-up) ---------------------

@pytest.mark.parametrize("code", sorted(NOTICE_SENTENCES))
def test_a_tag_right_after_a_notice_is_still_sung(code):
    sentence = NOTICE_SENTENCES[code]
    text, song = singing.choose(f"{sentence} [sing: twinkle] Lit-tle build",
                                transcript="sing a song", enabled=True, read_back=False,
                                framing=FRAMING_SENTENCES)
    assert song is not None and song.tune.id == "twinkle"
    assert song.before == sentence and song.lyrics == "Lit-tle build" and song.after == ""
    assert text == f"{sentence} Little build"


def test_the_read_only_fallback_is_spoken_before_the_song_and_keeps_its_hyphen():
    sentence = next(iter(FALLBACK_SENTENCES.values()))
    assert "read-only" in sentence
    spoken = f"{sentence} [sing: twinkle] Lit-tle build"
    _, song = singing.choose(spoken, transcript="sing", enabled=True, read_back=False,
                             framing=FRAMING_SENTENCES)
    assert song.before == sentence
    # Not sung: the tag goes, the lyrics lose their hyphens, the fixed sentence does not.
    text, song = singing.choose(spoken, transcript="what now", enabled=True, read_back=False,
                                framing=FRAMING_SENTENCES)
    assert song is None and text == f"{sentence} Little build"


def test_news_after_the_song_is_spoken_after_it():
    sentence = NOTICE_SENTENCES["new_session"]
    spoken = f"{sentence} [sing: twinkle] Lit-tle build {PROJECT_READY}"
    text, song = singing.choose(spoken, transcript="sing", enabled=True, read_back=False,
                                framing=FRAMING_SENTENCES)
    assert (song.before, song.lyrics, song.after) == (sentence, "Lit-tle build", PROJECT_READY)
    assert text == f"{sentence} Little build {PROJECT_READY}"


def test_only_the_bridges_own_sentences_are_taken_off():
    # Anything else before the tag still means: not first, not sung.
    text, song = singing.choose("Sure thing. [sing: twinkle] Lit-tle build",
                                transcript="sing", enabled=True, read_back=False,
                                framing=FRAMING_SENTENCES)
    assert song is None and text == "Sure thing. Little build"


def test_a_read_back_after_a_notice_is_untouched():
    spoken = f"{NOTICE_SENTENCES['new_session']} [sing: twinkle] Merge it. Say yes."
    assert singing.choose(spoken, transcript="sing", enabled=True, read_back=True,
                          framing=FRAMING_SENTENCES) == (spoken, None)


@pytest.mark.parametrize("code", sorted(NOTICE_SENTENCES))
def test_the_server_sings_after_each_notice(full, code):
    full.host.scenario(mode="wrapper", notice=code, spoken=TAGGED)
    response, said = full.say("Jarvis, sing me a song")
    assert response.status == 200
    assert said == "SUNG:%d" % (SR // 4)
    request = full.singer.requests[-1]
    assert request.before == NOTICE_SENTENCES[code]
    assert request.lyrics == "Lit-tle build, you're green at last"
    summary_header = urllib.parse.unquote(response.getheader("X-Jarvis-Summary"))
    assert summary_header == f"{NOTICE_SENTENCES[code]} Little build, you're green at last"


def test_a_notice_is_spoken_before_the_song_and_news_after_it(monkeypatch):
    said = []

    def word_audio(text, *, voice):
        said.append(text)
        return np.full(100, 0.1, dtype=np.float32)
    monkeypatch.setattr(tts, "word_audio", word_audio)
    song = singing.SongRequest(TUNES["twinkle"], "la la la la la la la", before="Notice.",
                               after="News.")
    samples = tts.sing(song, voice="bm_george")
    assert said[0] == "la" and said[-2:] == ["Notice.", "News."]
    pause = int(tts.SONG_PAUSE_S * SR)
    assert np.all(samples[:100] == np.float32(0.1))
    assert np.all(samples[100:100 + pause] == 0)
    assert np.all(samples[-100:] == np.float32(0.1))
    sung = singing.render(TUNES["twinkle"], "la la la la la la la", lambda w: word_audio(w, voice=""))
    assert len(samples) == 200 + 2 * pause + len(sung.samples)
