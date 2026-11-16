"""The hash a confirmation is bound to (1.8, D36 "What exactly is confirmed").

One module, imported by both the gate and the wrapper. If the two ever
computed it separately they would drift, and a drift means a read-back that
releases another call.

- `Bash`: SHA-256 over the tool name, a zero byte, the `cwd`, a zero byte
  and the command string, byte for byte;
- any other tool: SHA-256 over the tool name, a zero byte, the `cwd`, a zero
  byte and the canonical JSON of its whole input (sorted keys, no
  insignificant whitespace). D36 leaves out a field `description` as
  cosmetic; that is true of `Bash`, where only the command is hashed
  anyway. For another tool a field of that name can be what is sent (the
  description of an issue, of an event), so it is part of the hash;
- a push: additionally a zero byte and what the push resolves to: the push
  address of its remote, and the source and destination of every ref.

`cwd` is the real path of the directory the call runs in: the same bytes do
different things in different directories, so a call with the right bytes
from another directory has another hash.
"""
from __future__ import annotations

import hashlib
import json

def canonical_input(tool_input: dict) -> str:
    return json.dumps(tool_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def call_text(tool_name: str, tool_input: dict) -> str:
    """The part of a call that is hashed, and that the confirming prompt
    tells the agent to run exactly."""
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            raise ValueError("a Bash call without a command string")
        return command
    return canonical_input(tool_input)


def call_hash(tool_name: str, cwd: str, tool_input: dict, push_address: str | None = None) -> str:
    digest = hashlib.sha256()
    digest.update(tool_name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(cwd.encode("utf-8", "surrogateescape"))
    digest.update(b"\0")
    digest.update(call_text(tool_name, tool_input).encode("utf-8", "surrogateescape"))
    if push_address is not None:
        digest.update(b"\0")
        digest.update(push_address.encode("utf-8", "surrogateescape"))
    return digest.hexdigest()
