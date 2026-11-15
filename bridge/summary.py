"""Extracting the spoken summary from a Claude Code reply (D5, 1.4).

Claude Code is instructed, via an appended system prompt, to end every reply
with a line starting with SUMMARY_MARKER. Only that line is ever sent to
text-to-speech; the full reply is logged on the machine instead of spoken.
"""
from __future__ import annotations

import re

SUMMARY_MARKER = "SPOKEN SUMMARY:"

SYSTEM_PROMPT_ADDITION = (
    "After your normal reply, add one final line starting with exactly "
    f'"{SUMMARY_MARKER}" followed by a short, plain-language summary (one or '
    "two sentences, no code, no file paths, no markdown) suitable for being "
    "read aloud over the phone."
)

FALLBACK_SUMMARY = "Done. Check the log for details."


MAX_SPOKEN_CHARS = 300

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
        if (summary and len(summary) <= MAX_SPOKEN_CHARS
                and not _UNSPEAKABLE.search(summary) and not _SECRET_LIKE.search(summary)):
            return summary
        break
    # No marker, or a summary with code, paths or too long: a short, safe line
    # instead of reading it aloud. The full reply is in the log.
    return FALLBACK_SUMMARY
