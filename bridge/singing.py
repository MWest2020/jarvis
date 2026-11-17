"""Singing: an easter egg (Mark, 2026-11-17, change jarvis-sing).

When the driver asks Jarvis to sing, the agent starts its spoken summary with a tag
that names a tune from the small table below, followed by its own short lyrics:

    SPOKEN SUMMARY: [sing: twinkle] Lit-tle build, you're green at last, ...

The bridge owns the melodies. It renders every word with the voice it already has
(Kokoro, through the `speak` callable), cuts each word into its syllables, and moves
every syllable onto its note: a pitch-synchronous overlap-add (TD-PSOLA, in numpy
only) gives the syllable the note's pitch, held steady with a little vibrato, and
stretches its vowel to the note's length. A soft organ chord goes under it. No model
is loaded here and nothing beyond numpy is needed.

It is off the normal path: a reply is sung only when all of these hold (`choose`):
the tag stands at the very start of the agent's summary and names a known tune, the
driver's own words in this turn ask for a song, the reply is not a read-back, and
JARVIS_SING is not 0 (and, for a parody tune, JARVIS_SING_PARODY is not 0). Otherwise
every tag is removed and the lyrics are spoken.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field

SAMPLE_RATE = 24000
# The longest song, in seconds. A tune that would take longer is sung faster.
MAX_SECONDS = 20.0

# The tag, at the start of the summary: "[sing: happy-birthday]".
TAG = re.compile(r"\[\s*sing\s*:?\s*([A-Za-z _-]*?)\s*\]\s*", re.IGNORECASE)
# A broken tag the agent may write ("[sing: twinkle hi", no closing bracket): never
# spoken aloud, always taken out (review of #78).
OPEN_TAG = re.compile(r"\[\s*sing\b\s*:?\s*[A-Za-z_-]*\s*", re.IGNORECASE)
# A closed tag with a name TAG does not take ("[sing: Iron Man!]", "[sing: In the Hall
# of the Mountain King (Grieg)]"): taken out up to its bracket, so no part of the name
# is spoken. Bounded, so an unclosed tag never swallows the lyrics after it (review of
# #81).
ODD_TAG = re.compile(r"\[\s*sing\b[^\]\[\n]{0,60}\]\s*", re.IGNORECASE)
# What the driver must have said in this turn for a reply to be sung: a word that asks
# for a song, in English or Dutch. The agent's tag alone is not enough.
ASKED = re.compile(
    r"\b(sing|sings|singing|sang|song|songs|serenade|zing|zingt|zingen|zong|"
    r"lied|liedje|liedjes)\b", re.IGNORECASE)

# Chords as semitones above the tonic: the tonic, the subdominant, the dominant.
I = (0, 4, 7)
IV = (5, 9, 12)
V = (7, 11, 14)


@dataclass(frozen=True)
class Note:
    """One note: semitones from the tonic, length in beats, and the chord that starts
    under it (None: the previous chord goes on)."""

    step: int
    beats: float
    chord: tuple[int, ...] | None = None


@dataclass(frozen=True)
class Tune:
    id: str
    title: str
    bpm: float
    lines: tuple[tuple[Note, ...], ...]
    # A parody or pastiche of a song still under copyright: switched off on its own
    # with JARVIS_SING_PARODY=0 (see `tunes`).
    parody: bool = False

    @property
    def notes(self) -> tuple[Note, ...]:
        return tuple(note for line in self.lines for note in line)

    @property
    def syllables(self) -> tuple[int, ...]:
        """Syllables per line: one per note."""
        return tuple(len(line) for line in self.lines)

    @property
    def beats(self) -> float:
        return sum(note.beats for note in self.notes)


def _line(*notes) -> tuple[Note, ...]:
    return tuple(Note(*n) for n in notes)


def _quicker(factor: float, line: tuple[Note, ...]) -> tuple[Note, ...]:
    """The same notes, each `factor` times as long: a written-out accelerando (the
    table has one tempo per tune, so a line that should go faster gets shorter notes)."""
    return tuple(Note(n.step, n.beats * factor, n.chord) for n in line)


# Public-domain melodies, and one marked parody (see docs/how-to/bridge-service.md,
# "Singing"). The lyrics are always the agent's own. Steps are semitones from the tonic
# (C = 0, G below it = -5).
TUNES: dict[str, Tune] = {t.id: t for t in (
    # "Good Morning to All" (Mildred and Patty Hill, 1893), 3/4.
    Tune("happy-birthday", "Happy Birthday", 110, (
        _line((-5, .75, I), (-5, .25), (-3, 1), (-5, 1), (0, 1), (-1, 2, V)),
        _line((-5, .75), (-5, .25), (-3, 1), (-5, 1), (2, 1), (0, 2, I)),
        _line((-5, .75), (-5, .25), (7, 1), (4, 1), (0, 1), (-1, 1, IV), (-3, 2)),
        _line((5, .75), (5, .25), (4, 1, I), (0, 1), (2, 1, V), (0, 3, I)),
    )),
    # "Ah! vous dirai-je, maman" (18th century), the first two lines.
    Tune("twinkle", "Twinkle Twinkle Little Star", 100, (
        _line((0, 1, I), (0, 1), (7, 1), (7, 1), (9, 1, IV), (9, 1), (7, 2, I)),
        _line((5, 1, IV), (5, 1), (4, 1, I), (4, 1), (2, 1, V), (2, 1), (0, 2, I)),
    )),
    # "Frère Jacques" / "Vader Jacob" (18th century).
    Tune("frere-jacques", "Frère Jacques", 120, (
        _line((0, 1, I), (2, 1), (4, 1), (0, 1)),
        _line((0, 1), (2, 1), (4, 1), (0, 1)),
        _line((4, 1), (5, 1), (7, 2)),
        _line((4, 1), (5, 1), (7, 2)),
        _line((7, .5), (9, .5), (7, .5), (5, .5), (4, 1), (0, 1)),
        _line((7, .5), (9, .5), (7, .5), (5, .5), (4, 1), (0, 1)),
        _line((0, 1), (-5, 1, V), (0, 2, I)),
        _line((0, 1), (-5, 1, V), (0, 2, I)),
    )),
    # "In the Hall of the Mountain King" (Grieg, Peer Gynt, 1875), the opening theme as
    # the score has it, in B minor (B = 0): a bar per line, eighths and quarters. The
    # organ holds only the dominant (F sharp), no third. The last two bars are a
    # quarter faster, so the riff builds the way the piece does.
    Tune("mountain-king", "In the Hall of the Mountain King", 100, (
        _line((0, .5, (7,)), (2, .5), (3, .5), (5, .5), (7, .5), (3, .5), (7, 1)),
        _line((6, .5), (2, .5), (6, 1), (5, .5), (1, .5), (5, 1)),
        *(_quicker(.8, line) for line in (
            _line((0, .5), (2, .5), (3, .5), (5, .5), (7, .5), (3, .5), (7, .5), (12, .5)),
            _line((10, .5), (7, .5), (3, .5), (7, .5), (10, 2)),
        )),
    )),
    # PARODY / PASTICHE, not a public-domain melody: riff after Black Sabbath, "Iron Man"
    # (1970). Only the main riff motif is evoked, once, transcribed from memory, in B
    # (B = 0) with a power chord (root and fifth) under every note; no other part of the
    # song. Non-commercial, the words are always the agent's own parody about Jarvis,
    # never the song's. Relies on the parody/pastiche exception (Dutch Auteurswet
    # art. 18b; EU Directive 2001/29/EC art. 5(3)(k)); removed on request of the rights
    # holder. Off on its own with JARVIS_SING_PARODY=0.
    Tune("iron-man", "Iron Man (parody)", 80, (
        _line((0, 1.5, (0, 7)), (3, 1, (3, 10)), (3, .5), (5, 1, (5, 12)), (5, 1)),
        _line((8, .5, (8, 15)), (7, .5, (7, 14)), (8, .5, (8, 15)), (7, .5, (7, 14)),
              (8, .5, (8, 15)), (7, .5, (7, 14))),
        _line((3, 1, (3, 10)), (3, .5), (5, 1, (5, 12)), (5, 1.5)),
    ), parody=True),
    # "Ode to Joy" (Beethoven, Symphony No. 9, 1824), the first two phrases.
    Tune("ode-to-joy", "Ode to Joy", 120, (
        _line((4, 1, I), (4, 1), (5, 1), (7, 1), (7, 1, V), (5, 1), (4, 1), (2, 1)),
        _line((0, 1, I), (0, 1), (2, 1), (4, 1), (4, 1.5), (2, .5, V), (2, 2)),
        _line((4, 1, I), (4, 1), (5, 1), (7, 1), (7, 1, V), (5, 1), (4, 1), (2, 1)),
        _line((0, 1, I), (0, 1), (2, 1), (4, 1), (2, 1.5, V), (0, .5, I), (0, 2)),
    )),
)}

# Other names the agent may write for a tune (after normalising: lower case, spaces and
# underscores to "-").
ALIASES = {
    "hall-of-the-mountain-king": "mountain-king",
    "in-the-hall-of-the-mountain-king": "mountain-king",
    "black-sabbath": "iron-man",
}


def tunes(parody: bool = True) -> dict[str, Tune]:
    """The table the bridge sings from: without the parody tunes when `parody` is off
    (JARVIS_SING_PARODY=0); a tag naming one is then removed and its words spoken."""
    return {name: t for name, t in TUNES.items() if parody or not t.parody}


# --- the tag and who may sing ----------------------------------------------------------

@dataclass(frozen=True)
class SongRequest:
    tune: Tune
    lyrics: str          # as the agent wrote them, syllables joined by hyphens
    # The bridge's own fixed sentences around the agent's line (a notice of the agent
    # host, the read-only fallback, news of a new project): spoken before and after
    # the song, never sung.
    before: str = ""
    after: str = ""

    @property
    def text(self) -> str:
        """What the phone shows (X-Jarvis-Summary): the sentences around the song and
        the lyrics as words."""
        return _join(self.before, plain(self.lyrics), self.after)


def _join(*parts: str) -> str:
    return " ".join(part for part in parts if part)


def peel(spoken: str, framing: tuple[str, ...] = ()) -> tuple[str, str, str]:
    """(fixed sentences before, the agent's own line, fixed sentences after).

    `framing` is the closed list of sentences the bridge itself puts around an agent's
    line (bridge.full_mode.FRAMING_SENTENCES). Only whole sentences from that list are
    taken off, as many as there are, so the tag of a line that follows a notice
    ("I started a new session for this project. [sing: twinkle] ...") is still first
    on the agent's own line.
    """
    before: list[str] = []
    after: list[str] = []
    core = spoken.strip()
    taken = True
    while taken:
        taken = False
        for sentence in framing:
            if core.startswith(sentence + " "):
                before.append(sentence)
                core = core[len(sentence):].lstrip()
                taken = True
            elif core.endswith(" " + sentence):
                after.insert(0, sentence)
                core = core[:-len(sentence)].rstrip()
                taken = True
    return " ".join(before), core, " ".join(after)


def plain(lyrics: str) -> str:
    """Lyrics without the syllable hyphens: "Hap-py day" -> "Happy day"."""
    return re.sub(r"(?<=[^\W\d_])-(?=[^\W\d_])", "", lyrics)


def split_tag(spoken: str, *, parody: bool = True) -> tuple[Tune | None, str, bool]:
    """(the tune, the text without any tag, whether there was a tag). The tune only
    when the tag is the very first thing and names a known tune (of `tunes(parody)`)."""
    match = TAG.match(spoken)
    name = re.sub(r"[\s_]+", "-", match.group(1).strip().lower()) if match else ""
    tune = tunes(parody).get(ALIASES.get(name, name)) if match else None
    rest, count = TAG.subn("", spoken)
    rest, odd = ODD_TAG.subn("", rest)
    rest, broken = OPEN_TAG.subn("", rest)
    broken += odd
    return tune, " ".join(rest.split()), count + broken > 0


def asked_to_sing(transcript: str) -> bool:
    return bool(ASKED.search(transcript or ""))


def choose(spoken: str, *, transcript: str, enabled: bool, read_back: bool,
           framing: tuple[str, ...] = (), parody: bool = True,
           ) -> tuple[str, SongRequest | None]:
    """What to say, and the song when this reply is sung.

    A read-back is returned exactly as built and never sung. A reply without a tag is
    returned unchanged. The tag must be first on the agent's own line: after the
    bridge's fixed sentences in `framing` (see peel), never after anything else. A
    tagged reply that is not sung loses its tags, and its lyrics their hyphens, and is
    spoken as words; the fixed sentences stay exactly as they are.
    """
    if read_back:
        return spoken, None
    before, core, after = peel(spoken, framing)
    tune, rest, tagged = split_tag(core, parody=parody)
    if not tagged:
        return spoken, None
    if tune is None or not enabled or not asked_to_sing(transcript) or not words(rest):
        return _join(before, plain(rest), after), None
    song = SongRequest(tune, rest, before, after)
    return song.text, song


# --- syllables and notes ----------------------------------------------------------------

_WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
_VOWELS = re.compile(r"[aeiouyàáâäèéêëìíîïòóôöùúûü]+", re.IGNORECASE)


def syllable_guess(word: str) -> int:
    """Vowel groups, a silent final "e" not counted: happy 2, code 1, leven 2."""
    lower = word.lower()
    count = len(_VOWELS.findall(lower))
    if count > 1 and lower.endswith("e") and not lower.endswith(("le", "ee")):
        count -= 1
    return max(1, count)


def words(lyrics: str) -> list[tuple[str, int]]:
    """(the word as Kokoro should say it, its number of syllables). Hyphens mark the
    syllables when the agent wrote them; otherwise they are guessed."""
    out = []
    for match in _WORD.finditer(lyrics):
        word = match.group(0)
        parts = [p for p in word.split("-") if p]
        count = len(parts) if len(parts) > 1 else syllable_guess(word)
        out.append(("".join(parts), count))
    return out


def assign(syllables: int, notes: int) -> list[list[tuple[int, float]]]:
    """Per syllable, the notes it is sung on as (note index, share of that note).

    As many syllables as notes: one each. Fewer syllables: a syllable holds several
    notes in a row. More syllables: a note is shared by several syllables, each with
    an equal part of it.
    """
    if syllables <= 0 or notes <= 0:
        return []
    if syllables <= notes:
        return [[(n, 1.0) for n in range(i * notes // syllables, (i + 1) * notes // syllables)]
                for i in range(syllables)]
    owner = [i * notes // syllables for i in range(syllables)]
    shared = [owner.count(n) for n in range(notes)]
    return [[(owner[i], 1.0 / shared[owner[i]])] for i in range(syllables)]


def midi_to_hz(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - 69.0) / 12.0)


def hz_to_midi(hz: float) -> float:
    return 69.0 + 12.0 * math.log2(hz / 440.0)


def tempo(tune: Tune, max_seconds: float = MAX_SECONDS) -> float:
    """The tune's tempo, faster when the tune would not fit in `max_seconds`."""
    return max(tune.bpm, tune.beats * 60.0 / max_seconds)


def note_seconds(beats: float, bpm: float) -> float:
    return beats * 60.0 / bpm


# How far above the voice's own speaking pitch the tune's middle note is sung.
KEY_LIFT = 2
DEFAULT_VOICE_HZ = 110.0


def tonic_midi(tune: Tune, voice_hz: float) -> int:
    """The tonic, so that the tune's middle note lies KEY_LIFT semitones above the
    voice's median speaking pitch: every voice sings in its own range."""
    steps = sorted(note.step for note in tune.notes)
    middle = (steps[0] + steps[-1]) / 2.0
    return int(round(hz_to_midi(voice_hz) + KEY_LIFT - middle))


# --- signal processing (numpy only) -----------------------------------------------------

F0_MIN, F0_MAX = 60.0, 500.0
_HOP_S = 0.01
_FRAME_S = 0.04


def f0_track(x, sr: int = SAMPLE_RATE):
    """Fundamental frequency per 10 ms frame, 0 where unvoiced (autocorrelation)."""
    import numpy as np

    x = np.asarray(x, dtype=np.float64)
    hop, flen = int(_HOP_S * sr), int(_FRAME_S * sr)
    frames = max(1, 1 + (len(x) - flen) // hop) if len(x) >= flen else 1
    padded = np.zeros(frames * hop + flen)
    padded[:len(x)] = x
    lag_min, lag_max = int(sr / F0_MAX), min(int(sr / F0_MIN), flen - 2)
    window = np.hanning(flen)
    rms = np.array([np.sqrt(np.mean(padded[i * hop:i * hop + flen] ** 2)) for i in range(frames)])
    floor = 0.08 * rms.max() if rms.size and rms.max() > 0 else 1.0
    f0 = np.zeros(frames)
    for i in range(frames):
        if rms[i] < floor or rms[i] <= 1e-6:
            continue
        seg = padded[i * hop:i * hop + flen]
        seg = (seg - seg.mean()) * window
        spectrum = np.fft.rfft(seg, 2 * flen)
        r = np.fft.irfft(np.abs(spectrum) ** 2)[:flen]
        if r[0] <= 0:
            continue
        r = r / r[0]
        region = r[lag_min:lag_max + 1]
        peak = float(region.max())
        if peak < 0.35:
            continue
        # The shortest lag close to the best one: no octave too low.
        lag = lag_min + int(np.argmax(region >= 0.9 * peak))
        while lag + 1 <= lag_max and r[lag + 1] > r[lag]:
            lag += 1
        if lag_min < lag < lag_max:
            a, b, c = r[lag - 1], r[lag], r[lag + 1]
            denom = a - 2 * b + c
            shift = 0.5 * (a - c) / denom if denom != 0 else 0.0
        else:
            shift = 0.0
        f0[i] = sr / (lag + shift)
    return f0


def median_f0(pieces, sr: int = SAMPLE_RATE) -> float:
    import numpy as np

    voiced = [f for x in pieces for f in f0_track(x, sr) if f > 0]
    return float(np.median(voiced)) if voiced else DEFAULT_VOICE_HZ


def trim(x, sr: int = SAMPLE_RATE, level: float = 0.03):
    """`x` without the near-silence before and after the word."""
    import numpy as np

    x = np.asarray(x, dtype=np.float32)
    if x.size == 0:
        return x
    hop = int(0.005 * sr)
    env = np.array([np.abs(x[i:i + hop]).max() for i in range(0, len(x), hop)])
    loud = np.nonzero(env >= level * env.max())[0] if env.max() > 0 else []
    if len(loud) == 0:
        return x[:0]
    return x[loud[0] * hop:min(len(x), (loud[-1] + 1) * hop)]


def split_syllables(x, count: int, sr: int = SAMPLE_RATE) -> list:
    """`x` cut into `count` pieces at the quietest point near each equal division."""
    import numpy as np

    x = np.asarray(x, dtype=np.float32)
    if count <= 1 or len(x) < count * 2:
        return [x] + [x[:0]] * (count - 1) if count > 1 else [x]
    hop = int(0.005 * sr)
    n = max(1, len(x) // hop)
    env = np.array([np.sqrt(np.mean(x[i * hop:(i + 1) * hop] ** 2)) for i in range(n)])
    env = np.convolve(env, np.ones(3) / 3, mode="same")
    cuts, last = [], 0
    for i in range(1, count):
        centre = i * n / count
        lo = max(last + 1, int(centre - n / (2 * count)))
        hi = min(n - (count - i), int(centre + n / (2 * count)) + 1)
        frame = lo + int(np.argmin(env[lo:hi])) if hi > lo else max(last + 1, int(centre))
        cuts.append(frame * hop)
        last = frame
    bounds = [0] + cuts + [len(x)]
    return [x[a:b] for a, b in zip(bounds, bounds[1:])]


def _marks(x, f0, sr: int):
    """Analysis marks: one per pitch period where voiced (on the period's peak), every
    5 ms where not. Returns (positions, periods, voiced)."""
    import numpy as np

    hop = int(_HOP_S * sr)
    unvoiced_step = int(0.005 * sr)
    positions, periods, voiced = [], [], []
    t = 0
    while t < len(x):
        f = f0[min(t // hop, len(f0) - 1)] if len(f0) else 0.0
        if f > 0:
            period = int(round(sr / f))
            lo = max(0, t - period // 4)
            if positions:
                lo = max(lo, positions[-1] + period // 2)
            hi = min(len(x), t + period // 4 + 1)
            if hi > lo:
                t = lo + int(np.argmax(x[lo:hi]))
            positions.append(t)
            periods.append(period)
            voiced.append(True)
            t += period
        else:
            positions.append(t)
            periods.append(unvoiced_step)
            voiced.append(False)
            t += unvoiced_step
    return np.array(positions), np.array(periods), np.array(voiced, dtype=bool)


def psola(x, target_hz: Callable[[float], float], length: int, sr: int = SAMPLE_RATE):
    """`x` resynthesised to `length` samples, voiced parts at `target_hz(t)` (t in
    seconds from the start of the output). The vowels are stretched; the consonants
    keep their own length while the output is long enough for them."""
    import numpy as np

    x = np.asarray(x, dtype=np.float64)
    out = np.zeros(max(0, length))
    if length <= 0 or x.size == 0:
        return out.astype(np.float32)
    f0 = f0_track(x, sr)
    positions, periods, voiced = _marks(x, f0, sr)
    spans = np.diff(np.append(positions, len(x))).astype(np.float64)
    unvoiced_total = spans[~voiced].sum()
    voiced_total = spans[voiced].sum()
    if voiced_total > 0 and length >= unvoiced_total:
        factor = np.where(voiced, (length - unvoiced_total) / voiced_total, 1.0)
    else:
        factor = np.full(len(spans), length / len(x))
    out_positions = np.concatenate(([0.0], np.cumsum(spans * factor)[:-1]))

    pad = int(periods.max()) + 1
    source = np.concatenate((np.zeros(pad), x, np.zeros(pad)))
    buffer = np.zeros(length + 2 * pad)
    windows: dict[int, np.ndarray] = {}
    t = 0.0
    while t < length:
        s = float(np.interp(t, out_positions, positions))
        k = int(np.searchsorted(positions, s))
        if k >= len(positions) or (k > 0 and s - positions[k - 1] < positions[k] - s):
            k -= 1
        period = int(periods[k])
        window = windows.get(period)
        if window is None:
            window = windows[period] = np.hanning(2 * period)
        centre = int(positions[k]) + pad
        grain = source[centre - period:centre + period] * window
        if voiced[k]:
            step = sr / max(F0_MIN / 2, target_hz(t / sr))
        else:
            step = float(period)
        gain = min(2.0, max(0.25, step / period))
        at = int(round(t)) + pad
        buffer[at - period:at + period] += grain * gain
        t += step
    out = buffer[pad:pad + length]
    return out.astype(np.float32)


def _fade(x, sr: int, seconds: float = 0.012):
    import numpy as np

    n = min(len(x) // 2, int(seconds * sr))
    if n > 0:
        ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
        x[:n] *= ramp
        x[-n:] *= ramp[::-1]
    return x


def _rms(x) -> float:
    import numpy as np

    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


# Vibrato: a slow wobble that starts after the note has settled.
VIBRATO_HZ = 5.5
VIBRATO_SEMITONES = 0.3
VIBRATO_DELAY_S = 0.18


def _contour(segments: list[tuple[float, float]]) -> Callable[[float], float]:
    """A pitch over time for one syllable: (hz, seconds) per note, with vibrato."""
    starts = []
    at = 0.0
    for hz, seconds in segments:
        starts.append(at)
        at += seconds

    def target(t: float) -> float:
        i = len(starts) - 1
        while i > 0 and t < starts[i]:
            i -= 1
        hz, seconds = segments[i]
        local = t - starts[i]
        depth = VIBRATO_SEMITONES * min(1.0, max(0.0, (local - VIBRATO_DELAY_S) / 0.2))
        return hz * 2.0 ** (depth / 12.0 * math.sin(2 * math.pi * VIBRATO_HZ * local))
    return target


# --- the song ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Scheduled:
    """One note as sung: where it starts, how long, at what pitch, on which syllable."""

    start_s: float
    seconds: float
    hz: float
    syllable: int


@dataclass
class Song:
    samples: object                 # float32 numpy array at SAMPLE_RATE
    schedule: list[Scheduled] = field(default_factory=list)
    tonic_midi: int = 0
    bpm: float = 0.0

    @property
    def seconds(self) -> float:
        return len(self.samples) / SAMPLE_RATE


# The organ under the voice: harmonics of each chord tone, and how loud it is against
# the voice (as a ratio of RMS).
ORGAN_HARMONICS = (1.0, 0.5, 0.25, 0.12)
ACCOMPANIMENT_LEVEL = 0.3


def accompaniment(chords: list[tuple[float, float, tuple[int, ...]]], tonic: int,
                  length: int, sr: int = SAMPLE_RATE):
    """A soft organ: (start, seconds, chord) segments, chord tones an octave below
    the tonic and the root two octaves below, with a short attack and release."""
    import numpy as np

    out = np.zeros(length, dtype=np.float64)
    for start, seconds, chord in chords:
        a = int(start * sr)
        n = min(length - a, int((seconds + 0.08) * sr))
        if n <= 0:
            continue
        t = np.arange(n) / sr
        tones = [tonic - 12 + step for step in chord] + [tonic - 24 + chord[0]]
        wave = np.zeros(n)
        for midi in tones:
            f = midi_to_hz(midi)
            for h, amp in enumerate(ORGAN_HARMONICS, start=1):
                if f * h < sr / 2:
                    wave += amp * np.sin(2 * np.pi * f * h * t)
        attack, release = int(0.04 * sr), int(0.12 * sr)
        env = np.ones(n)
        env[:min(attack, n)] = np.linspace(0, 1, min(attack, n))
        env[-min(release, n):] *= np.linspace(1, 0, min(release, n))
        out[a:a + n] += wave * env
    return out


def render(tune: Tune, lyrics: str, speak: Callable[[str], object], *,
           sr: int = SAMPLE_RATE, max_seconds: float = MAX_SECONDS,
           with_accompaniment: bool = True) -> Song:
    """Sing `lyrics` to `tune`. `speak(word)` returns the word as float samples at
    `sr` (Kokoro on the bridge, a sine in the tests)."""
    import numpy as np

    said = words(lyrics)
    if not said:
        return Song(np.zeros(0, dtype=np.float32))
    cache: dict[str, np.ndarray] = {}
    for word, _count in said:
        if word.lower() not in cache:
            cache[word.lower()] = trim(np.asarray(speak(word), dtype=np.float32), sr)
    syllable_audio = []
    for word, count in said:
        syllable_audio.extend(split_syllables(cache[word.lower()], count, sr))

    bpm = tempo(tune, max_seconds - 0.4)
    tonic = tonic_midi(tune, median_f0(cache.values(), sr))
    notes = tune.notes
    starts, at = [], 0.0
    for note in notes:
        starts.append(at)
        at += note_seconds(note.beats, bpm)
    total = min(max_seconds, at + 0.3)
    length = int(total * sr)

    voice = np.zeros(length, dtype=np.float64)
    schedule: list[Scheduled] = []
    level = max((_rms(a) for a in syllable_audio), default=0.0) or 0.1
    plan = assign(len(syllable_audio), len(notes))
    for index, (audio, parts) in enumerate(zip(syllable_audio, plan)):
        segments = []
        begin = None
        for note_index, share in parts:
            note = notes[note_index]
            seconds = note_seconds(note.beats, bpm) * share
            start = starts[note_index]
            if share < 1.0:
                # The note's own syllables in order: this one's part of it.
                before = sum(1 for p in plan[:index] if p[0][0] == note_index)
                start += before * seconds
            begin = start if begin is None else begin
            hz = midi_to_hz(tonic + note.step)
            segments.append((hz, seconds))
            schedule.append(Scheduled(start, seconds, hz, index))
        if begin is None or len(audio) == 0:
            continue
        span = int(sum(s for _hz, s in segments) * sr)
        sung = psola(audio, _contour(segments), span, sr)
        if _rms(sung) > 0:
            sung = sung * (level / _rms(sung))
        sung = _fade(sung.astype(np.float32), sr)
        a = int(begin * sr)
        n = min(len(sung), length - a)
        if n > 0:
            voice[a:a + n] += sung[:n]

    mix = voice
    if with_accompaniment:
        chords, current = [], None
        for note, start in zip(notes, starts):
            if note.chord is not None:
                current = note.chord
                chords.append([start, 0.0, current])
            if chords:
                chords[-1][1] = start + note_seconds(note.beats, bpm) - chords[-1][0]
        organ = accompaniment([tuple(c) for c in chords], tonic, length, sr)
        if _rms(organ) > 0:
            mix = voice + organ * (ACCOMPANIMENT_LEVEL * max(_rms(voice), 1e-4) / _rms(organ))
    peak = float(np.abs(mix).max()) if length else 0.0
    if peak > 0:
        mix = mix * (0.9 / peak)
    fade = min(length, int(0.25 * sr))
    if fade:
        mix[-fade:] *= np.linspace(1.0, 0.0, fade)
    return Song(mix.astype(np.float32), schedule, tonic, bpm)
