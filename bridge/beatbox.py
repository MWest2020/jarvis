"""Beatboxing: the second easter egg (Mark, 2026-11-18, change jarvis-beatbox: "Can it
beatbox?").

When the driver asks for a beat, the agent starts its spoken summary with a tag, with or
without a pattern name, and one short line of its own that Jarvis says before the beat:

    SPOKEN SUMMARY: [beatbox: hip-hop] Alright, here's a beat for the road.

The bridge owns the patterns, a small table below: a 16-step grid per bar, written as
mouth sounds (B kick, K and P snares, t and T hi-hats). Every sound is made the way the
singing is, from what the bridge already has: the attack of a word in Jarvis's own voice
(Kokoro, through the `speak` callable: the "b" of "boots", the "k" of "cats", the "t" of
"tss"), cut short and shaped, layered over a synthesised body (a falling sine for the
kick, filtered noise for snares and hats), as a beatboxer's lips, tongue and breath do.
One pattern adds a hummed bass line and a sung hook ("Jar-vis", "yeah"), moved onto
their notes with the TD-PSOLA of bridge/singing.py. numpy only, no samples, no model.

Off the normal path, like singing (`choose`): only when the tag is first on the agent's
own line, the driver's words in this turn ask for a beat, the reply is not a read-back,
and JARVIS_BEATBOX is not 0. Otherwise every tag is removed and the line is spoken.
"""
from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from bridge import singing
from bridge.singing import SAMPLE_RATE

# The longest beat, in seconds (the intro line Jarvis says before it not counted).
MAX_SECONDS = 15.0
MIN_SECONDS = 5.0
STEPS_PER_BAR = 16

# The tag, at the start of the summary: "[beatbox]", "[beatbox: hip-hop]",
# "[beat-box: boots and cats]", "[beat box]", "[beatboxing]".
TAG = re.compile(r"\[\s*beat[- ]?box(?:ing)?\s*(?::\s*([A-Za-z _-]{0,40}?))?\s*\]\s*",
                 re.IGNORECASE)
