"""Project resolution (1.2, D33).

The bridge never sends a path, only a name: the wrapper resolves it itself,
against an explicit list in the configuration or against the directories
directly under a configured root, minus an exclusion list. A name that does
not fit the pattern, a symlink, a directory outside the root, or a git
repository without a work tree (bare) are all refused the same way: there is
no checkout to run a turn in.
"""
from __future__ import annotations

import sys
from pathlib import Path

from agent_host.config import AgentHostConfig
from bridge.contract import MAX_LISTED, PROJECT_NAME

# One definition for both sides of the wire (bridge/contract.py).
_PROJECT_NAME = PROJECT_NAME


class ProjectError(ValueError):
    """Raised when a project name cannot be resolved to a usable checkout."""


def _has_work_tree(path: Path) -> bool:
    # A normal clone or worktree has a .git entry (directory, or a file
    # pointing at the real git dir for `git worktree add`); a bare
    # repository has none - its HEAD, objects/ and refs/ sit at the top
    # level instead. That is exactly the "work tree, bare refused" test.
    return (path / ".git").exists()


def resolve_project(config: AgentHostConfig, name: str) -> Path:
    if not _PROJECT_NAME.fullmatch(name):
        raise ProjectError(f"project name {name!r} does not match the allowed pattern")

    if config.projects:
        try:
            candidate = config.projects[name]
        except KeyError:
            raise ProjectError(f"project {name!r} is not on the configured list") from None
        if not candidate.is_dir() or candidate.is_symlink():
            raise ProjectError(f"project {name!r} is not a directory")
        if not _has_work_tree(candidate):
            raise ProjectError(f"project {name!r} has no work tree (bare repository refused)")
        return candidate.resolve()

    if name in config.project_exclusions:
        raise ProjectError(f"project {name!r} is excluded")

    root = config.projects_root
    assert root is not None  # config.config_from_dict enforces exactly one of the two
    candidate = root / name
    try:
        if candidate.is_symlink() or not candidate.is_dir():
            raise ProjectError(f"project {name!r} is not a directory under the root")
    except OSError as exc:
        raise ProjectError(f"project {name!r} could not be checked: {exc}") from exc

    try:
        real_root = root.resolve()
        real_candidate = candidate.resolve()
    except OSError as exc:
        raise ProjectError(f"project {name!r} could not be resolved: {exc}") from exc
    if real_candidate.parent != real_root:
        raise ProjectError(f"project {name!r} does not resolve directly under the root")

    if not _has_work_tree(real_candidate):
        raise ProjectError(f"project {name!r} has no work tree (bare repository refused)")

    return real_candidate



class ListError(OSError):
    """The project root cannot be listed at all (D51): an error reply, never an
    empty list, so the bridge falls back to its own and says why."""


def project_name_ok(name: str) -> bool:
    return bool(_PROJECT_NAME.fullmatch(name))


def list_projects(config: AgentHostConfig) -> list[str]:
    """Every name a turn would accept, sorted, at most MAX_LISTED (D51).

    Each candidate goes through `resolve_project`, the turn's own resolver,
    so a listed name is always a name a turn accepts, and nothing that a
    turn refuses (excluded, bare, a symlink out, not a directory) is named.
    An entry that cannot even be looked at (permissions) is skipped and said
    on stderr; a root that cannot be listed raises ListError. Reads
    directory names only.
    """
    if config.projects:
        candidates = list(config.projects)
    else:
        root = config.projects_root
        assert root is not None  # config.config_from_dict enforces exactly one of the two
        try:
            candidates = [entry.name for entry in root.iterdir()]
        except OSError as exc:
            raise ListError(f"the project root cannot be listed: {exc.strerror or exc}") from exc
    names: list[str] = []
    for name in sorted(candidates):
        try:
            resolve_project(config, name)
        except ProjectError:
            continue
        except OSError as exc:
            print(f"jarvis-agent: project {name!r} skipped in the list: {exc.strerror or exc}",
                  file=sys.stderr)
            continue
        names.append(name)
        if len(names) == MAX_LISTED:
            break
    return names
