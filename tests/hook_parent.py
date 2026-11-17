"""Stands in for the `claude` process the wrapper starts, for the tests that
run a hook script as a process (agent_host/gate, agent_host/action-log).

The environment it was started with is the turn's: the exec-time
environment the gate checks the record against (agent_host.trusted). It
writes the trusted record for itself, as the wrapper does between fork and
exec (unless the gate directory is "-"), and runs the hook the way Claude
Code 2.1.322 does: as `sh -c <command>`, with its own environment plus
what project `env` settings would add (the overrides; a null value removes
a name). The hook's stdin, stdout and stderr are this process's; its exit
code is this process's.

argv: <gate dir or "-"> <overrides as JSON> <hook argv ...>
"""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_host import trusted  # noqa: E402


def main() -> int:
    gate_dir, overrides, hook = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3:]
    if gate_dir != "-":
        trusted.write_for_self(Path(gate_dir), dict(os.environ))
    env = dict(os.environ)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    try:
        return subprocess.run(["/bin/sh", "-c", shlex.join(hook)], env=env).returncode
    finally:
        if gate_dir != "-":
            trusted.remove_for(Path(gate_dir), os.getpid())


if __name__ == "__main__":
    sys.exit(main())
