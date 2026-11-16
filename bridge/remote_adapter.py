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
import tempfile
import threading
import time
import unicodedata

from bridge.agent import (
    ANSWER_NO,
    ANSWER_YES,
    AgentTurn,
    AgentTurnFailed,
    AgentUnavailable,
    ConfirmAnswer,
    PendingConfirmation,
    ProjectNews,
)
from bridge.contract import MAX_LISTED, PROJECT_FIELDS, PROJECT_REASONS
from bridge.config import (
    AGENT_PROJECT_NAME,
    AgentHostConfig,
    ConfigError,
    ProjectConfig,
    check_private_file,
)
from bridge.logging_setup import FILE_ONLY
from bridge.summary import FALLBACK_SUMMARY, extract_summary

logger = logging.getLogger("bridge.remote")

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024   # D33, for the request; the reply is held to the same

# D33: the wrapper accepts no other project name, so no other is ever sent.
PROJECT_NAME = AGENT_PROJECT_NAME

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
# jarvis-general-conversation D53: a reply may carry one more field, `project`,
# what became of a repository the turn created (bridge.contract). Without it a
# reply is exactly the fields above, as before.
PROJECT_KEY = "project"
# The answer to the list request {"v":1,"list":true} (D51). A refusal of it comes
# in the shape above, with status "error".
LIST_REPLY_KEYS = frozenset({"v", "status", "error", "projects"})
# A list reads directory names on the agent host; it is not left running. Well
# under the phone's own wait for `GET /projects`.
LIST_TIMEOUT_S = 8.0
CONFIRM_KEYS = frozenset({"id", "sha256", "class", "expires_s"})
STATUS_OK = "ok"
STATUS_ERROR = "error"
# `expired` answers a confirm whose pending call is gone or too old on the agent host.
EXPIRED = "expired"
ERROR_CODES = frozenset({
    "bad_request", "no_checkout", "busy", "low_memory", "timeout", "agent_failed",
    "disabled", "unsafe_settings", EXPIRED,
})
# The one place that says which codes mean what; bridge.full_mode imports these.
# D30: the agent host answers these before any turn has started there, and only
# these let a turn be answered read-only instead.
FALLBACK_CODES = frozenset({AgentUnavailable.NO_CHECKOUT, AgentUnavailable.DISABLED,
                            AgentUnavailable.UNSAFE_SETTINGS})
BUSY = "busy"
LOW_MEMORY = "low_memory"
BAD_REQUEST = "bad_request"
# No turn can run right now, and nothing was touched: the same request, a yes
# included, can simply be sent again in a moment.
TRY_AGAIN_CODES = frozenset({BUSY, LOW_MEMORY})
# Answered before a turn exists, so `turn` is null with them (D33).
NOT_STARTED_CODES = FALLBACK_CODES | TRY_AGAIN_CODES | {BAD_REQUEST, EXPIRED}

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

# Whether a turn can have started is read from ssh's OWN log and from nothing else.
# `-E <file>` sends that log to a file of the bridge's, so it is separate from the
# remote command's stderr: whatever the wrapper, the agent or a git or ssh child on
# the agent host prints (an "ssh: connect to host ..." of its own, say) never
# reaches it. Checked with OpenSSH 9.6 against a real sshd (docs, "Read-only for
# one turn").
#
# "Never connected" needs all of: exit status 255, nothing on stdout, no
# "Authenticated to" line (LogLevel=VERBOSE writes one the moment the agent host
# accepts the key, before the forced command runs), and one of these lines, which
# OpenSSH only writes before authentication: no route, no listener, no name, no
# banner, or a key the agent host does not accept. "Connection reset by ..." and
# "Connection closed by ..." are NOT here: OpenSSH writes them for a session that
# broke later as well.
_NOT_CONNECTED = tuple(re.compile(p) for p in (
    r"ssh: connect to host \S+ port \d+: .+",
    r"ssh: Could not resolve hostname \S+: .+",
    r"kex_exchange_identification: .+",
    r"banner exchange: Connection to \S+ port \d+: .+",
    r"Connection timed out during banner exchange",
    r"\S+@\S+: Permission denied \([a-z0-9,-]+\)\.",
))
_AUTHENTICATED = "Authenticated to "
_HOST_KEY_FAILED = "Host key verification failed."
SSH_CONNECT_FAILED_EXIT = 255
MAX_LOGGED_CHARS = 4000
# While reading: the cap on a reply plus a margin, so that "exactly 64 KB" and
# "more" can be told apart. Beyond it nothing is kept.
MAX_READ_BYTES = MAX_MESSAGE_BYTES + 1024
READ_CHUNK_BYTES = 16 * 1024
WAIT_STEP_S = 0.05
JOIN_S = 5.0
# An ssh the bridge stopped waiting for is left to end by itself, but not for
# ever: the agent host's own turn timeout is 600 s by default (D34).
REAP_AFTER_S = 900.0