# A closed tag TAG does not take ("[beatbox: drum & bass!]"): taken out up to its
# bracket, bounded so an unclosed tag never swallows the words after it.
ODD_TAG = re.compile(r"\[\s*beat[- ]?box\w*[^\]\[\n]{0,60}\]\s*", re.IGNORECASE)
# A broken tag ("[beatbox: hip-hop Here we go", no bracket): never spoken.
OPEN_TAG = re.compile(r"\[\s*beat[- ]?box\w*\s*:?\s*[A-Za-z_-]*\s*", re.IGNORECASE)
# What the driver must have said in this turn: a word that asks for a beat, in English
# or Dutch ("beatbox", "beat box", "doe eens een beat", "een beatje", "drop a beat",
# "boots and cats").
ASKED = re.compile(r"\b(beat[- ]?box\w*|beats?|beatjes?|boots and cats)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Hook:
    """A word sung over the beat: where it starts (bar, step), and per syllable its note
    (semitones from the voice's own pitch) and length in steps."""

    bar: int
    step: int
    word: str                                 # syllables joined by hyphens: "Jar-vis"
    notes: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class Pattern:
    id: str
    title: str
    bpm: float
    # One string per bar, STEPS_PER_BAR tokens: a sound from KIT, or "." for a rest.
    bars: tuple[str, ...]
    # Said instead when the beat cannot be rendered, and shown on the phone when the
    # agent wrote no line of its own.
    spoken: str
    # How late every second sixteenth comes, as a share of a sixteenth (a lazy swing).
    swing: float = 0.0
    # The hummed bass: (bar, step, semitones from an octave below the voice, steps).
    hum: tuple[tuple[int, int, int, int], ...] = ()
    hook: tuple[Hook, ...] = ()

    @property
    def step_seconds(self) -> float:
        return 60.0 / self.bpm / 4

    @property
    def seconds(self) -> float:
        return len(self.bars) * STEPS_PER_BAR * self.step_seconds


# The mouth sounds a bar is written in, and how loud each is against the others.
KIT = {
    "B": 1.0,     # kick: the lips' "b", a falling thump under it
    "b": 0.55,    # a softer kick
    "K": 1.0,     # snare: the tongue's "k" of "cats", a noise burst under it
    "k": 0.45,    # a ghost snare
    "P": 1.0,     # snare: the beatboxer's "Pf", lips then breath
    "t": 0.45,    # closed hi-hat: the "t" of "tss"
    "T": 0.45,    # open hi-hat: "tsss"
}

# The patterns, all written for this table (a beat is not a melody: the grooves are the
# common grammar of beatboxing, the hook and bass line are Jarvis's own).
PATTERNS: dict[str, Pattern] = {p.id: p for p in (
    # The first beat every beatboxer learns: "boots and cats and boots and cats".
    Pattern("boots-and-cats", "Boots and cats", 100, (
        "B . t . K . t . B . t . K . t .",
        "B . t . K . t . B . t . K . t .",
        "B . t . K . t . B . t . K . t .",
        "B . t . K . t . B . B . K . T .",
    ), spoken="Boots and cats and boots and cats."),
    # A basic hip-hop (boom-bap) beat: a kick that skips, "Pf" snares, a little swing.
    Pattern("hip-hop", "Hip-hop beat", 90, (
        "B . t . P . t B . B t . P . t .",
        "B . t b P . t . . B t . P . T .",
        "B . t . P . t B . B t . P . t .",
        "B . t b P . t . B . B . P k T .",
    ), spoken="Boom, tss, pf, tss, boom boom, pf.", swing=0.18),
    # A beat with a hummed bass line and a little vocal hook.
    Pattern("jarvis-hook", "Beat with a vocal hook", 96, (
        "B . t . K . t t B . B . K . t .",
        "B . t . K . t t B . B . K . t .",
        "B . t . K . t t B . B . K . t .",
        "B . t . K . t t B . B . K . T .",
    ), spoken="Boom, tss, k, Jarvis, yeah.",
       hum=tuple((bar, step, semi, length) for bar in range(4)
                 for step, semi, length in ((0, 0, 3), (6, 0, 2), (8, 3, 2), (10, 5, 3))),
       hook=(Hook(1, 12, "Jar-vis", ((7, 2), (3, 2))),
             Hook(3, 8, "Jar-vis", ((7, 2), (10, 2))),
             Hook(3, 12, "yeah", ((7, 4),)))),
)}

# Other names the agent may write (after normalising: lower case, spaces and underscores
# to "-").
ALIASES = {
    "boots-cats": "boots-and-cats",
    "boots-n-cats": "boots-and-cats",
    "basic": "boots-and-cats",
    "hiphop": "hip-hop",
    "boom-bap": "hip-hop",
    "boombap": "hip-hop",
    "hook": "jarvis-hook",
    "jarvis": "jarvis-hook",
    "vocal-hook": "jarvis-hook",
}


# --- the tag and when to beatbox --------------------------------------------------------

@dataclass(frozen=True)
class BeatRequest:
    pattern: Pattern
    intro: str            # the agent's own line, said before the beat
    # The bridge's own fixed sentences around the agent's line (see singing.peel):
    # spoken before and after the beat.
    before: str = ""
    after: str = ""

    @property
    def text(self) -> str:
        """What the phone shows (X-Jarvis-Summary), and what is said instead when the
        beat cannot be rendered."""
        return _join(self.before, self.intro or self.pattern.spoken, self.after)


def _join(*parts: str) -> str:
    return " ".join(part for part in parts if part)


def split_tag(spoken: str, rng: random.Random | None = None,
              ) -> tuple[Pattern | None, str, bool]:
    """(the pattern, the text without any tag, whether there was a tag). A pattern only
    when the tag is the very first thing: the one it names, or one at random when it
    names none or one not in the table (every pattern is the bridge's own)."""
    match = TAG.match(spoken)
    pattern = None
    if match:
        name = re.sub(r"[\s_]+", "-", (match.group(1) or "").strip().lower())
        pattern = PATTERNS.get(ALIASES.get(name, name))
        if pattern is None:
            pattern = (rng or random).choice(list(PATTERNS.values()))
    rest, count = TAG.subn("", spoken)
    rest, odd = ODD_TAG.subn("", rest)
    rest, broken = OPEN_TAG.subn("", rest)
    return pattern, " ".join(rest.split()), count + odd + broken > 0


def asked_for_a_beat(transcript: str) -> bool:
    return bool(ASKED.search(transcript or ""))


def choose(spoken: str, *, transcript: str, enabled: bool, read_back: bool,
           framing: tuple[str, ...] = (), rng: random.Random | None = None,
           ) -> tuple[str, BeatRequest | None]:
    """What to say, and the beat when this reply is beatboxed.

    A read-back is returned exactly as built. A reply without a beatbox tag is returned
    unchanged (a sing tag is singing.choose's). The tag must be first on the agent's own
    line, after nothing but the bridge's fixed sentences in `framing`. A tagged reply
    that is not beatboxed loses its tags and is spoken.
    """
    if read_back:
        return spoken, None
    before, core, after = singing.peel(spoken, framing)
    pattern, rest, tagged = split_tag(core, rng)
    if not tagged:
        return spoken, None
    if pattern is None or not enabled or not asked_for_a_beat(transcript):
        return _join(before, rest, after), None
    # A sing tag after the beatbox tag ("[beatbox] [sing: twinkle] ...") is never said.
    _, intro, _ = singing.split_tag(rest)
    beat = BeatRequest(pattern, intro, before, after)
    return beat.text, beat


# --- the sounds (numpy only) ------------------------------------------------------------

# The words the kit takes its attacks from, and how much of each (seconds).
VOICE_ATTACKS = {"B": ("boots", 0.06), "K": ("cats", 0.06), "P": ("puh", 0.05),
                 "t": ("tss", 0.035), "T": ("tss", 0.05)}
# How loud the voice is against the synthesised body of each sound (RMS ratio).
VOICE_SHARE = {"B": 0.7, "K": 0.8, "P": 0.6, "t": 0.6, "T": 0.5}


def _t(seconds: float, sr: int):
    import numpy as np

    return np.arange(int(seconds * sr)) / sr


def _decay(seconds: float, tau: float, sr: int, attack: float = 0.002):
    """An envelope: a short linear attack, then an exponential decay with time constant
    `tau`, reaching silence at the end."""
    import numpy as np

    t = _t(seconds, sr)
    env = np.exp(-t / tau)
    a = max(1, int(attack * sr))
    env[:a] *= np.linspace(0.0, 1.0, a)
    tail = max(1, len(env) // 8)
    env[-tail:] *= np.linspace(1.0, 0.0, tail)
    return env


def band(x, lo: float, hi: float, sr: int = SAMPLE_RATE):
    """`x` with only the frequencies between `lo` and `hi` (a smooth FFT mask)."""
    import numpy as np

    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return x
    spectrum = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    edge = 0.15
    rise = np.clip((np.log2(np.maximum(f, 1.0) / lo) + edge) / (2 * edge), 0.0, 1.0)
    fall = np.clip((np.log2(hi / np.maximum(f, 1.0)) + edge) / (2 * edge), 0.0, 1.0)
    return np.fft.irfft(spectrum * rise * fall, len(x))


def _noise(seconds: float, sr: int, seed: int):
    import numpy as np

    return np.random.default_rng(seed).standard_normal(int(seconds * sr))


def _peak1(x):
    import numpy as np

    peak = float(np.abs(x).max()) if len(x) else 0.0
    return x / peak if peak > 0 else x


def _rms(x) -> float:
    return singing._rms(x)


def _layer(body, voice, share: float):
    """`voice` laid over the start of `body` at `share` of its loudness."""
    import numpy as np

    out = np.array(body, dtype=np.float64)
    if voice is not None and len(voice) and _rms(voice) > 0:
        n = min(len(out), len(voice))
        out[:n] += voice[:n] * (share * _rms(out[:n]) / _rms(voice[:n]))
    return _peak1(out)


def voice_attack(word_audio, seconds: float, lo: float, hi: float, tau: float,
                 sr: int = SAMPLE_RATE):
    """The first `seconds` of a word in Jarvis's voice (silence trimmed), kept to the
    band where that consonant lives, dying away with time constant `tau`."""
    import numpy as np

    x = singing.trim(np.asarray(word_audio, dtype=np.float32), sr)
    n = int(seconds * sr)
    if len(x) == 0:
        return np.zeros(0)
    x = np.asarray(x[:n], dtype=np.float64)
    return band(x, lo, hi, sr) * _decay(len(x) / sr, tau, sr, attack=0.001)[:len(x)]


def kick(sr: int = SAMPLE_RATE, voice=None):
    """A beatbox kick: a sine falling from about 140 Hz to 50 Hz, a click of breath at
    its start, the lips' "b" over it."""
    import numpy as np

    t = _t(0.24, sr)
    freq = 50.0 + 90.0 * np.exp(-t / 0.03)
    body = np.sin(2 * np.pi * np.cumsum(freq) / sr) * _decay(0.24, 0.07, sr)
    click = band(_noise(0.24, sr, 1), 1000, 4000, sr) * _decay(0.24, 0.004, sr, 0.0005)
    body = body + 0.15 * click
    return np.tanh(1.6 * _layer(body, voice, VOICE_SHARE["B"])) / np.tanh(1.6)


def k_snare(sr: int = SAMPLE_RATE, voice=None):
    """A "k" snare: a burst of noise from 1.2 to 7 kHz, a short tone of 190 Hz under
    it, the tongue's "k" over it."""
    import numpy as np

    t = _t(0.18, sr)
    noise = band(_noise(0.18, sr, 2), 1200, 7000, sr) * _decay(0.18, 0.05, sr)
    tone = np.sin(2 * np.pi * 190 * t) * _decay(0.18, 0.03, sr)
    return _layer(_peak1(noise) + 0.5 * tone, voice, VOICE_SHARE["K"])


def pf_snare(sr: int = SAMPLE_RATE, voice=None):
    """The beatboxer's "Pf": a short pop of the lips, then a breath of "f" (noise from
    1.5 to 8 kHz)."""
    import numpy as np

    t = _t(0.2, sr)
    pop = band(_noise(0.2, sr, 3), 150, 1200, sr) * _decay(0.2, 0.006, sr, 0.0005)
    breath = band(_noise(0.2, sr, 4), 1500, 8000, sr) * _decay(0.2, 0.07, sr, 0.008)
    pop = _peak1(pop) + 0.3 * np.sin(2 * np.pi * 160 * t) * _decay(0.2, 0.02, sr)
    return _layer(_peak1(pop) + 0.8 * _peak1(breath), voice, VOICE_SHARE["P"])


def hat(sr: int = SAMPLE_RATE, voice=None, open_: bool = False):
    """A hi-hat: the "t" of the tongue, then a hiss above 5 kHz; short when closed,
    "tsss" when open."""
    seconds, tau = (0.26, 0.09) if open_ else (0.07, 0.016)
    hiss = band(_noise(seconds, sr, 5 if open_ else 6),
                5000, 11000, sr) * _decay(seconds, tau, sr, 0.001)
    return _layer(hiss, voice, VOICE_SHARE["T" if open_ else "t"])


def build_kit(speak: Callable[[str], object], sr: int = SAMPLE_RATE) -> dict:
    """Every sound of KIT, peak 1, at its own loudness. `speak(word)` gives the words
    the attacks are cut from (Kokoro on the bridge, a fake voice in the tests); each
    word is asked for once."""
    import numpy as np

    said: dict[str, np.ndarray] = {}

    def attack(symbol: str, lo: float, hi: float, tau: float):
        word, seconds = VOICE_ATTACKS[symbol]
        if word not in said:
            said[word] = np.asarray(speak(word), dtype=np.float32)
        return voice_attack(said[word], seconds, lo, hi, tau, sr)

    b = kick(sr, attack("B", 60, 1500, 0.03))
    k = k_snare(sr, attack("K", 900, 9000, 0.03))
    p = pf_snare(sr, attack("P", 300, 8000, 0.025))
    t = hat(sr, attack("t", 2500, 11000, 0.012))
    open_t = hat(sr, attack("T", 2500, 11000, 0.03), open_=True)
    sounds = {"B": b, "b": b, "K": k, "k": k, "P": p, "t": t, "T": open_t}
    return {symbol: (sounds[symbol] * gain).astype(np.float64) for symbol, gain in KIT.items()}


# --- the beat ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Hit:
    start_s: float
    sound: str


@dataclass(frozen=True)
class SungNote:
    start_s: float
    seconds: float
    hz: float
    part: str             # "hum" or "hook"
    fall: float = 0.0     # semitones the pitch falls over the note


@dataclass
class Beat:
    samples: object                       # float32 numpy array at SAMPLE_RATE
    hits: list[Hit] = field(default_factory=list)
    notes: list[SungNote] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return len(self.samples) / SAMPLE_RATE


def hits(pattern: Pattern) -> list[Hit]:
    """Every drum sound of the pattern, in time order."""
    out = []
    for bar_index, bar in enumerate(pattern.bars):
        for step, token in enumerate(bar.split()):
            if token == ".":
                continue
            late = pattern.swing if step % 2 else 0.0
            out.append(Hit((bar_index * STEPS_PER_BAR + step + late) * pattern.step_seconds,
                           token))
    return out


# How loud the hum and the hook are against the drums (RMS ratio over the beat).
HUM_LEVEL = 0.35
HOOK_LEVEL = 1.1
# The last syllable of a hook word falls this many semitones, as a spoken "yeah" does.
HOOK_FALL = 1.5
# How loud the beat is: its RMS this many dB above the speech around it, so that it is
# not louder than Jarvis's voice at the same volume in the car (review of #86: with a
# peak of 0.9 it was 9 to 10 dB louder).
BEAT_LIFT_DB = 2.0
# Kokoro's speaking level (RMS of a sentence, about -25 dBFS for bm_george), for a beat
# with no speech to measure.
DEFAULT_SPEECH_RMS = 0.056
MAX_PEAK = 0.9
TAIL_S = 0.35


def _sing(audio, segments: list[tuple[float, float]], glide: float, sr: int):
    """`audio` moved onto `segments` ((hz, seconds) per note) with singing.psola, a
    small fall (`glide` semitones) over its last note."""
    total = sum(seconds for _hz, seconds in segments)
    starts, at = [], 0.0
    for _hz, seconds in segments:
        starts.append(at)
        at += seconds

    def target(t: float) -> float:
        i = len(starts) - 1
        while i > 0 and t < starts[i]:
            i -= 1
        hz, seconds = segments[i]
        fall = glide * max(0.0, (t - starts[i]) / seconds) if i == len(starts) - 1 else 0.0
        return hz * 2.0 ** (-fall / 12.0)
    return singing._fade(singing.psola(audio, target, int(total * sr), sr), sr)


def render(pattern: Pattern, speak: Callable[[str], object], *, sr: int = SAMPLE_RATE,
           drums: bool = True, voices: bool = True, level: float | None = None) -> Beat:
    """The beat for `pattern`. `speak(word)` returns the word as float samples at `sr`
    (Kokoro on the bridge). `drums` and `voices` (the hum and the hook) can be left out,
    for the tests. `level`: the RMS of the speech said with the beat; the beat's RMS is
    BEAT_LIFT_DB above it (without it, above the words the beat was made from)."""
    import numpy as np

    asked: dict[str, np.ndarray] = {}

    def word(text: str):
        key = text.lower()
        if key not in asked:
            asked[key] = np.asarray(speak(text), dtype=np.float32)
        return asked[key]

    length = int(min(MAX_SECONDS, pattern.seconds + TAIL_S) * sr)
    drum_mix = np.zeros(length)
    beat_hits = hits(pattern)
    if drums:
        kit = build_kit(word, sr)
        for hit in beat_hits:
            sound = kit[hit.sound]
            a = int(hit.start_s * sr)
            n = min(len(sound), length - a)
            if n > 0:
                drum_mix[a:a + n] += sound[:n]

    notes: list[SungNote] = []
    voice_mix = np.zeros(length)
    if voices and (pattern.hum or pattern.hook):
        spoken = [singing.trim(word(w), sr) for w in
                  ["mmm"] * bool(pattern.hum)
                  + [h.word.replace("-", "") for h in pattern.hook]]
        voice_midi = singing.hz_to_midi(singing.median_f0(spoken, sr))
        step = pattern.step_seconds
        hum_mix = np.zeros(length)
        if pattern.hum:
            hum = spoken[0]
            for bar, at, semi, steps in pattern.hum:
                start, seconds = (bar * STEPS_PER_BAR + at) * step, steps * step * 0.9
                hz = singing.midi_to_hz(voice_midi - 12 + semi)
                # Kept below 1.2 kHz: a hum, not the word "mmm".
                sung = band(_sing(hum, [(hz, seconds)], 0.0, sr), 40, 1200, sr)
                # A sine at the note's pitch gives the hum the weight of a bass.
                tt = np.arange(len(sung)) / sr
                sung = sung / max(_rms(sung), 1e-6) + 0.8 * np.sqrt(2) * np.sin(
                    2 * np.pi * hz * tt) * singing._fade(np.ones(len(sung), np.float32), sr)
                a = int(start * sr)
                n = min(len(sung), length - a)
                if n > 0:
                    hum_mix[a:a + n] += sung[:n]
                notes.append(SungNote(start, seconds, hz, "hum"))
        hook_mix = np.zeros(length)
        for h in pattern.hook:
            audio = word(h.word.replace("-", ""))
            syllables = singing.split_syllables(singing.trim(audio, sr), len(h.notes), sr)
            at = (h.bar * STEPS_PER_BAR + h.step) * step
            for i, (syllable, (semi, steps)) in enumerate(zip(syllables, h.notes)):
                seconds = steps * step
                hz = singing.midi_to_hz(voice_midi + semi)
                fall = HOOK_FALL if i == len(h.notes) - 1 else 0.0
                if len(syllable):
                    sung = _sing(syllable, [(hz, seconds)], fall, sr)
                    sung = sung / max(_rms(sung), 1e-6)
                    a = int(at * sr)
                    n = min(len(sung), length - a)
                    if n > 0:
                        hook_mix[a:a + n] += sung[:n]
                notes.append(SungNote(at, seconds, hz, "hook", fall))
                at += seconds
        reference = _rms(drum_mix) if drums and _rms(drum_mix) > 0 else 0.1
        if _rms(hum_mix) > 0:
            voice_mix += hum_mix * (HUM_LEVEL * reference / _rms(hum_mix))
        if _rms(hook_mix) > 0:
            voice_mix += hook_mix * (HOOK_LEVEL * reference / _rms(hook_mix))

    mix = drum_mix + voice_mix
    peak = float(np.abs(mix).max()) if length else 0.0
    if peak > 0:
        # A little glue (soft saturation), then brought to the level of the speech.
        mix = np.tanh(1.4 * mix / peak)
        if level is None:
            words = [w for w in asked.values() if len(w)]
            level = _rms(np.concatenate(words)) if words else 0.0
        target = (level or DEFAULT_SPEECH_RMS) * 10 ** (BEAT_LIFT_DB / 20)
        mix = mix * (target / max(_rms(mix), 1e-9))
        peak = float(np.abs(mix).max())
        if peak > MAX_PEAK:
            mix = mix * (MAX_PEAK / peak)
    fade = min(length, int(0.05 * sr))
    if fade:
        mix[-fade:] *= np.linspace(1.0, 0.0, fade)
    return Beat(mix.astype(np.float32), beat_hits, sorted(notes, key=lambda n: n.start_s))
