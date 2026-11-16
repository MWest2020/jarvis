"""Which repository an address names, and where a push really goes (1.7e, D36).

A push names its remote (`origin`). Which repository that is depends on git
configuration the call's bytes do not contain: git pushes to
`remote.<name>.pushurl` when it is set, rewrites addresses with
`pushInsteadOf`, and may push to several addresses at once. So the remote is
resolved the way git itself will, with `git remote get-url --push --all
<remote>` in the stored `cwd`.

An address names a repository only when its host is the configured forge
host and it has exactly one of three forms, each with an optional `.git`:

    git@<host>:owner/name
    ssh://git@<host>/owner/name
    https://<host>/owner/name

Another host, another user, a port, credentials in the address, a path with
more parts or any other scheme does not: `git@elsewhere.example:me/jarvis`
has the right owner and name and must never be read back as "jarvis".
Exactly one address is nameable; more than one is not.

The same rule judges the checkout's remote in the `--repo` consistency check
of a `gh` call. Nothing here fills in a target that is missing from a call.

The resolved address is part of a push's hash (agent_host.gatehash), and the
gate resolves it again when the call is released.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_PART = r"[A-Za-z0-9._-]+"
_REMOTE_NAME_RE = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._/-]*")

GitRunner = Callable[[list[str], Path], "str | None"]


@dataclass(frozen=True)
class Address:
    raw: str       # the line git printed, byte for byte: this goes into the hash
    owner: str
    name: str

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.name}"


def parse_address(raw: str, forge_host: str) -> Address | None:
    """The repository an address names, or None when it names none we may say."""
    host = re.escape(forge_host)
    for pattern in (
        rf"git@{host}:({_PART})/({_PART})",
        rf"ssh://git@{host}/({_PART})/({_PART})",
        rf"https://{host}/({_PART})/({_PART})",
    ):
        match = re.fullmatch(pattern, raw)
        if not match:
            continue
        owner, name = match.group(1), match.group(2)
        if name.endswith(".git"):
            name = name[:-len(".git")]
        if not name or owner in (".", "..") or name in (".", ".."):
            return None
        return Address(raw=raw, owner=owner, name=name)
    return None


def raw_push_addresses(remote: str, cwd: Path, git: GitRunner) -> list[str] | None:
    """Every address a push to `remote` goes to, as git resolves them."""
    if not _REMOTE_NAME_RE.fullmatch(remote):
        return None
    out = git(["remote", "get-url", "--push", "--all", remote], cwd)
    if out is None:
        return None
    return [line for line in out.splitlines() if line.strip()]


def resolve_push(remote: str, cwd: Path, forge_host: str, git: GitRunner) -> Address | None:
    """The one repository a push to `remote` goes to, or None."""
    addresses = raw_push_addresses(remote, cwd, git)
    if addresses is None or len(addresses) != 1:
        return None
    return parse_address(addresses[0], forge_host)


def raw_push_address(remote: str, cwd: Path, git: GitRunner) -> str | None:
    """What goes into a push's hash: the resolved address lines, joined. None
    when the remote cannot be resolved at all."""
    addresses = raw_push_addresses(remote, cwd, git)
    if not addresses:
        return None
    return "\n".join(addresses)


CHECKOUT_UNKNOWN = "unknown"


def checkout_repository(cwd: Path, forge_host: str, git: GitRunner) -> Address | None | str:
    """The repository the checkout at `cwd` belongs to, for the `--repo`
    consistency check.

    None: `cwd` is no checkout with an `origin` remote, so there is nothing
    to be consistent with. `CHECKOUT_UNKNOWN`: there is such a remote and
    its address is not one of the exact forms on the forge host; the call is
    then not nameable. Otherwise the repository.
    """
    out = git(["remote", "get-url", "--all", "origin"], cwd)
    if out is None:
        return None
    addresses = [line for line in out.splitlines() if line.strip()]
    if len(addresses) != 1:
        return CHECKOUT_UNKNOWN
    return parse_address(addresses[0], forge_host) or CHECKOUT_UNKNOWN
