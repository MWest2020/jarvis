#!/usr/bin/env python3
"""List the GitHub repositories one owner has, for `jarvis-sync-projects` with
JARVIS_SYNC_REPOS='*' (add-project-list).

    GET https://api.github.com/user/repos?affiliation=owner   (paginated, with GIT_PAT)

Prints `owner/name` per line on stdout, sorted. Kept: repositories whose owner is
exactly JARVIS_SYNC_OWNER (no organisations, whatever the token can see), that are
neither archived nor forks, not in JARVIS_SYNC_EXCLUDE, and not larger than
JARVIS_SYNC_MAX_KB (the API's `size`, in KB). Every skip is logged on stderr.

Exits non-zero, printing nothing on stdout, when the list cannot be read in full:
the caller then keeps the copies it has instead of acting on half a list.

Standard library only; the token goes in a header and is never printed.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable

API = "https://api.github.com"
DEFAULT_MAX_KB = 500 * 1024          # 500 MB, as the API counts a repository's size
MAX_PAGES = 50                       # 5000 repositories; a loop guard, not a limit anyone hits
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


class ListError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"sync: {message}", file=sys.stderr)


def _get(url: str, token: str, timeout: float = 30.0) -> tuple[list, str | None]:
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "jarvis-sync-projects",
    })
    # Never copied into a redirect (urllib copies normal headers to any 3xx target).
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            page = json.load(response)
            link = response.headers.get("Link") or ""
    except urllib.error.HTTPError as exc:
        raise ListError(f"GitHub answered {exc.code} for the repository list") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ListError(f"the repository list could not be read: {exc.__class__.__name__}") from None
    if not isinstance(page, list):
        raise ListError("the repository list is not a JSON array")
    match = _NEXT.search(link)
    return page, match.group(1) if match else None


def fetch_all(token: str, *, get: Callable[[str, str], tuple[list, str | None]] = _get,
              api: str = API) -> list[dict]:
    url: str | None = f"{api}/user/repos?affiliation=owner&per_page=100"
    repos: list[dict] = []
    for _ in range(MAX_PAGES):
        if url is None:
            return repos
        if not url.startswith(api + "/"):
            # The next-page link must stay on the API host: the token goes with it.
            raise ListError("the next-page link points away from the API")
        page, url = get(url, token)
        repos.extend(r for r in page if isinstance(r, dict))
    raise ListError(f"more than {MAX_PAGES} pages of repositories")


def select(repos: Iterable[dict], owner: str, *, max_kb: int,
           exclude: Iterable[str] = ()) -> list[str]:
    excluded = {e.lower() for e in exclude}
    kept: set[str] = set()
    for repo in repos:
        login = (repo.get("owner") or {}).get("login") or ""
        name = repo.get("name") or ""
        if login.lower() != owner.lower():
            continue   # another owner (an organisation): never cloned, not even logged by name
        if not _NAME.fullmatch(name):
            log(f"skip a repository with an unusable name {name!r}")
            continue
        full = f"{login}/{name}"
        if repo.get("archived"):
            log(f"skip {full}: archived")
        elif repo.get("fork"):
            log(f"skip {full}: a fork")
        elif name.lower() in excluded or full.lower() in excluded:
            log(f"skip {full}: in JARVIS_SYNC_EXCLUDE")
        elif int(repo.get("size") or 0) > max_kb:
            log(f"skip {full}: {int(repo['size']) // 1024} MB is over the limit of {max_kb // 1024} MB")
        else:
            kept.add(full)
    return sorted(kept, key=str.lower)


def main(argv: list[str] | None = None) -> int:
    owner = os.environ.get("JARVIS_SYNC_OWNER", "").strip()
    token = os.environ.get("GIT_PAT", "")
    if not _NAME.fullmatch(owner):
        log("ERROR JARVIS_SYNC_OWNER (the GitHub account whose repositories to sync) is required")
        return 2
    if not token:
        log("ERROR GIT_PAT is required to list the repositories")
        return 2
    try:
        max_kb = int(os.environ.get("JARVIS_SYNC_MAX_KB") or DEFAULT_MAX_KB)
    except ValueError:
        log("ERROR JARVIS_SYNC_MAX_KB must be a number of KB")
        return 2
    exclude = os.environ.get("JARVIS_SYNC_EXCLUDE", "").replace(",", " ").split()
    try:
        repos = fetch_all(token)
    except ListError as exc:
        log(f"ERROR {exc}")
        return 1
    names = select(repos, owner, max_kb=max_kb, exclude=exclude)
    chosen = set(names)
    total_kb = sum(int(r.get("size") or 0) for r in repos
                   if f"{(r.get('owner') or {}).get('login')}/{r.get('name')}" in chosen)
    log(f"{len(names)} repositories of {owner} to sync, {total_kb // 1024} MB as GitHub counts them")
    sys.stdout.write("".join(f"{n}\n" for n in names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