def build_ssh_command(agent: AgentHostConfig, log_file: str) -> list[str]:
    """The argv of D32, with no remote command, plus where ssh writes its own log.
    A test fixes it.

    `-F /dev/null`: no client configuration file, so no ProxyCommand, SendEnv or
    extra identity from one. `BatchMode`: never a prompt. `IdentitiesOnly` and
    `IdentityAgent=none`: this key and no other. The key is named with
    `-o IdentityFile=`, not `-i` as D32 writes it: with `-i`, a file that is not
    there is dropped with a warning and ssh goes on with the default keys in
    `~/.ssh`; with the option it stays the only identity (`ssh -G` shows both). `StrictHostKeyChecking=yes` with
    one pinned file and no global file: an unknown or changed host key ends the
    connection before the request is written (no trust on first use).

    Added to D32's list after review: `LogLevel=VERBOSE` and `-E <log file>`. They
    change nothing about what the connection may do; they make ssh's own account of
    it (connected or not, authenticated or not) readable apart from the remote
    command's stderr, which is what the read-only fallback is decided on.
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
        "-o", "LogLevel=VERBOSE",
        "-E", log_file,
        "-o", f"IdentityFile={agent.key_file}",
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


def build_list_request() -> bytes:
    """The list request: exactly `{"v":1,"list":true}` (D51)."""
    return json.dumps({"v": PROTOCOL_VERSION, "list": True}, separators=(",", ":")).encode()


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


def _load_object(raw: bytes) -> dict:
    """One JSON object of at most 64 KB, no duplicate keys, no NaN or Infinity."""
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ReplyError("larger than 64 KB")
    try:
        reply = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys,
                           parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ReplyError(f"not one JSON object: {exc}") from exc
    if not isinstance(reply, dict):
        raise ReplyError("not a JSON object")
    return reply


def _parse_project(value: object) -> ProjectNews:
    """The optional `project` field (D53): exactly name, ready, reason."""
    if not isinstance(value, dict) or set(value) != PROJECT_FIELDS:
        raise ReplyError("project is not exactly name, ready, reason")
    name, ready, reason = value["name"], value["ready"], value["reason"]
    if not isinstance(name, str) or not PROJECT_NAME.fullmatch(name):
        raise ReplyError("project name is not a project name")
    if type(ready) is not bool:
        raise ReplyError("project ready is not true or false")
    if (reason is not None) if ready else (reason not in PROJECT_REASONS):
        raise ReplyError("project reason does not fit ready")
    return ProjectNews(name=name, ready=ready, reason=reason)


def parse_reply(raw: bytes) -> dict:
    """One JSON object with exactly the fields of version 1, each of its own type,
    and at most the optional `project` field besides.

    Returns `error`, `turn`, `spoken`, `readback`, `notice` (strings, empty for
    null), `confirmation` (a PendingConfirmation or None), `expires_s` and
    `project` (a ProjectNews or None).
    """
    reply = _load_object(raw)
    keys = set(reply) - {PROJECT_KEY}
    if keys != REPLY_KEYS:
        odd = sorted(keys ^ REPLY_KEYS)
        raise ReplyError(f"not exactly the fields of version 1 ({odd[0]!r})")
    project = _parse_project(reply[PROJECT_KEY]) if PROJECT_KEY in reply else None
    if type(reply["v"]) is not int or reply["v"] != PROTOCOL_VERSION:
        raise ReplyError("not version 1")

    status, error, turn = reply["status"], reply["error"], reply["turn"]
    spoken, pending, readback = reply["spoken"], reply["pending"], reply["readback"]
    confirm, notice = reply["confirm"], reply["notice"]
    if turn is not None and (not isinstance(turn, str) or not _TURN_ID.fullmatch(turn)):
        raise ReplyError("turn is not a turn id")
    if not isinstance(spoken, str):
        raise ReplyError("spoken is not text")
    if any(unicodedata.category(ch) == "Cs" for ch in spoken):
        # JSON can carry half a surrogate pair ("\\ud800"). It is not text: it cannot
        # be encoded, so the header with the summary could not be written.
        raise ReplyError("spoken holds a lone surrogate")
    if type(pending) is not bool:
        raise ReplyError("pending is not true or false")
    if notice is not None and (not isinstance(notice, str) or notice not in NOTICE_SENTENCES):
        raise ReplyError("a notice that is not on the closed list")
    parsed = {"error": "", "turn": turn or "", "spoken": spoken, "readback": "",
              "notice": notice or "", "confirmation": None, "expires_s": 0.0,
              "project": project}

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


class ListRefused(Exception):
    """The agent host answered the list request with an error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def parse_list_reply(raw: bytes) -> list[str]:
    """The names from the answer to a list request, sorted (D51).

    Exactly one of two shapes: the list shape, or a turn-shaped reply with
    status "error" (the kill switch answers before it knows which request it
    refuses; an older wrapper refuses the list as `bad_request`), which raises
    ListRefused. Anything else is a ReplyError.
    """
    reply = _load_object(raw)
    if set(reply) - {PROJECT_KEY} == REPLY_KEYS:
        parsed = parse_reply(raw)
        if not parsed["error"]:
            raise ReplyError("a turn reply to a list request")
        raise ListRefused(parsed["error"])
    if set(reply) != LIST_REPLY_KEYS:
        raise ReplyError("not the fields of a list answer")
    if type(reply["v"]) is not int or reply["v"] != PROTOCOL_VERSION:
        raise ReplyError("not version 1")
    if reply["status"] != STATUS_OK or reply["error"] is not None:
        raise ReplyError("a list answer that is not ok")
    names = reply["projects"]
    if not isinstance(names, list) or len(names) > MAX_LISTED:
        raise ReplyError("projects is not a list of at most 500 names")
    if not all(isinstance(name, str) and PROJECT_NAME.fullmatch(name) for name in names):
        raise ReplyError("a project name the agent host would not accept")
    return sorted(set(names))


