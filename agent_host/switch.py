"""Switching voice confirmation on or off (1.7f, 1.11, D36 "Switching it off").

The logic behind `agent_host/confirm`, the root-owned program that is the
one exception to the class `self`: by voice, the setting is changed only
through the fixed command

    sudo /usr/local/lib/jarvis-agent/confirm on|off [class]

with its templated read-back ("Switch voice confirmation off for
everything. Say yes to go ahead.") and a yes. No other argument form
exists; any other edit of the configuration is a desk job.

It changes exactly two keys of the configuration file and nothing else:

- `confirm on` / `confirm off`: `confirm = "on"` / `"off"`. Switching it
  on for everything also empties `confirm_skip`;
- `confirm off <class>` / `confirm on <class>`: adds the class to, or
  removes it from, `confirm_skip`.

The file is TOML and the standard library cannot write TOML, so the two
keys are rewritten as lines: every existing top-level `confirm =` and
`confirm_skip =` line is removed and the new ones are put before the first
table header. The result must parse, and every other key must be unchanged,
or nothing is written. A `confirm_skip` spread over several lines is
refused: fix that at the desk.
"""
from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

from agent_host.config import CONFIRM_CLASSES, DEFAULT_CONFIG_PATH

USAGE = "usage: confirm on|off [class]"
_KEY_LINE = re.compile(r"\s*(confirm|confirm_skip)\s*=")
_TABLE_LINE = re.compile(r"\s*\[")


class SwitchError(Exception):
    """Nothing was changed."""


def parse_args(argv: list[str]) -> tuple[str, str | None]:
    if len(argv) not in (1, 2) or argv[0] not in ("on", "off"):
        raise SwitchError(USAGE)
    if len(argv) == 2 and argv[1] not in CONFIRM_CLASSES:
        raise SwitchError(f"{USAGE}; class is one of {', '.join(sorted(CONFIRM_CLASSES))}")
    return argv[0], argv[1] if len(argv) == 2 else None


def new_values(data: dict, state: str, cls: str | None) -> tuple[str, list[str]]:
    confirm = data.get("confirm", "on")
    skip = data.get("confirm_skip", [])
    if confirm not in ("on", "off") or not isinstance(skip, list) \
            or not all(isinstance(item, str) for item in skip):
        raise SwitchError("the configuration's confirm settings are not what they should be")
    skip = [item for item in skip if item in CONFIRM_CLASSES]
    if cls is None:
        return state, [] if state == "on" else skip
    if state == "off" and cls not in skip:
        skip.append(cls)
    if state == "on":
        skip = [item for item in skip if item != cls]
    return confirm, sorted(skip)


def rewrite(text: str, state: str, cls: str | None) -> str:
    try:
        before = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SwitchError(f"the configuration is not valid TOML: {exc}") from exc
    confirm, skip = new_values(before, state, cls)

    lines = text.splitlines()
    kept: list[str] = []
    in_top_level = True
    for line in lines:
        if _TABLE_LINE.match(line):
            in_top_level = False
        if in_top_level and _KEY_LINE.match(line):
            continue
        kept.append(line)
    insert_at = next((i for i, line in enumerate(kept) if _TABLE_LINE.match(line)), len(kept))
    new_lines = [
        f'confirm = "{confirm}"',
        "confirm_skip = [" + ", ".join(f'"{item}"' for item in skip) + "]",
    ]
    result = "\n".join(kept[:insert_at] + new_lines + kept[insert_at:]) + "\n"

    try:
        after = tomllib.loads(result)
    except tomllib.TOMLDecodeError as exc:
        raise SwitchError(
            "the confirm settings are written in a form this program cannot rewrite"
            f" (fix it at the desk): {exc}") from exc
    expected = dict(before, confirm=confirm, confirm_skip=skip)
    if after != expected:
        raise SwitchError("rewriting would have changed more than the confirm settings")
    return result


def switch(argv: list[str], config_path: Path = DEFAULT_CONFIG_PATH) -> str:
    """Apply `on|off [class]` to the configuration file; returns what was done."""
    state, cls = parse_args(argv)
    try:
        text = config_path.read_text()
    except OSError as exc:
        raise SwitchError(f"cannot read {config_path}: {exc}") from exc
    result = rewrite(text, state, cls)
    tmp = config_path.with_name(config_path.name + ".confirm-tmp")
    try:
        mode = config_path.stat().st_mode & 0o777
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w") as fh:
            fh.write(result)
        os.chmod(tmp, mode)
        os.replace(tmp, config_path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise SwitchError(f"cannot write {config_path}: {exc}") from exc
    return f"voice confirmation {state} for {cls + ' actions' if cls else 'everything'}"
