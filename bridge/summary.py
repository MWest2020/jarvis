"""Extracting the spoken summary from a Claude Code reply (D5, 1.4).

Claude Code is instructed, via an appended system prompt, to end every reply
with a line starting with SUMMARY_MARKER. Only that line is ever sent to
text-to-speech; the full reply is logged on the machine instead of spoken.
"""
from __future__ import annotations

import re

SUMMARY_MARKER = "SPOKEN SUMMARY:"


# jarvis-sing, the easter egg: the tunes the bridge can sing (bridge/singing.py owns
# the melodies; a test keeps the two in step) and the agent's part of it. The same
# words are in agent_host/prompt.md for `full` mode.
SING_TUNES = (
    ("happy-birthday", (6, 6, 7, 6)),
    ("twinkle", (7, 7)),
    ("frere-jacques", (4, 4, 3, 3, 6, 6, 3, 3)),
    ("mountain-king", (7, 6, 8, 5)),
    ("iron-man", (5, 6, 4)),
    ("ode-to-joy", (8, 7, 8, 7)),
)
SING_INSTRUCTION = (
    "Only when the user explicitly asks you to sing, start the text after the marker "
    "with a tag naming one of these tunes, then short lyrics in your own words, in "
    "English, one syllable per note, the syllables of a word joined by hyphens "
    "(hap-py): "
    + "; ".join(f"[sing: {name}] {sum(lines)} syllables ({', '.join(map(str, lines))})"
                for name, lines in SING_TUNES)
    + ". iron-man is a parody of the riff only: write your own funny lines about "
    "being Jarvis, the voice assistant in the car, never the song's own words or title "
    "line. Asked for any other song still under copyright, say without the tag that "
    "you cannot sing that one and offer mountain-king or iron-man. "
    "A sung line may be up to 220 characters. Never use the tag in any other reply."
)

# jarvis-beatbox, the second easter egg: the beats the bridge can make (bridge/beatbox.py
# owns them; a test keeps the two in step). The same words are in agent_host/prompt.md.
BEATBOX_PATTERNS = ("boots-and-cats", "hip-hop", "jarvis-hook")
BEATBOX_INSTRUCTION = (
    " Only when the user explicitly asks you to beatbox or for a beat, start the text "
    "after the marker with [beatbox] (the bridge picks a beat) or a tag naming one: "
    + ", ".join(f"[beatbox: {name}]" for name in BEATBOX_PATTERNS)
    + "; then one short line of your own in English, at most 60 characters, said before "
    "the beat. The bridge makes the sounds: never write them yourself. "
    "Never use the tag in any other reply."
)

SYSTEM_PROMPT_ADDITION = (
    "After your normal reply, add one final line starting with exactly "
    f'"{SUMMARY_MARKER}" followed by a short, plain-language summary suitable '
    "for being read aloud over the phone: one sentence, two at most, about "
    "100 to 150 characters, no code, no file paths, no markdown. "
    + SING_INSTRUCTION
    + BEATBOX_INSTRUCTION
)

FALLBACK_SUMMARY = "Done. Check the log for details."


# Speech is the slow part of a turn: Kokoro renders at about real time, some
# 0.055 s per character (measured 2026-11-15), so 160 characters is under 9 s.
MAX_SPOKEN_CHARS = 160
# A sung line (it starts with a [sing: …] tag) may be longer: its lyrics are not read
# at speaking speed, and the song itself is capped at 20 seconds (bridge/singing.py).
MAX_SUNG_CHARS = 240
_SUNG = re.compile(r"\[\s*sing\s*:", re.IGNORECASE)

# Anything that looks like code or a path is never spoken (Requirement: Spoken
# output), whatever the model wrote after the marker.
_MARKER = re.compile(r"^[\s*#_>`-]*SPOKEN SUMMARY:[*_`]*\s*(.*)$", re.IGNORECASE)
_UNSPEAKABLE = re.compile(
    r"(```|`|(^|\s)[~.]?/[\w.-]+/|[\w-]+/[\w./-]+|\\[\w]|"
    r"\b[\w-]+\.(py|js|ts|tsx|json|ya?ml|toml|md|sh|go|rs|java|c|h|cpp|rb|php|html|css|sql|lock)\b|"
    r"[{}<>$|;]|&&|=>|\(\))"
)
# Never speak anything shaped like a credential: an Anthropic key, or any long
# unbroken run of token characters (hex tokens, base64 secrets, session ids).
_SECRET_LIKE = re.compile(r"sk-ant-|[A-Za-z0-9_-]{32,}", re.IGNORECASE)


def _clean(text: str) -> str:
    text = re.sub(r"[*_#>]+", "", text)
    return " ".join(text.split())


def last_marker_line(reply_text: str) -> str:
    """The last line of `reply_text` that starts with the marker, verbatim.

    Used by the agent host (D33's `spoken` field): it sends this one line
    back to the bridge, unprocessed, so that running it through
    extract_summary gives the same result as running the full reply
    through extract_summary would.
    """
    lines = reply_text.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if _MARKER.match(lines[i]):
            return lines[i]
    return ""


def extract_summary(reply_text: str) -> str:
    lines = reply_text.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        m = _MARKER.match(lines[i])
        if not m:
            continue
        summary = _clean(m.group(1))
        if not summary:
            # The marker on its own line, the summary on the next one.
            rest = [ln for ln in lines[i + 1:] if ln.strip()]
            summary = _clean(rest[0]) if rest else ""
        # The filters see the whole summary, also the part a cut would drop.
        if summary and not _UNSPEAKABLE.search(summary) and not _SECRET_LIKE.search(summary):
            return shorten(summary, MAX_SUNG_CHARS if _SUNG.match(summary) else MAX_SPOKEN_CHARS)
        break
    # No marker, or a summary with code, paths or a credential: a short, safe
    # line instead of reading it aloud. The full reply is in the log.
    return FALLBACK_SUMMARY


_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def shorten(summary: str, limit: int = MAX_SPOKEN_CHARS) -> str:
    """`summary` within `limit` characters: as it is when it fits, otherwise up to
    the last sentence end within the limit, otherwise up to the last whole word
    with "…" after it."""
    if len(summary) <= limit:
        return summary
    ends = [m.end() for m in _SENTENCE_END.finditer(summary) if m.end() <= limit]
    if ends:
        return summary[:ends[-1]]
    head = summary[:limit - 1]
    if not summary[limit - 1].isspace() and " " in head:
        head = head[:head.rindex(" ")]
    return head.rstrip(" ,;:-") + "…"