def speakable_readback(readback: str) -> str | None:
    """The read-back exactly as given, or None when it cannot be spoken as given.

    Nothing is taken out or tidied: a sentence that would have to be changed to be
    spoken is not spoken. So refused, not stripped: an empty or over-long text,
    white space at either end, anything Python would split into lines, every
    control, format, surrogate, private-use or unassigned character (category C),
    and every separator (category Z) but the plain space: no line or paragraph
    separator, no no-break or other odd space that looks like one word break and
    is another.
    """
    if not readback or len(readback) > MAX_READBACK_CHARS:
        return None
    if readback != readback.strip() or len(readback.splitlines()) != 1:
        return None
    for ch in readback:
        category = unicodedata.category(ch)
        if category[0] == "C" or (category[0] == "Z" and ch != " "):
            return None
    return readback


# With an empty log, two things ssh says on its real stderr before it has a log
# or a connection at all: the bridge's uid has no passwd entry (exit 255), or the
# log file could not be opened (exit 1). Only when that line is all there is.
_BEFORE_ANY_LOG = (
    (255, re.compile(r"No user exists for uid \d+")),
    (1, re.compile(r"Couldn't open logfile \S+: .+")),
)


def classify_no_reply(returncode: int, ssh_log: str, stderr: str = "") -> str:
    """Why nothing came back: "unreachable", "host_key" or "lost".

    Read from ssh's own log. Only "unreachable" allows the read-only fallback, and
    only "unreachable" and "host_key" mean that the forced command cannot have
    run. Everything that is not certain is "lost": a turn may have started.
    """
    lines = [ln.strip() for ln in ssh_log.splitlines() if ln.strip()]
    if not lines:
        # No log at all. ssh may have stopped before it could write one; then its
        # real stderr holds that one line and nothing else.
        said = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
        if len(said) == 1 and any(returncode == code and pattern.fullmatch(said[0])
                                  for code, pattern in _BEFORE_ANY_LOG):
            return AgentUnavailable.UNREACHABLE
        return AgentTurnFailed.LOST
    if returncode != SSH_CONNECT_FAILED_EXIT:
        return AgentTurnFailed.LOST
    if any(ln.startswith(_AUTHENTICATED) for ln in lines):
        return AgentTurnFailed.LOST          # the agent host let the key in
    if _HOST_KEY_FAILED in lines:
        return AgentTurnFailed.HOST_KEY
    if any(p.fullmatch(ln) for p in _NOT_CONNECTED for ln in lines):
        return AgentUnavailable.UNREACHABLE
    return AgentTurnFailed.LOST


