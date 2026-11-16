"""What the wrapper adds to a read-back from outside the call (1.10, D36).

Nothing enforces "review and green checks before a merge"; the read-back
states both, so that the yes is an informed one.

- **A merge.** The review decision and the check roll-up of the pull
  request, for the commit the merge is pinned to. Working assumption,
  written here because the design names no command: the wrapper runs

      gh pr view <number> --repo <owner>/<name> --json reviewDecision,statusCheckRollup,headRefOid

  (the command is the configuration key `pr_status_command`, faked in the
  tests). It runs in the stored `cwd` with the **same environment the turn
  uses** (agent_host.identity.build_child_env): the wrapper's own
  environment comes from sshd, where `gh` would find no credentials and
  every merge would be read back as "status unknown". A fetch that fails in
  any way says "Status unknown" and never "green"; a head that is no longer
  the pinned commit says "The pull request has moved".
- **A push to a default branch.** How many commits, and "no review".

The words are fixed; nothing GitHub returns is spoken, only counted and
compared.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

STATUS_UNKNOWN = "Status unknown."
MOVED = "The pull request has moved."

_GREEN = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})
_RUNNING = frozenset({"PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED", "WAITING", "REQUESTED"})
_REVIEWS = {
    "APPROVED": "review approved",
    "CHANGES_REQUESTED": "changes requested",
    "REVIEW_REQUIRED": "no review",
    "": "no review",
    None: "no review",
}


def _run(argv: list[str], cwd: Path, env: dict[str, str], timeout: int = 20) -> str | None:
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _checks(rollup: object) -> str | None:
    if not isinstance(rollup, list):
        return None
    if not rollup:
        return "No checks"
    failing = running = False
    for item in rollup:
        if not isinstance(item, dict):
            return None
        # A check run has `status` and `conclusion`; a commit status has `state`.
        state = item.get("conclusion") or item.get("state") or item.get("status")
        if not isinstance(state, str):
            return None
        state = state.upper()
        if state in _GREEN:
            continue
        if state in _RUNNING or state == "COMPLETED":
            running = True
        else:
            failing = True
    if failing:
        return "Checks failing"
    if running:
        return "Checks still running"
    return "Checks green"


def merge_status(number: str, repo: str, pin: str, *, command: tuple[str, ...], cwd: Path,
                 env: dict[str, str], run=_run) -> str:
    out = run([*command, number, "--repo", repo, "--json",
               "reviewDecision,statusCheckRollup,headRefOid"], cwd, env)
    if out is None:
        return STATUS_UNKNOWN
    try:
        data = json.loads(out)
        head = data["headRefOid"]
        decision = data["reviewDecision"]
        rollup = data["statusCheckRollup"]
    except (ValueError, KeyError, TypeError):
        return STATUS_UNKNOWN
    if not isinstance(head, str) or not head:
        return STATUS_UNKNOWN
    if head.lower() != pin.lower():
        return MOVED
    checks = _checks(rollup)
    try:
        review = _REVIEWS.get(decision)
    except TypeError:
        review = None
    if checks is None or review is None:
        return STATUS_UNKNOWN
    return f"{checks}, {review}."


def push_status(remote: str, pairs: tuple, is_default, *, cwd: Path,
                env: dict[str, str], run=_run) -> str:
    """For a push that lands on a default branch: the number of commits the
    remote does not have yet, and that nobody reviewed them. `is_default`
    is the classifier's own test of a destination (agent_host.classify), so
    the read-back and the verdict cannot disagree about what a default
    branch is."""
    count = None
    for source, destination in pairs:
        if not is_default(destination):
            continue
        branch = destination
        for prefix in ("refs/heads/", "heads/"):
            if branch.startswith(prefix):
                branch = branch[len(prefix):]
                break
        out = run(["git", "rev-list", "--count", f"refs/remotes/{remote}/{branch}..{source}", "--"],
                  cwd, env)
        try:
            count = (count or 0) + int((out or "").strip())
        except ValueError:
            return "An unknown number of commits, no review."
    if count is None:
        return "An unknown number of commits, no review."
    return f"{count} commit{'' if count == 1 else 's'}, no review."
