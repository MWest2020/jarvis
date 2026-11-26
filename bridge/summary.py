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

# Said only when neither the summary line nor the reply holds anything speakable. Mark is
# in the car with a phone, not at a screen: "and he says check the log for details, but
# cant show them" (26 Nov 2026). So it never points at a log.
FALLBACK_SUMMARY = "Done, but I couldn't put the answer into words. Ask me to say it again."


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
    r"[{}<>$|]|&&|=>|\(\))"
)
# A semicolon is speech, not code (it was in the set above until 26 Nov 2026, and 29 of
# 165 recorded turns, "...nothing failing; the only blips were...", were replaced by the
# fallback for it).
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


def _speakable_ok(text: str) -> bool:
    return not _UNSPEAKABLE.search(text) and not _SECRET_LIKE.search(text)


def extract_summary(reply_text: str) -> str:
    """What is spoken for `reply_text`: its summary line when that is speakable as it is;
    otherwise the summary line made speakable (speakable()); otherwise the reply itself
    made speakable, its first sentences; and only when nothing speakable is left,
    FALLBACK_SUMMARY."""
    lines = reply_text.splitlines()
    candidates = []
    body = lines
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
        if summary and _speakable_ok(summary):
            return shorten(summary, MAX_SUNG_CHARS if _SUNG.match(summary) else MAX_SPOKEN_CHARS)
        candidates.append(summary)
        body = lines[:i]
        break
    # No marker, or a summary with code, paths or a credential: what can be said of it,
    # else of the reply (in `full` mode the bridge has only the summary line; the agent
    # host makes the reply's own line, spoken_line()).
    candidates.append("\n".join(body))
    for text in candidates:
        said = speakable(text)
        if said:
            return shorten(said)
    return FALLBACK_SUMMARY


# --- a reply made speakable (no summary line, or one with code in it) -------------------

_FILE_EXT = (r"py|js|ts|tsx|json|ya?ml|toml|md|sh|go|rs|java|c|h|cpp|rb|php|html|css|sql|"
             r"lock|txt|cfg|ini|env|kt|swift|xml|csv|log")
_FENCE = re.compile(r"^\s*(```|~~~).*?(^\s*\1[^\n]*$|\Z)", re.MULTILINE | re.DOTALL)
_DROP_LINE = re.compile(r"^\s*(\||[-*_=]{3,}\s*$|SESSION:\s*close\b)", re.IGNORECASE)
_LINE_MARKUP = re.compile(r"^\s*(?:#{1,6}\s+|>+\s*|[-*+•]\s+|\d{1,3}[.)]\s+|\[[ xX]\]\s+)+")
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\([^)\n]*\)")
_URL = re.compile(r"\b(?:https?://|www\.)[^\s<>()\]]+[^\s<>()\].,;:!?'\"]", re.IGNORECASE)
_INLINE_CODE = re.compile(r"`+([^`\n]*)`+")
_SIMPLE_WORD = re.compile(r"[A-Za-z][A-Za-z0-9.-]{0,30}")
# A path: a slash between names, or one before a name ("/etc/hosts", "~/x", "./run").
_PATH = re.compile(r"(?<![\w/])(?:~|\.{1,2})?/?(?:[\w.-]+/)+[\w.-]*")
_FILE = re.compile(rf"\b([\w-]+)\.(?:{_FILE_EXT})\b")
# Never in a spoken sentence: a sentence with what is left of code is left out whole.
_CODE_LEFT = "\x00"
_SENTENCE = re.compile(r"(?<=[.!?…])\s+")


def _path_words(match: re.Match) -> str:
    """A path as words: a file's or a deep path's last name without its extension
    ("bridge/server.py" -> "server"); "owner/repo" or "and/or" with a space."""
    path = match.group(0)
    parts = [p for p in path.split("/") if p not in ("", "~", ".", "..")]
    if not parts:
        return " "
    last = parts[-1]
    if path.startswith(("/", "~", ".")) or len(parts) > 2 or _FILE.fullmatch(last):
        return _FILE.sub(r"\1", last)
    return " ".join(parts)


def _inline_code(match: re.Match) -> str:
    code = match.group(1).strip()
    if _SIMPLE_WORD.fullmatch(code) and not _FILE.fullmatch(code):
        return code
    if _FILE.fullmatch(code):
        return _FILE.sub(r"\1", code)
    return _CODE_LEFT


def _speakable_line(line: str) -> str:
    line = _LINE_MARKUP.sub("", line)
    line = _MD_LINK.sub(r"\1", line)
    line = _INLINE_CODE.sub(_inline_code, line)
    line = _URL.sub("the link", line)
    line = _PATH.sub(_path_words, line)
    line = _FILE.sub(r"\1", line)
    line = _clean(line)
    line = re.sub(r"\(\s*\)", "", line)
    line = re.sub(r"\s+([.,;:!?])", r"\1", line)
    line = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", line).strip(" ,;")
    if line and line[-1] in ":,;-":
        line = line[:-1].rstrip() + "."
    elif line and line[-1] not in ".!?…":
        line += "."
    return line


def speakable(text: str) -> str:
    """`text` (a reply, or a summary line with code in it) as words that can be read
    aloud: no code blocks, tables or list markup, no markdown, links as their text, a URL
    as "the link", a path or file as its name. A sentence that still holds code, a path
    or anything shaped like a credential is left out whole. "" when nothing is left.
    Not shortened: extract_summary does that."""
    text = _FENCE.sub("\n", text or "")
    lines = []
    for line in text.splitlines():
        if not line.strip() or _DROP_LINE.match(line) or _MARKER.match(line):
            continue
        line = _speakable_line(line)
        if line:
            lines.append(line)
    kept = [sentence for sentence in _SENTENCE.split(" ".join(lines))
            if sentence and _CODE_LEFT not in sentence and _speakable_ok(sentence)
            and re.search(r"[A-Za-z]{2}", sentence)]
    return " ".join(kept)


def spoken_line(reply_text: str) -> str:
    """The `spoken` field the agent host sends (jarvis-full-agent D33): the reply's
    summary line verbatim, as before, when the bridge makes the same of it as of the
    whole reply. When the reply has no summary line, or one that extract_summary has to
    replace, a line with the marker and what extract_summary makes of the whole reply
    (at most MAX_SPOKEN_CHARS, through the same filters), so the driver hears the
    answer instead of the fallback. "" or the line itself when nothing is speakable."""
    line = last_marker_line(reply_text)
    spoken = extract_summary(reply_text)
    if spoken == FALLBACK_SUMMARY or extract_summary(line) == spoken:
        return line
    return f"{SUMMARY_MARKER} {spoken}"


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
    # A semicolon ends a clause: "..., and nothing failing; the only blips were ...".
    clauses = [m.start() for m in re.finditer(r";(?=\s)", summary) if m.start() < limit]
    if clauses and clauses[-1] > limit // 3:
        return summary[:clauses[-1]].rstrip() + "."
    head = summary[:limit - 1]
    if not summary[limit - 1].isspace() and " " in head:
        head = head[:head.rindex(" ")]
    return head.rstrip(" ,;:-") + "…"