def check_ssh() -> None:
    """Fail at start, clearly, when `full` mode is set and there is no ssh client."""
    if shutil.which("ssh") is None:
        raise RuntimeError("JARVIS_AGENT_MODE=full needs the ssh client (OpenSSH) on PATH")


class RemoteAdapter:
    name = "full"

    def __init__(self, agent: AgentHostConfig, *, reap_after_s: float = REAP_AFTER_S) -> None:
        self._agent = agent
        self._reap_after_s = reap_after_s

    def run_turn(self, *, project: ProjectConfig, prompt: str,
                 confirm: ConfirmAnswer | None = None) -> AgentTurn:
        if not PROJECT_NAME.fullmatch(project.name):
            # The agent host would refuse this name; it is not sent at all.
            raise AgentUnavailable(AgentUnavailable.NO_CHECKOUT,
                                   "the project name is not one the agent host accepts")
        self._check_files()
        # With an answer the prompt is empty: the wrapper builds its own (D36).
        reply = self._ask(project.name, "" if confirm is not None else prompt, confirm)

        turn_id = reply["turn"]
        if reply["error"]:
            code = reply["error"]
            if code in NOT_STARTED_CODES and turn_id:
                # These codes say "no turn started"; a turn id says one did. A turn
                # that may have run is never answered read-only and never run again.
                raise AgentTurnFailed(AgentTurnFailed.PROTOCOL,
                                      f"{code} with the turn id of a started turn", turn_id)
            if code in FALLBACK_CODES and confirm is None:
                raise AgentUnavailable(code)
            raise AgentTurnFailed(code, turn_id=turn_id, notice=reply["notice"],
                                  project=reply["project"])

        pending = reply["confirmation"]
        news = reply["project"]
        if pending is None:
            return AgentTurn(spoken=_spoken(reply["notice"], reply["spoken"]), ref=turn_id,
                             project=news)
        readback = speakable_readback(reply["readback"])
        if readback is not None:
            return AgentTurn(spoken=readback, ref=turn_id, confirmation=pending,
                             expires_s=reply["expires_s"], project=news)
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
                           project.name, exc.code)
        return AgentTurn(spoken=NEEDS_THE_DESK, ref=turn_id, project=news)

    def list_projects(self) -> list[str]:
        """The agent host's projects (D51), sorted. One connection, at most
        LIST_TIMEOUT_S; ssh is stopped then, nothing runs there that must be left
        alone. Raises AgentUnavailable or AgentTurnFailed when there is no list."""
        self._check_files()
        returncode, stdout, ssh_log, remote_stderr = self._exchange(
            build_list_request(), timeout_s=min(LIST_TIMEOUT_S, self._agent.timeout_s),
            hand_over=False)
        if not stdout.strip():
            why = classify_no_reply(returncode, ssh_log, remote_stderr)
            detail = f"ssh exited {returncode} without a list"
            if why == AgentUnavailable.UNREACHABLE:
                raise AgentUnavailable(why, detail)
            raise AgentTurnFailed(why, detail)
        try:
            return parse_list_reply(stdout)
        except ListRefused as exc:
            if exc.code in FALLBACK_CODES:
                raise AgentUnavailable(exc.code) from exc
            raise AgentTurnFailed(exc.code) from exc
        except ReplyError as exc:
            raise AgentTurnFailed(AgentTurnFailed.PROTOCOL, str(exc)) from exc

    def _check_files(self) -> None:
        """Before every turn, not only at start: the key and the pinned host key
        must still be there and still be the bridge's alone. ssh is not started
        otherwise, so nothing leaves this machine."""
        for path, name in ((self._agent.key_file, "JARVIS_AGENT_KEY_FILE"),
                           (self._agent.known_hosts_file, "JARVIS_AGENT_KNOWN_HOSTS_FILE")):
            try:
                check_private_file(path, name)
            except ConfigError as exc:
                logger.error("agent=full: %s; ssh was not started", exc)
                raise AgentUnavailable(AgentUnavailable.UNREACHABLE, str(exc)) from exc

    def _ask(self, project: str, prompt: str, confirm: ConfirmAnswer | None) -> dict:
        """One connection: one request, one parsed reply."""
        request = build_request(project=project, prompt=prompt, confirm=confirm)
        returncode, stdout, ssh_log, remote_stderr = self._exchange(request)
        if not stdout.strip():
            why = classify_no_reply(returncode, ssh_log, remote_stderr)
            detail = (f"ssh exited {returncode} without a reply; its log: "
                      f"{ssh_log.strip()[:MAX_LOGGED_CHARS]!r}; the remote stderr: "
                      f"{remote_stderr.strip()[:MAX_LOGGED_CHARS]!r}")
            if why == AgentUnavailable.UNREACHABLE:
                raise AgentUnavailable(why, detail)
            # host_key: nothing was sent. lost: connected, or possibly connected,
            # and no answer, so the turn may be running on the agent host. Neither
            # is ever answered by a second run (D30).
            raise AgentTurnFailed(why, detail)
        try:
            return parse_reply(stdout)
        except ReplyError as exc:
            raise AgentTurnFailed(AgentTurnFailed.PROTOCOL, str(exc)) from exc

    def _exchange(self, request: bytes, *, timeout_s: float | None = None,
                  hand_over: bool = True) -> tuple[int, bytes, str, str]:
        """Run ssh once. Returns its exit status, stdout, its own log, the remote stderr.

        Both streams are read as they come and never kept beyond the cap; more than
        that is not a reply, and ssh is stopped. When the wait is over and ssh is
        still there, the bridge stops waiting and does nothing else: the connection
        stays, the turn on the agent host goes on and is recorded there (D34). With
        `hand_over=False` (a list: nothing runs there that must be left alone) ssh
        is stopped instead.
        """
        # ssh gets a path and a home, nothing else: not the phone's token, not the
        # Claude credentials, no agent socket. With -F /dev/null it forwards no
        # variable anyway, so nothing of the pod reaches the agent host.
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "HOME": os.environ.get("HOME", "/")}
        try:
            fd, log_file = tempfile.mkstemp(prefix="jarvis-ssh-", suffix=".log")   # 0600
            os.close(fd)
        except OSError as exc:
            raise AgentUnavailable(AgentUnavailable.UNREACHABLE,
                                   f"no file for ssh's log: {exc}") from exc
        handed_over = False
        try:
            command = build_ssh_command(self._agent, log_file)
            try:
                proc = subprocess.Popen(
                    command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, env=env, start_new_session=True,
                )
            except OSError as exc:
                # No ssh client, or it cannot be started: nothing left this machine.
                raise AgentUnavailable(AgentUnavailable.UNREACHABLE,
                                       f"ssh could not be started: {exc}") from exc
            overflow = threading.Event()
            out = _Capture(proc.stdout, MAX_READ_BYTES, overflow)
            err = _Capture(proc.stderr, MAX_READ_BYTES, overflow)
            feeder = threading.Thread(target=_feed, args=(proc.stdin, request), daemon=True)
            threads = (out, err, feeder)
            for thread in threads:
                thread.start()

            wait_s = self._agent.timeout_s if timeout_s is None else timeout_s
            deadline = time.monotonic() + wait_s
            while True:
                try:
                    proc.wait(timeout=WAIT_STEP_S)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if overflow.is_set():
                    _kill(proc)
                    break
                if time.monotonic() >= deadline and not hand_over:
                    _kill(proc)
                    for thread in threads:
                        thread.join(timeout=JOIN_S)
                    raise AgentTurnFailed(
                        AgentTurnFailed.PROTOCOL,
                        f"no answer from the agent host within {wait_s}s; ssh was stopped")
                if time.monotonic() >= deadline:
                    handed_over = True
                    threading.Thread(target=self._reap, args=(proc, threads, log_file),
                                     daemon=True).start()
                    raise AgentTurnFailed(
                        AgentTurnFailed.STILL_RUNNING,
                        f"no reply from the agent host within {wait_s}s; "
                        f"the bridge stopped waiting, the turn goes on there")
            for thread in (out, err):
                thread.join(timeout=JOIN_S)
            if overflow.is_set():
                raise AgentTurnFailed(AgentTurnFailed.PROTOCOL,
                                      "more than 64 KB came back; ssh was stopped")
            try:
                with open(log_file, encoding="utf-8", errors="replace") as fh:
                    ssh_log = fh.read(MAX_MESSAGE_BYTES)
            except OSError:
                ssh_log = ""    # no account of the connection: nothing is assumed
            return proc.returncode, out.data(), ssh_log, err.data().decode("utf-8", "replace")
        finally:
            if not handed_over:
                _unlink(log_file)

    def _reap(self, proc: subprocess.Popen, threads: tuple, log_file: str) -> None:
        """An ssh the bridge stopped waiting for: left alone until it ends by itself,
        so the connection is not cut under a running turn. Whatever it still says is
        read and thrown away. One that outlives every turn timeout is stopped."""
        try:
            try:
                proc.wait(timeout=self._reap_after_s)
            except subprocess.TimeoutExpired:
                logger.warning("agent=full: ssh pid %s still there after %ss; stopped",
                               proc.pid, self._reap_after_s)
                _kill(proc)
            for thread in threads:
                thread.join(timeout=JOIN_S)
        finally:
            _unlink(log_file)


