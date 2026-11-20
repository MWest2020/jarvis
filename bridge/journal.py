"""The drive journal and the drive's own words (add-drive-mode phase 1: D10, D12, D18, D19).

Every use by voice is part of a *drive*: a drive starts with the first event after a
quiet gap (30 minutes by default) and ends when the next one starts. The journal keeps,
per drive, the turns (time, project, transcript, spoken reply, turn id, session id),
the notes, and the recaps that were asked for, as one JSON line per event in one file
per drive on the bridge's state volume. Files are owner-only and each line is synced
before the reply goes out, so a restart keeps everything up to the last completed event.

Two control words are matched here, from the driver's own transcript only (D12): "where
was I" (the recap, assembled from the journal, no agent turn, D19) and "note ..." (the
text into the journal, answered "Noted."; never sent to the agent). Nothing the agent
wrote is ever passed to `match`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from bridge.config import normalise_utterance

logger = logging.getLogger("bridge.journal")

RECAP = "recap"
NOTE = "note"

NOTED = "Noted."
NOTE_EMPTY = "What should I note? Say note, then the note."
NOTE_FAILED = "I could not save that note."
NOTHING_TO_RECAP = "There is nothing to recap yet."
RECAP_FAILED = "I could not read the journal."
# Said after a control word that used up a read-back (D36: the next utterance of its
# project answers it, and only a yes releases it).
READ_BACK_DROPPED = "What I read back was not done."

# Caps on what is kept and what the recap speaks.
MAX_NOTE_CHARS = 1000
MAX_FIELD_CHARS = 2000
RECAP_QUESTION_WORDS = 25
RECAP_ANSWER_WORDS = 40

_SUFFIX = ".jsonl"


@dataclass(frozen=True)
class Word:
    """A control word the driver said: `kind` is RECAP or NOTE; `text` is the note."""

    kind: str
    text: str = ""


def match(transcript: str, *, recap_words: Iterable[str],
          note_words: Iterable[str]) -> Word | None:
    """The control word in the driver's transcript, or None for an ordinary utterance.

    "Where was I" counts only as the whole utterance (normalised for case and
    punctuation), so "where was I with the config" goes to the agent. A note word counts
    as the first words of the utterance; the rest, as transcribed, is the note. An
    utterance that ends in a question mark is a question, not a note ("notitie over de
    release, wat staat erin?" goes to the agent).
    """
    said = normalise_utterance(transcript)
    if not said:
        return None
    if said in set(recap_words):
        return Word(RECAP)
    # Each transcribed word with its normalised form; pure punctuation drops out.
    tokens = [(raw, normalise_utterance(raw)) for raw in transcript.split()]
    tokens = [(raw, norm) for raw, norm in tokens if norm]
    for phrase in sorted(set(note_words), key=lambda p: -len(p.split())):
        words = phrase.split()
        head = " ".join(norm for _, norm in tokens[:len(words)])
        if head != phrase:
            continue
        rest = " ".join(raw for raw, _ in tokens[len(words):])
        if transcript.rstrip().endswith("?"):
            return None
        return Word(NOTE, rest.lstrip(":,;.-–— ").strip()[:MAX_NOTE_CHARS])
    return None


_SENTENCE_END = re.compile(r"(?<=[.?!])\s+")


def _cut(text: str, words: int) -> str:
    """The first sentence of `text`, at most `words` words: the recap stays at three
    short sentences (D19) whatever the question or the reply held."""
    text = _SENTENCE_END.split(" ".join(text.split()), maxsplit=1)[0]
    parts = text.split(" ")
    if len(parts) <= words:
        return " ".join(parts)
    return " ".join(parts[:words]) + " ..."


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "?", "!", "...")) else text + "."


class Journal:
    """The journal on the bridge's state volume, one JSON-lines file per drive.

    `clock` gives wall-clock seconds (time.time); tests pass their own. `redact` is
    applied to every text before it is written: the same redaction as the bridge log
    (the token, the OAuth token, sk-ant- keys).
    """

    def __init__(self, directory: Path, *, gap_s: float, retention_s: float,
                 clock: Callable[[], float] = time.time,
                 redact: Callable[[str], str] = lambda text: text) -> None:
        self._dir = directory
        self._redact = redact
        self._gap_s = gap_s
        self._retention_s = retention_s
        self._clock = clock
        self._lock = threading.Lock()
        self._drive: str | None = None
        self._last_t = 0.0
        with self._lock:
            self._prune()
            self._resume()

    # -- writing --------------------------------------------------------------

    def record_turn(self, *, project: str, transcript: str, spoken: str, turn_id: str = "",
                    session_id: str = "", read_back: bool = False,
                    answer: bool = False) -> None:
        """One agent turn. `answer`: the utterance was the yes or no to a read-back."""
        self._append({"kind": "turn", "project": project,
                      "transcript": self._redact(transcript)[:MAX_FIELD_CHARS],
                      "spoken": self._redact(spoken)[:MAX_FIELD_CHARS], "turn_id": turn_id,
                      "session_id": session_id, "read_back": read_back, "answer": answer})

    def add_note(self, *, project: str, text: str) -> None:
        self._append({"kind": "note", "project": project,
                      "text": self._redact(text)[:MAX_NOTE_CHARS]})

    def record_recap(self, *, project: str) -> None:
        self._append({"kind": "recap", "project": project})

    def _append(self, event: dict) -> None:
        with self._lock:
            now = self._clock()
            if self._drive is None or now - self._last_t >= self._gap_s:
                self._drive = self._new_drive_id(now)
                self._prune()
            data = (json.dumps({"t": round(now, 3), **event}, ensure_ascii=False)
                    + "\n").encode("utf-8")
            self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(self._dir / (self._drive + _SUFFIX),
                         os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                size = os.fstat(fd).st_size
                if size and os.pread(fd, 1, size - 1) != b"\n":
                    # A line cut off by a crash: end it, so this event is a line of
                    # its own and not glued onto the broken one.
                    data = b"\n" + data
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
            self._last_t = now

    def _new_drive_id(self, now: float) -> str:
        base = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
        drive, n = base, 1
        while (self._dir / (drive + _SUFFIX)).exists():
            n += 1
            drive = f"{base}-{n}"
        return drive

    # -- reading --------------------------------------------------------------

    def drives(self) -> list[str]:
        """Drive ids, oldest first."""
        try:
            names = [p.name for p in self._dir.iterdir()
                     if p.name.endswith(_SUFFIX) and p.is_file()]
        except FileNotFoundError:
            return []
        return sorted(n[:-len(_SUFFIX)] for n in names)

    def events(self, drive: str) -> list[dict]:
        """A drive's events; a line that was cut off by a crash is skipped, also one
        cut in the middle of a multi-byte character."""
        try:
            raw = (self._dir / (drive + _SUFFIX)).read_bytes()
        except FileNotFoundError:
            return []
        events = []
        for line in raw.split(b"\n"):
            try:
                event = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        return events

    def current_drive(self) -> str | None:
        """The open drive, or None when none is open (none yet, or the gap has passed)."""
        with self._lock:
            if self._drive is None or self._clock() - self._last_t >= self._gap_s:
                return None
            return self._drive

    def recap(self, *, running: Iterable[str] = ()) -> str:
        """"Where was I" (D19): at most three sentences, from the journal only. The
        project with the last question and its spoken answer, from this drive or, when
        it has no turn yet, the last drive that has one; then the turns still running.
        No agent turn: this works when the agent host is down and invents nothing."""
        current = self.current_drive()
        cutoff = self._clock() - self._retention_s
        sentences: list[str] = []
        for drive in reversed(self.drives()):
            events = self.events(drive)
            turns = [e for e in events if e.get("kind") == "turn"]
            if not turns or _last_time(events) < cutoff:
                # Nothing to say, or past the retention and not pruned yet.
                continue
            last = turns[-1]
            asked = next((t for t in reversed(turns) if not t.get("answer")), last)
            when = "Last" if drive == current else "Last drive"
            question = _cut(str(asked.get("transcript", "")), RECAP_QUESTION_WORDS)
            answer = _cut(str(last.get("spoken", "")), RECAP_ANSWER_WORDS)
            sentences.append(_sentence(f"{when}, on {asked.get('project', '')}, "
                                       f"you asked: {question}"))
            if answer:
                sentences.append(_sentence(f"I said: {answer}"))
            break
        busy = sorted(set(running))
        if len(busy) == 1:
            sentences.append(f"A turn on {busy[0]} is still running.")
        elif busy:
            sentences.append(f"Turns on {', '.join(busy[:-1])} and {busy[-1]} are still running.")
        return " ".join(sentences) if sentences else NOTHING_TO_RECAP

    # -- housekeeping ---------------------------------------------------------

    def _resume(self) -> None:
        """After a restart: the newest drive stays open if its last event is recent."""
        drives = self.drives()
        if not drives:
            return
        last = _last_time(self.events(drives[-1]))
        if last > 0:
            self._drive, self._last_t = drives[-1], last

    def _prune(self) -> None:
        """Drives whose last event is older than the retention are deleted."""
        cutoff = self._clock() - self._retention_s
        try:
            paths = list(self._dir.iterdir())
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning("journal: could not list %s for pruning: %s", self._dir, exc)
            return
        for path in paths:
            drive = path.name[:-len(_SUFFIX)]
            if not path.name.endswith(_SUFFIX) or drive == self._drive:
                continue
            try:
                last = _last_time(self.events(drive)) or path.stat().st_mtime
                if last < cutoff:
                    path.unlink()
            except OSError as exc:
                logger.warning("journal: could not prune %s: %s", path.name, exc)


def _last_time(events: list[dict]) -> float:
    """The time of a drive's last event; 0 when none has one."""
    times = [e["t"] for e in events
             if isinstance(e.get("t"), (int, float)) and not isinstance(e.get("t"), bool)]
    return float(max(times)) if times else 0.0
