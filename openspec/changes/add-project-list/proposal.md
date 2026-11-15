# Change: add-project-list

## Why

Mark (2026-11-15), about the app: "jarvis mag alle projecten. kan dat met blank or *"
(Jarvis may have every project; can that be a blank field or `*`). Today the phone needs
the project names typed in its settings, the bridge has no route that lists them, and the
cluster deployment clones a fixed list of six repositories that is kept in two places.

Mark asked for this directly, so it is built in the same PR as the proposal; the PR is the
approval point.

## What Changes

- **`GET /projects` on the bridge:** `{"projects": ["a", "b"]}`, the names sorted, never
  paths. The same gate as `/turn`: the route, then the token in constant time, and a refused
  request drains an announced body before it answers.
- **`JARVIS_PROJECTS=*` with `JARVIS_PROJECTS_ROOT`:** every git checkout directly under the
  root is a project, named after its directory. Hidden directories and symlinks are
  skipped, so a name never leads out of the root. Re-scanned at most every 10 seconds.
- **Project sync for every repository of one owner:** `JARVIS_SYNC_REPOS='*'` with
  `JARVIS_SYNC_OWNER` lists the repositories through the GitHub API
  (`GET /user/repos?affiliation=owner`, paginated, with `GIT_PAT`), keeps only those whose
  owner is that account (never an organisation's), skips archived repositories, forks,
  names in `JARVIS_SYNC_EXCLUDE` and repositories over `JARVIS_SYNC_MAX_KB` (500 MB), and
  logs every skip. Copies stay shallow. A first clone that fails is logged and skipped; the
  run fails only when no repository is available. When the list cannot be read, the copies
  already there are refreshed and kept; when it can, a copy that left the list is removed.
- **The app** (in the change `add-android-client`, which is still open): an empty projects
  field or `*` means "ask the bridge"; the list is fetched at app start and with a refresh
  button, with the same client as a turn.

## Impact

- Modified capability: `voice-bridge`, a new requirement Project list.
- Affected code: `bridge/config.py`, `bridge/server.py`, `deploy/sync-projects.sh`, new
  `deploy/list_repos.py`, `deploy/Dockerfile`; the app's `core` and `app` modules.
- Nothing changes for a bridge with an explicit `JARVIS_PROJECTS` list or a sync with an
  explicit `JARVIS_SYNC_REPOS` list, except that `GET /projects` now exists.
- Exposure: the route tells a caller with the token which project names exist; a caller
  with the token could already ask a turn about any of them. Without the token it says
  nothing (401).