class _Capture(threading.Thread):
    """Reads a pipe to its end, keeps at most `limit` bytes, says when there was more."""

    def __init__(self, pipe, limit: int, overflow: threading.Event) -> None:
        super().__init__(daemon=True)
        self._pipe = pipe
        self._limit = limit
        self._overflow = overflow
        self._chunks: list[bytes] = []
        self._kept = 0

    def run(self) -> None:
        try:
            while chunk := self._pipe.read1(READ_CHUNK_BYTES):
                room = self._limit - self._kept
                if len(chunk) > room:
                    self._overflow.set()      # read on, keep nothing more
                    chunk = chunk[:room]
                if chunk:
                    self._chunks.append(chunk)
                    self._kept += len(chunk)
        except (OSError, ValueError):
            pass
        finally:
            try:
                self._pipe.close()
            except OSError:
                pass

    def data(self) -> bytes:
        return b"".join(self._chunks)


def _feed(pipe, request: bytes) -> None:
    """The request on stdin, then end of input. A wrapper that answers without
    reading (its kill switch) closes the pipe first; that is not an error."""
    try:
        pipe.write(request)
        pipe.flush()
    except (OSError, ValueError):
        pass
    finally:
        try:
            pipe.close()
        except (OSError, ValueError):
            pass


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    proc.wait()


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _spoken(notice: str, marker_line: str) -> str:
    """The notice's fixed sentence first, then the agent's summary when it gave one."""
    if not notice:
        return extract_summary(marker_line)
    sentence = NOTICE_SENTENCES[notice]
    if not marker_line.strip():
        return sentence
    summary = extract_summary(marker_line)
    return sentence if summary == FALLBACK_SUMMARY else f"{sentence} {summary}"
