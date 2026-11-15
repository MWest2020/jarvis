"""The `full` adapter: one ssh connection per turn to the wrapper on the agent host
(jarvis-full-agent D32, D33).

The key's `authorized_keys` line on the agent host forces one fixed command, so
the bridge names no remote command at all. It writes one JSON request on stdin
and reads one JSON reply. Nothing in the request names a command, an option, a
tool, a permission mode, a path, a settings file or a session id.

What this module decides, and nothing else does: whether a turn that did not
come back with an answer may be answered read-only instead (D30). It may only
when it is certain that no turn was started on the agent host.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import subprocess
import unicodedata

from bridge.agent import (
    ANSWER_NO,
    ANSWER_YES,
    AgentTurn,
    AgentTurnFailed,
    AgentUnavailable,
    ConfirmAnswer,
    PendingConfirmation,
)
from bridge.config import AgentHostConfig, ProjectConfig
from bridge.logging_setup import FILE_ONLY
from bridge.summary import FALLBACK_SUMMARY, extract_summary

logger = logging.getLogger("bridge.remote")

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024   # D33, for the request; the reply is held to the same

# D33: the wrapper accepts no other project name, so no other is ever sent.
PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

# The reply, version 1 (D33): one JSON object, always with exactly these fields.
#   v         1
#   status    "ok" or "error"
#   error     null, or a code from the closed list
#   turn      the turn id on the agent host, null when no turn started
#   spoken    the agent's marker line, marker included, or "". It goes through
#             summary.extract_summary as a read-only reply does; nothing else of the
#             reply ever leaves the agent host
#   pending   true when a gated call waits for the driver's yes
#   readback  the sentence to speak when pending, else null
#   confirm   when pending: {"id", "sha256", "class", "expires_s"}, else null
#   notice    null, or a code the bridge has a fixed sentence for
REPLY_KEYS = frozenset({"v", "status", "error", "turn", "spoken", "pending", "readback",
                        "confirm", "notice"})
CONFIRM_KEYS = frozenset({"id", "sha256", "class", "expires_s"})
STATUS_OK = "ok"
STATUS_ERROR = "error"
# `expired` answers a confirm whose pending call is gone or too old on the agent host.
EXPIRED = "expired"
ERROR_CODES = frozenset({
    "bad_request", "no_checkout", "busy", "low_memory", "timeout", "agent_failed",
    "disabled", "unsafe_settings", EXPIRED,
})
# D30: the agent host answers these before any turn has started there.
FALLBACK_CODES = frozenset({"no_checkout", "disabled", "unsafe_settings"})

NEEDS_THE_DESK = "That one needs the desk."
OUT_OF_BUDGET = "out_of_budget"
# The closed list of notices, each with the bridge's own fixed sentence.
NOTICE_SENTENCES = {
    "needs_desk": NEEDS_THE_DESK,
    "run_alone": "That command has to be run on its own, so it was refused.",
    "settings_changed": "The project settings changed. That needs the desk.",
    "new_session": "I started a new session for this project.",
    "target_changed": "The push target changed. That one needs the desk.",
    "out_of_budget": "The agent is out of budget for now.",
}

# A read-back is fixed words plus shape-checked targets, built on the agent host.
# It is spoken as given or not at all: never cleaned, never put through the summary
# filter, never replaced by another sentence. "Can be spoken" means only this: one
# line of text, of a length a sentence has.
MAX_READBACK_CHARS = 500

_TURN_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_CONFIRM_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CLASS = re.compile(r"[a-z][a-z_, -]{0,99}")
MAX_EXPIRES_S = 60.0

# OpenSSH prints these, on a line of their own, when it gives up before a session
# exists: no route, no listener, no name, no banner, or a key the agent host does
# not accept (its `authorized_keys` line is missing, or `from=` does not match).
# The forced command has not run in any of them. Messages of a connection that
# broke later ("Connection to … closed by remote host", "client_loop: …",
# "Timeout, server … not responding") are deliberately not here.
_NOT_CONNECTED = tuple(re.compile(p) for p in (
    r"ssh: connect to host \S+ port \d+: .+",
    r"ssh: Could not resolve hostname \S+: .+",
    r"kex_exchange_identification: .+",
    r"banner exchange: Connection to \S+ port \d+: .+",
    r"Connection timed out during banner exchange",
    r"Connection (closed|reset) by \S+ port \d+",
    r"\S+@\S+: Permission denied \([a-z0-9,-]+\)\.",
))
_HOST_KEY_FAILED = "Host key verification failed."
SSH_CONNECT_FAILED_EXIT = 255


def build_ssh_command(agent: AgentHostConfig) -> list[str]:
    """The argv of D32, with no remote command. A test fixes it.

    `-F /dev/null`: no client configuration file, so no ProxyCommand, SendEnv or
    extra identity from one. `BatchMode`: never a prompt. `IdentitiesOnly` and
    `IdentityAgent=none`: this key and no other. `StrictHostKeyChecking=yes` with
    one pinned file and no global file: an unknown or changed host key ends the
    connection before the request is written (no trust on first use).
    """
    return [
        "ssh", "-T", "-F", "/dev/null",
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "IdentityAgent=none",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={agent.known_hosts_file}",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=15",
        "-i", str(agent.key_file),
        "-p", str(agent.port),
        f"{agent.user}@{agent.host}",
    ]


def build_request(*, project: str, prompt: str, confirm: ConfirmAnswer | None) -> bytes:
    """Version 1 of the request: exactly these four fields (D33)."""
    confirm_field = None
    if confirm is not None:
        if confirm.answer not in (ANSWER_YES, ANSWER_NO):
            raise ValueError(f"not an answer: {confirm.answer!r}")
        confirm_field = {"id": confirm.id, "sha256": confirm.sha256, "answer": confirm.answer}
    body = json.dumps(
        {"v": PROTOCOL_VERSION, "project": project, "prompt": prompt, "confirm": confirm_field},
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    if len(body) > MAX_MESSAGE_BYTES:
        raise AgentTurnFailed("bad_request", "the request is larger than 64 KB; not sent")
    return body


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    found: dict = {}
    for key, value in pairs:
        if key in found:
            raise ValueError(f"duplicate key {key!r}")
        found[key] = value
    return found


def _reject_constant(name: str) -> None:
    raise ValueError(f"not JSON: {name}")


class ReplyError(ValueError):
    """The reply is not the agreed shape."""


def parse_reply(raw: bytes) -> dict:
    """One JSON object with exactly the fields of version 1, each of its own type.

    Returns `error`, `turn`, `spoken`, `readback`, `notice` (strings, empty for
    null), `confirmation` (a PendingConfirmation or None) and `expires_s`.
    """
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ReplyError("larger than 64 KB")
    try:
        reply = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys,
                           parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ReplyError(f"not one JSON object: {exc}") from exc
    if not isinstance(reply, dict):
        raise ReplyError("not a JSON object")
    if set(reply) != REPLY_KEYS:
        odd = sorted(set(reply) ^ REPLY_KEYS)
        raise ReplyError(f"not exactly the fields of version 1 ({odd[0]!r})")
    if type(reply["v"]) is not int or reply["v"] != PROTOCOL_VERSION:
        raise ReplyError("not version 1")

    status, error, turn = reply["status"], reply["error"], reply["turn"]
    spoken, pending, readback = reply["spoken"], reply["pending"], reply["readback"]
    confirm, notice = reply["confirm"], reply["notice"]
    if turn is not None and (not isinstance(turn, str) or not _TURN_ID.fullmatch(turn)):
        raise ReplyError("turn is not a turn id")
    if not isinstance(spoken, str):
        raise ReplyError("spoken is not text")
    if type(pending) is not bool:
        raise ReplyError("pending is not true or false")
    if notice is not None and (not isinstance(notice, str) or notice not in NOTICE_SENTENCES):
        raise ReplyError("a notice that is not on the closed list")
    parsed = {"error": "", "turn": turn or "", "spoken": spoken, "readback": "",
              "notice": notice or "", "confirmation": None, "expires_s": 0.0}

    if status == STATUS_ERROR:
        if not isinstance(error, str) or error not in ERROR_CODES:
            raise ReplyError("an error that is not on the closed list")
        if pending or confirm is not None or readback is not None:
            raise ReplyError("an error with a pending confirmation")
        return parsed | {"error": error, "spoken": ""}   # the bridge has its own words
    if status != STATUS_OK:
        raise ReplyError("status is neither ok nor error")
    if error is not None:
        raise ReplyError("status ok with an error")
    if not pending:
        if confirm is not None or readback is not None:
            raise ReplyError("a read-back or a confirm without pending")
        return parsed

    if not isinstance(readback, str):
        raise ReplyError("pending without a read-back")
    if not isinstance(confirm, dict) or set(confirm) != CONFIRM_KEYS:
        raise ReplyError("pending without a confirm of exactly id, sha256, class, expires_s")
    ident, digest, expires_s = confirm["id"], confirm["sha256"], confirm["expires_s"]
    if not isinstance(ident, str) or not _CONFIRM_ID.fullmatch(ident):
        raise ReplyError("confirm id is not what it should be")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ReplyError("confirm sha256 is not 64 lower-case hex digits")
    # D33 names the field, D36 says a call can have several classes; whether those
    # come as one string or as a list is not settled, so both are read. The bridge
    # does nothing with the value.
    classes = confirm["class"]
    if isinstance(classes, str):
        classes = [classes]
    if (not isinstance(classes, list) or not 1 <= len(classes) <= 16
            or not all(isinstance(c, str) and _CLASS.fullmatch(c) for c in classes)):
        raise ReplyError("confirm class is not what it should be")
    if type(expires_s) not in (int, float) or not 0 < expires_s <= MAX_EXPIRES_S:
        raise ReplyError("confirm expires_s is not a number of seconds up to 60")
    # The agent's own line is never spoken while a call is pending (D36, step 4).
    return parsed | {"spoken": "", "readback": readback, "expires_s": float(expires_s),
                     "confirmation": PendingConfirmation(id=ident, sha256=digest)}


def speakable_readback(readback: str) -> str | None:
    """The read-back exactly as given, or None when it cannot be spoken at all."""
    text = readback.strip()
    if not text or len(text) > MAX_READBACK_CHARS:
        return None
    if any(unicodedata.category(ch)[0] == "C" for ch in text):   # newlines, controls
        return None
    return text


def _never_connected(returncode: int, stdout: bytes, stderr: str) -> bool:
    """True only when ssh itself says that no session was ever opened."""
    if returncode != SSH_CONNECT_FAILED_EXIT or stdout.strip():
        return False
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    return any(p.fullmatch(ln) for p in _NOT_CONNECTED for ln in lines)


def check_ssh() -> None:
    """Fail at start, clearly, when `full` mode is set and there is no ssh client."""
    if shutil.which("ssh") is None:
        raise RuntimeError("JARVIS_AGENT_MODE=full needs the ssh client (OpenSSH) on PATH")


class RemoteAdapter:
    name = "full"

    def __init__(self, agent: AgentHostConfig) -> None:
        self._agent = agent

    def run_turn(self, *, project: ProjectConfig, prompt: str,
                 confirm: ConfirmAnswer | None = None) -> AgentTurn:
        if not PROJECT_NAME.fullmatch(project.name):
            # The agent host would refuse this name; it is not sent at all.
            raise AgentUnavailable(AgentUnavailable.NO_CHECKOUT,
                                   "the project name is not one the agent host accepts")
        # With an answer the prompt is empty: the wrapper builds its own (D36).
        reply = self._ask(project.name, "" if confirm is not None else prompt, confirm)

        turn_id = reply["turn"]
        if reply["error"]:
            code = reply["error"]
            if code in FALLBACK_CODES and confirm is None:
                raise AgentUnavailable(code)
            raise AgentTurnFailed(code, turn_id=turn_id, notice=reply["notice"])

        pending = reply["confirmation"]
        if pending is None:
            return AgentTurn(spoken=_spoken(reply["notice"], reply["spoken"]), ref=turn_id)
        readback = speakable_readback(reply["readback"])
        if readback is not None:
            return AgentTurn(spoken=readback, ref=turn_id, confirmation=pending,
                             expires_s=reply["expires_s"])
        # Nothing can be said, so nothing can be confirmed: the pending call is
        # cleared on the agent host with a no, and the driver hears the desk line.
        logger.warning("project=%s turn=%s: the read-back cannot be spoken; the pending "
                       "call is cancelled", project.name, turn_id or "-")
        logger.info("project=%s turn=%s read_back=%r", project.name, turn_id or "-",
                    reply["readback"], extra=FILE_ONLY)
        try:
            self._ask(project.name, "", ConfirmAnswer(pending.id, pending.sha256, ANSWER_NO))
        except (AgentUnavailable, AgentTurnFailed) as exc:
            # It then expires there by itself; this side keeps nothing to confirm.
            logger.warning("project=%s: the cancel did not reach the agent host (%s)",
                           project.name, getattr(exc, "code", None) or exc.reason)
        return AgentTurn(spoken=NEEDS_THE_DESK, ref=turn_id)

    def _ask(self, project: str, prompt: str, confirm: ConfirmAnswer | None) -> dict:
        """One connection: one request, one parsed reply."""
        request = build_request(project=project, prompt=prompt, confirm=confirm)
        returncode, stdout, stderr = self._exchange(request)
        if any(ln.strip() == _HOST_KEY_FAILED for ln in stderr.splitlines()) and not stdout.strip():
            raise AgentTurnFailed(AgentTurnFailed.HOST_KEY, stderr.strip())
        if _never_connected(returncode, stdout, stderr):
            raise AgentUnavailable(AgentUnavailable.UNREACHABLE, stderr.strip())
        if not stdout.strip():
            # Connected, or possibly connected, and no answer: the turn may be
            # running on the agent host. It is never run again (D30).
            raise AgentTurnFailed(
                AgentTurnFailed.LOST, f"ssh exited {returncode} without a reply: {stderr.strip()}")
        try:
            return parse_reply(stdout)
        except ReplyError as exc:
            raise AgentTurnFailed(AgentTurnFailed.PROTOCOL, str(exc)) from exc

    def _exchange(self, request: bytes) -> tuple[int, bytes, str]:
        command = build_ssh_command(self._agent)
        # ssh gets a path and a home, nothing else: not the phone's token, not the
        # Claude credentials, no agent socket. With -F /dev/null it forwards no
        # variable anyway, so nothing of the pod reaches the agent host.
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "HOME": os.environ.get("HOME", "/")}
        try:
            proc = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, start_new_session=True,
            )
        except OSError as exc:
            # No ssh client, or it cannot be started: nothing left this machine.
            raise AgentUnavailable(AgentUnavailable.UNREACHABLE,
                                   f"ssh could not be started: {exc}") from exc
        try:
            stdout, stderr = proc.communicate(request, timeout=self._agent.timeout_s)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
            # Only the connection is cut. The turn on the agent host runs to its
            # end or to its own timeout, and is recorded there (D34).
            raise AgentTurnFailed(
                AgentTurnFailed.NO_ANSWER,
                f"no reply from the agent host within {self._agent.timeout_s}s") from exc
        return proc.returncode, stdout, stderr.decode("utf-8", "replace")


def _spoken(notice: str, marker_line: str) -> str:
    """The notice's fixed sentence first, then the agent's summary when it gave one."""
    if not notice:
        return extract_summary(marker_line)
    sentence = NOTICE_SENTENCES[notice]
    if not marker_line.strip():
        return sentence
    summary = extract_summary(marker_line)
    return sentence if summary == FALLBACK_SUMMARY else f"{sentence} {summary}"
