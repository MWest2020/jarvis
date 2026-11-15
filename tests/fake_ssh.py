#!/usr/bin/env python3
"""A fake `ssh` for the tests of the remote adapter, with a fake wrapper behind it.

Copied into a temporary directory as `ssh` (tests/agent_host_fake.py). It never
opens a connection. Like the real one with `-E <file>`, it writes its own messages
to that file and only the remote command's output to stdout and stderr (the lines
are OpenSSH 9.6's, recorded against a real sshd). It appends what it was called with to `calls.jsonl` next to
itself, and behaves as `scenario.json` next to itself says:

  {"mode": "wrapper"}          the forced command answered: the fake wrapper below
  {"mode": "refused" | "unresolved" | "denied" | "banner"}
                               OpenSSH's own message for a connection that was
                               never made, exit 255
  {"mode": "host_key"}         the pinned host key did not match, exit 255
  {"mode": "drop"}             the request was read (a turn started), then the
                               connection broke, exit 255
  {"mode": "raw", "stdout": …, "stderr": …, "log": …, "exit": …}
                               `stderr` is what the remote command printed, `log`
                               what ssh itself wrote; without `log`, ssh says it
                               authenticated
  {"mode": "hang"}             never answers

The fake wrapper speaks version 1 of the contract (design D33, D36) and keeps one
pending call per project in `state.json`: 60 seconds, single use, cleared by the
next request for that project whatever it is. Standard library only.
"""
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_BYTES = 64 * 1024
REQUEST_KEYS = {"v", "project", "prompt", "confirm"}
CONFIRM_KEYS = {"id", "sha256", "answer"}
SHA = "ab" * 32


def _load(name, default):
    try:
        return json.loads((HERE / name).read_text())
    except FileNotFoundError:
        return default


def _save(name, data):
    (HERE / name).write_text(json.dumps(data))


def _log(entry):
    with (HERE / "calls.jsonl").open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


def _reply(**fields):
    """Version 1: always exactly these nine fields."""
    reply = {"v": 1, "status": "ok", "error": None, "turn": None, "spoken": "",
             "pending": False, "readback": None, "confirm": None, "notice": None}
    reply.update(fields)
    sys.stdout.write(json.dumps(reply))
    return 0


def _error(code, **extra):
    return _reply(status="error", error=code, **extra)


def wrapper(raw: bytes, scenario: dict) -> int:
    state = _load("state.json", {"pending": {}, "turns": [], "n": 0})
    try:
        if len(raw) > MAX_BYTES:
            raise ValueError("too large")
        request = json.loads(raw.decode("utf-8"))
        if not isinstance(request, dict) or set(request) != REQUEST_KEYS or request["v"] != 1:
            raise ValueError("shape")
        confirm = request["confirm"]
        if confirm is not None and (set(confirm) != CONFIRM_KEYS
                                    or confirm["answer"] not in ("yes", "no")):
            raise ValueError("confirm")
        if confirm is not None and request["prompt"] != "":
            raise ValueError("a confirm with a prompt")
    except (ValueError, KeyError, TypeError):
        return _error("bad_request")
    if scenario.get("error"):
        return _error(scenario["error"], **scenario.get("error_extra", {}))

    project = request["project"]
    state["n"] += 1
    turn_id = f"turn-{state['n']}"
    # Cleared by the next request for the project, whatever that request is.
    pending = state["pending"].pop(project, None)
    try:
        if confirm is not None:
            alive = (pending is not None and pending["id"] == confirm["id"]
                     and pending["sha256"] == confirm["sha256"]
                     and time.time() - pending["at"] < 60
                     and not scenario.get("host_expired"))
            if confirm["answer"] == "no":
                state["turns"].append({"project": project, "kind": "cancel"})
                return _reply(spoken="")
            if not alive:
                return _error("expired")
            state["turns"].append({"project": project, "kind": "confirmed",
                                   "sha256": confirm["sha256"]})
            return _reply(spoken="SPOKEN SUMMARY: Merged.", turn=turn_id)

        state["turns"].append({"project": project, "kind": "agent",
                               "prompt": request["prompt"]})
        if scenario.get("gate"):
            ident = f"c{state['n']}"
            state["pending"][project] = {"id": ident, "sha256": SHA, "at": time.time()}
            return _reply(
                turn=turn_id, pending=True,
                readback=scenario.get(
                    "readback", "Merge pull request 21 on jarvis. Checks green, one "
                                "approving review. Say yes to go ahead."),
                confirm={"id": ident, "sha256": SHA, "class": "merge",
                         "expires_s": scenario.get("expires_s", 60)})
        return _reply(turn=turn_id, notice=scenario.get("notice"),
                      spoken=scenario.get("spoken", "SPOKEN SUMMARY: Done on the agent host."))
    finally:
        _save("state.json", state)


AUTHENTICATED = 'Authenticated to agent.example ([100.64.0.9]:22) using "publickey".\n'
OWN_MESSAGES = {
    "refused": "ssh: connect to host agent.example port 22: Connection refused\n",
    "unresolved": "ssh: Could not resolve hostname agent.example: Name or service not known\n",
    "denied": "jarvis@agent.example: Permission denied (publickey).\n",
    "banner": "Connection timed out during banner exchange\n",
    "host_key": "Host key for agent.example has changed and you have requested strict "
                "checking.\nHost key verification failed.\n",
}


def _own_log(text: str) -> None:
    """ssh's own log: the file after -E, as the bridge asked for."""
    path = sys.argv[sys.argv.index("-E") + 1]
    with open(path, "a") as fh:
        fh.write(text)


def main() -> int:
    scenario = _load("scenario.json", {"mode": "wrapper"})
    mode = scenario.get("mode", "wrapper")
    entry = {"argv": sys.argv, "env": dict(os.environ), "mode": mode}
    if mode in OWN_MESSAGES:
        _log(entry)   # stdin is never read: the request went nowhere
        _own_log(OWN_MESSAGES[mode])
        return 255
    if mode == "hang":
        _log(entry)
        _own_log(AUTHENTICATED)
        time.sleep(60)
        return 0
    raw = sys.stdin.buffer.read()
    entry["stdin"] = raw.decode("utf-8", "replace")
    _log(entry)
    if mode == "drop":
        _own_log(AUTHENTICATED)
        sys.stderr.write("Connection to agent.example closed by remote host.\n")
        return 255
    if mode == "raw":
        _own_log(scenario.get("log", AUTHENTICATED))
        sys.stdout.write(scenario.get("stdout", ""))
        sys.stderr.write(scenario.get("stderr", ""))
        return scenario.get("exit", 0)
    _own_log(AUTHENTICATED)
    sys.stderr.write(scenario.get("stderr", ""))
    return wrapper(raw, scenario)


if __name__ == "__main__":
    sys.exit(main())
