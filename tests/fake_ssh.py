#!/usr/bin/env python3
"""A fake `ssh` for the tests of the remote adapter, with a fake wrapper behind it.

Copied into a temporary directory as `ssh` (tests/agent_host_fake.py). It never
opens a connection. Like the real one with `-E <file>`, it writes its own messages
to that file and only the remote command's output to stdout and stderr; the lines
are OpenSSH 9.6's, recorded against a real sshd. As the real one does, it still
writes a few lines to its real stderr whatever `-E` says ("Connection to ...
closed by remote host.", "No user exists for uid ...").

It appends what it was called with (its pid included) to `calls.jsonl` next to
itself, and behaves as `scenario.json` next to itself says:

  {"mode": "wrapper"}          the forced command answered: the fake wrapper below
  {"mode": "refused" | "unresolved" | "denied" | "banner"}
                               OpenSSH's own message for a connection that was
                               never made, in its log, exit 255
  {"mode": "host_key"}         the pinned host key did not match, exit 255
  {"mode": "no_user"}          ssh stopped before it had a log: one line on the
                               real stderr, an empty log, exit 255
  {"mode": "drop"}             the request was read (a turn started), then the
                               connection broke, exit 255
  {"mode": "raw", "stdout": ..., "stderr": ..., "log": ..., "exit": ...}
                               `stderr` is what the remote command printed, `log`
                               what ssh itself wrote; without `log`, ssh says it
                               authenticated
  {"mode": "slow", "delay": s} answers like the wrapper, `delay` seconds late,
                               and leaves a file `finished` when it got there
  {"mode": "flood", "stream": "stdout" | "stderr"}
                               writes without end
  {"mode": "hang"}             never answers

  With the wrapper, also (jarvis-general-conversation):
  "projects": [...]            the agent host's projects: the answer to a list
                               request, and a turn for any other name is
                               `no_checkout`. Without it every name is a project
                               and the list is ["demo", "other"]
  "old_wrapper": true          a wrapper from before the list request: it
                               answers the list `bad_request`
  "project": {...}             the optional `project` field, added to the reply
                               of every agent turn, read-back and error included

THE FAKE WRAPPER AND THE REAL ONE. The fake wrapper speaks version 1 of the
contract (design D33): the same request rules as agent_host/request.py, the nine
reply fields, no stdin read when the kill switch is set, a turn id on `timeout`
and `agent_failed` and none on the errors that come before a turn. Those parts
match the wrapper that exists (phase 1a), and tests/test_contract_with_wrapper.py
holds the bridge against that wrapper's own code.

`"gate": true` is different: it SIMULATES the gate of phase 1b. The real gate now
exists (agent_host/gate, agent_host/decision.py), and
tests/test_contract_with_wrapper.py drives the real wrapper's read-back and confirm
path against the bridge's own parser; this fake stays for the tests that need a
whole `ssh` process and no `claude`. A pending call per project in `state.json`, 60 seconds, single use, cleared by
the next request for that project; `pending`, `readback` and `confirm` in the
reply; a yes that releases it. That is the contract of D33 and D36 as written, not
the behaviour of any wrapper that exists: the real phase 1a answers every `confirm`
with `expired` (`"phase": "1a"` makes the fake do the same). Tests that use the
gate are contract-only until 1b is in. Standard library only.
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


PROJECT = {}


def _reply(**fields):
    """Version 1: always exactly these nine fields, and `project` when the
    scenario has one (jarvis-general-conversation D53)."""
    reply = {"v": 1, "status": "ok", "error": None, "turn": None, "spoken": "",
             "pending": False, "readback": None, "confirm": None, "notice": None}
    reply.update(fields)
    if "project" in PROJECT and reply.get("turn") is not None:
        reply["project"] = PROJECT["project"]
    sys.stdout.write(json.dumps(reply))
    return 0


def _error(code, **extra):
    return _reply(status="error", error=code, **extra)


STARTED_ERRORS = ("timeout", "agent_failed")    # a turn ran: these carry its id


def wrapper(raw: bytes, scenario: dict) -> int:
    if "project" in scenario:
        PROJECT["project"] = scenario["project"]
    state = _load("state.json", {"pending": {}, "turns": [], "n": 0})
    try:
        if len(raw) > MAX_BYTES:
            raise ValueError("too large")
        request = json.loads(raw.decode("utf-8"))
        if isinstance(request, dict) and "list" in request and not scenario.get("old_wrapper"):
            if set(request) != {"v", "list"} or request["v"] != 1 or request["list"] is not True:
                raise ValueError("list shape")
            if scenario.get("error"):
                return _error(scenario["error"])
            names = sorted(scenario.get("projects", ["demo", "other"]))
            sys.stdout.write(json.dumps({"v": 1, "status": "ok", "error": None,
                                         "projects": names}))
            return 0
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
    if scenario.get("phase") == "1a" and confirm is not None:
        return _error("expired")      # no gate, so no pending call can exist
    if scenario.get("error"):
        code = scenario["error"]
        extra = dict(scenario.get("error_extra", {}))
        if code in STARTED_ERRORS and "turn" not in extra:
            state["n"] += 1
            extra["turn"] = f"turn-{state['n']}"
            _save("state.json", state)
        return _error(code, **extra)

    project = request["project"]
    if "projects" in scenario and project not in scenario["projects"]:
        return _error("no_checkout")
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
        if scenario.get("gate"):                 # the simulated gate of phase 1b
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
    entry = {"argv": sys.argv, "env": dict(os.environ), "mode": mode, "pid": os.getpid()}
    if mode in OWN_MESSAGES:
        _log(entry)   # stdin is never read: the request went nowhere
        _own_log(OWN_MESSAGES[mode])
        return 255
    if mode == "no_user":
        _log(entry)   # before any log exists: the real stderr, whatever -E says
        sys.stderr.write(f"No user exists for uid {os.getuid()}\n")
        return 255
    if mode == "hang":
        _log(entry)
        _own_log(AUTHENTICATED)
        time.sleep(600)
        return 0
    if mode == "flood":
        _log(entry)
        _own_log(AUTHENTICATED)
        stream = sys.stdout if scenario.get("stream", "stdout") == "stdout" else sys.stderr
        while True:
            stream.write("x" * 8192)
            stream.flush()
    if mode == "wrapper" and scenario.get("error") == "disabled":
        # The kill switch: the real wrapper answers without reading its input.
        _log(entry)
        _own_log(AUTHENTICATED)
        return _error("disabled", **scenario.get("error_extra", {}))
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
    if mode == "slow":
        time.sleep(scenario.get("delay", 2))
        (HERE / "finished").write_text("the turn ran to its end\n")
    sys.stderr.write(scenario.get("stderr", ""))
    return wrapper(raw, scenario)


if __name__ == "__main__":
    sys.exit(main())
