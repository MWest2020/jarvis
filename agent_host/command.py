"""The claude -p command line for one turn, and what never reaches it (1.4, D34).

The turn settings file is written by the installer only (1c); this module
never writes one, and never writes anything into the state directory that
looks like settings. Two fallbacks, each behind its own configuration
switch (D34, task 1.17's open questions): the appended prompt inline instead
of from a file, and (handled by the caller, agent_host.sessions /
agent_host.wrapper) a daily session rotation instead of relying on
`--system-prompt-snapshot off` on a long resumed session.
"""
from __future__ import annotations

import json
from pathlib import Path

# Never on the command line (D34): the first three would lift the rules, the
# rest would make the environment differ from the coding session's.
FORBIDDEN_OPTIONS: tuple[str, ...] = (
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
    "--tools",
    "--allowedTools",
    "--restricted",
    "--setting-sources",
    "--strict-mcp-config",
)


def build_command(
    *,
    settings_path: Path,
    prompt_path: Path,
    session_id: str | None,
    append_prompt_inline: bool = False,
    prompt_text: str = "",
) -> list[str]:
    """argv for one headless turn, fixed by a test (D34):

        claude -p --output-format json --permission-mode auto --permission-prompts none
               --system-prompt-snapshot off --append-system-prompt-file <prompt>
               --settings <turn-settings> [--resume=<uuid>]

    `session_id`, if given, must already be validated as a canonical UUID
    (agent_host.sessions.is_canonical_uuid): it is passed as one token,
    `--resume=<uuid>`, never as two argv elements, because `--resume` takes
    an optional value and a stored id of `--dangerously-skip-permissions`
    would otherwise become an option of its own.
    """
    command = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        "auto",
        "--permission-prompts",
        "none",
        "--system-prompt-snapshot",
        "off",
    ]
    if append_prompt_inline:
        command += ["--append-system-prompt", prompt_text]
    else:
        command += ["--append-system-prompt-file", str(prompt_path)]
    command += ["--settings", str(settings_path)]
    if session_id:
        command.append(f"--resume={session_id}")
    return command


def settings_default_to_bypass(path: Path) -> bool:
    """True when the user's own settings default the permission mode to bypass.

    A turn must not start at all then (1.4): `--permission-mode auto` on the
    command line would still win, but a settings file that says
    bypassPermissions is a sign the rules this change relies on are not the
    ones in force, so the wrapper refuses rather than trust the override.
    """
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("defaultMode") == "bypassPermissions":
        return True
    permissions = data.get("permissions")
    if isinstance(permissions, dict) and permissions.get("defaultMode") == "bypassPermissions":
        return True
    return False
