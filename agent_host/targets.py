"""The strict shape of a target, per kind (1.7c, D36 "Targets have a strict shape").

A read-back holds only fixed words and targets. The call is written by the
agent, so a target is spoken only when it has the shape its kind must have:

| Kind | Shape |
|---|---|
| number | digits only |
| repository | `owner/name`, each part `[A-Za-z0-9._-]` |
| ref | a valid ref name by git's rules, no whitespace |
| commit | 7 to 40 hex digits |
| mail address | one `local@domain`, domain a DNS name |
| label | a DNS label (namespace, application, release, resource name) |
| host | a DNS name or an address |
| path | its real path lies under an allowed root |

On top of the shape: a target of more than 100 characters, or one that
looks like a credential (`sk-ant-`, or a run of 32 or more token
characters), is not nameable. A read-back skips the bridge's free-text
filter and is also sent in the notify ping, so nothing shaped like a secret
may ride in it. For a ref the pattern is applied to each part between `-`,
`/` and `_` (a long hyphenated branch name stays nameable, a ref that
carries a token does not) and the 100 characters to the whole ref. A commit
is spoken as its first seven digits, so a full commit id is not affected.

Everything here either returns the spoken form or raises `NotNameable`;
nothing is repaired, trimmed or guessed.
"""
from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass

MAX_TARGET_CHARS = 100
_TOKEN_RUN = 32

# The bridge's secret-shaped pattern (bridge/summary.py, `_SECRET_LIKE`).
_SECRET_RE = re.compile(r"[sS][kK]-[aA][nN][tT]-|[A-Za-z0-9_-]{%d,}" % _TOKEN_RUN, re.ASCII)
_REF_PART_SPLIT_RE = re.compile(r"[-/_]")

_NUMBER_RE = re.compile(r"[0-9]+", re.ASCII)
_REPO_PART = r"[A-Za-z0-9._-]+"
_REPO_RE = re.compile(rf"({_REPO_PART})/({_REPO_PART})", re.ASCII)
_COMMIT_RE = re.compile(r"[0-9a-fA-F]{7,40}")
_FULL_COMMIT_RE = re.compile(r"[0-9a-fA-F]{40}")
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_LABEL_RE = re.compile(_LABEL, re.ASCII)
_UPPER_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_DNS_NAME_RE = re.compile(rf"(?=.{{1,253}}\Z){_UPPER_LABEL}(?:\.{_UPPER_LABEL})*", re.ASCII)
_MAIL_LOCAL_RE = re.compile(r"[A-Za-z0-9._%+-]+", re.ASCII)
# A cluster context or a similar configured name: not in D36's table by
# kind, so the narrowest shape that real names fit (`admin@homelab`).
_NAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._@-]*[A-Za-z0-9])?")
_REF_FORBIDDEN_RE = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


def looks_like_secret(text: str) -> bool:
    """Free text shaped like a credential: `sk-ant-` anywhere, or a run of 32
    token characters between `-`, `/` and `_`, as for a ref (a long hyphenated
    name is not a secret, a run that carries a token is)."""
    if "sk-ant-" in text.lower():
        return True
    return any(_SECRET_RE.search(part) for part in _REF_PART_SPLIT_RE.split(text))


class NotNameable(Exception):
    """This call cannot be read back. Nothing is pending; it needs the desk."""


@dataclass(frozen=True)
class Target:
    kind: str
    value: str   # exactly as it stands in the call
    spoken: str  # as it is said in the read-back


def _plain_ascii(value: object) -> bool:
    """Visible ASCII only: no other script (a Cyrillic `а` in a repository
    name reads like the Latin one), no whitespace, no control or
    direction-changing character."""
    return isinstance(value, str) and value.isascii() and all(33 <= ord(ch) <= 126 for ch in value)


def _guard(value: str, spoken: str, kind: str) -> None:
    if not _plain_ascii(value):
        raise NotNameable(f"a {kind} with characters outside plain ASCII")
    if not value or len(value) > MAX_TARGET_CHARS or len(spoken) > MAX_TARGET_CHARS * 2:
        raise NotNameable(f"a {kind} that is empty or over {MAX_TARGET_CHARS} characters")
    if _SECRET_RE.search(spoken):
        raise NotNameable(f"a {kind} shaped like a secret")


def number(value: str) -> Target:
    if not _plain_ascii(value) or not _NUMBER_RE.fullmatch(value):
        raise NotNameable("a number that is not digits only")
    _guard(value, value, "number")
    return Target("number", value, value)


