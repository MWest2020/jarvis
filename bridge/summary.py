"""Extracting the spoken summary from a Claude Code reply (D5, 1.4).

Claude Code is instructed, via an appended system prompt, to end every reply
with a line starting with SUMMARY_MARKER. Only that line is ever sent to
text-to-speech; the full reply is logged on the machine instead of spoken.
"""
from __future__ import annotations

SUMMARY_MARKER = "SPOKEN SUMMARY:"

SYSTEM_PROMPT_ADDITION = (
    "After your normal reply, add one final line starting with exactly "
    f'"{SUMMARY_MARKER}" followed by a short, plain-language summary (one or '
    "two sentences, no code, no file paths, no markdown) suitable for being "
    "read aloud over the phone."
)

FALLBACK_SUMMARY = "Done. Check the log for details."


def extract_summary(reply_text: str) -> str:
    for line in reversed(reply_text.splitlines()):
        line = line.strip()
        if line.startswith(SUMMARY_MARKER):
            summary = line[len(SUMMARY_MARKER):].strip()
            if summary:
                return summary
    # Claude did not add the marker - fall back to a short, safe line rather
    # than reading code or paths aloud (Requirement: Spoken output).
    return FALLBACK_SUMMARY