def repository(value: str, own_owners) -> Target:
    """Spoken as "name" when the owner is one of the user's own, otherwise
    as "name of owner"."""
    match = _REPO_RE.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise NotNameable("a repository that is not owner/name")
    owner, name = match.group(1), match.group(2)
    if owner in (".", "..") or name in (".", ".."):
        raise NotNameable("a repository that is not owner/name")
    own = owner.lower() in {o.lower() for o in own_owners}
    spoken = name if own else f"{name} of {owner}"
    _guard(value, spoken, "repository")
    return Target("repository", value, spoken)


def is_valid_ref(value: str) -> bool:
    """git's own rules for a ref name (git-check-ref-format), plus: not HEAD's
    relatives (`@`), and no leading dash."""
    if not value or value == "@" or value.startswith(("-", "/")) or value.endswith(("/", ".")):
        return False
    if _REF_FORBIDDEN_RE.search(value) or ".." in value or "@{" in value or "//" in value:
        return False
    for part in value.split("/"):
        if not part or part.startswith(".") or part.endswith(".lock"):
            return False
    return True


def ref(value: str) -> Target:
    """A `/` is spoken as "slash" (`release/1.2`: "release slash 1.2")."""
    if not _plain_ascii(value) or not is_valid_ref(value):
        raise NotNameable("a ref that is not a valid ref name in plain ASCII")
    if len(value) > MAX_TARGET_CHARS:
        raise NotNameable(f"a ref over {MAX_TARGET_CHARS} characters")
    for part in _REF_PART_SPLIT_RE.split(value):
        if len(part) >= _TOKEN_RUN or "sk-ant-" in value.lower():
            raise NotNameable("a ref shaped like a secret")
    return Target("ref", value, value.replace("/", " slash "))


def commit(value: str, *, full: bool = False) -> Target:
    """Spoken as its first seven hex digits; the hash and a merge pin keep
    the full value."""
    pattern = _FULL_COMMIT_RE if full else _COMMIT_RE
    if not _plain_ascii(value) or not pattern.fullmatch(value):
        raise NotNameable("a commit that is not hex digits of the right length")
    return Target("commit", value, value[:7].lower())


def mail_address(value: str) -> Target:
    if not _plain_ascii(value) or value.count("@") != 1:
        raise NotNameable("a mail address that is not local@domain in plain ASCII")
    local, domain = value.split("@")
    if not _MAIL_LOCAL_RE.fullmatch(local) or "." not in domain or not _DNS_NAME_RE.fullmatch(domain):
        raise NotNameable("a mail address that is not local@domain")
    _guard(value, value, "mail address")
    return Target("mail", value, value)


def label(value: str) -> Target:
    if not isinstance(value, str) or not _LABEL_RE.fullmatch(value):
        raise NotNameable("a name that is not a DNS label")
    _guard(value, value, "name")
    return Target("label", value, value)


def name(value: str) -> Target:
    if not isinstance(value, str) or not _NAME_RE.fullmatch(value):
        raise NotNameable("a name with characters a name cannot have")
    _guard(value, value, "name")
    return Target("name", value, value)


def host(value: str) -> Target:
    if not _plain_ascii(value) or "%" in value:
        # No zone id (`fe80::1%eth0`): what stands behind the % is free text.
        raise NotNameable("a host that is not a DNS name or an address")
    bare = value[1:-1] if value.startswith("[") and value.endswith("]") else value
    try:
        ipaddress.ip_address(bare)
    except ValueError:
        if not _DNS_NAME_RE.fullmatch(value):
            raise NotNameable("a host that is not a DNS name or an address") from None
    _guard(value, value, "host")
    return Target("host", value, value)


def path(value: str, roots) -> Target:
    """A path whose real path lies under one of `roots`. `value` must be
    absolute: the caller resolves it against the stored `cwd` first."""
    if not isinstance(value, str) or not os.path.isabs(value):
        raise NotNameable("a path that is not absolute")
    real = os.path.realpath(value)
    for root in roots:
        real_root = os.path.realpath(root)
        if real == real_root or real.startswith(real_root.rstrip(os.sep) + os.sep):
            _guard(value, real, "path")
            return Target("path", value, real)
    raise NotNameable("a path outside the roots allowed for this action")


def join_spoken(items: list[str]) -> str:
    """"a", "a and b", "a, b and c"."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]
