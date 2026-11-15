# Tasks: add-project-list

## 1. Bridge
- [x] 1.1 `GET /projects` in `bridge/server.py`: route, token, drain on refusal, sorted names
- [x] 1.2 `JARVIS_PROJECTS=*` with `JARVIS_PROJECTS_ROOT` in `bridge/config.py`: git
  checkouts directly under the root, no hidden directories or symlinks, 10 s cache
- [x] 1.3 Tests: the route (token, wrong token, non-ASCII token, other paths, a large refused
  body, a body on an authorized GET, the wildcard's rescan) and the config

## 2. Project sync
- [x] 2.1 `deploy/list_repos.py` (`jarvis-list-repos` in the image): the owner's repositories
  from `GET /user/repos?affiliation=owner`, paginated; owner, archived, fork, exclude and
  size filters; every skip logged; nothing printed on a failure
- [x] 2.2 `deploy/sync-projects.sh` with `JARVIS_SYNC_REPOS='*'`: per-repo failure logged,
  fail only with zero repositories, keep copies when the list fails, remove copies that
  left the list; explicit lists unchanged
- [x] 2.3 Tests against local git repositories with a fake lister; the lister's filters and
  paging with a fake API
- [x] 2.4 Docs: `bridge-service.md`, `run-in-cluster.md`

## 3. App (tracked in `add-android-client`, section 6)

## 4. Deployment
- [ ] 4.1 Image from main pinned in the cluster; `JARVIS_SYNC_REPOS='*'`,
  `JARVIS_SYNC_OWNER`, `JARVIS_PROJECTS=*`, `JARVIS_PROJECTS_ROOT=/work` (homelab PR)
- [ ] 4.2 Verified: the init log lists the synced and skipped repositories; `GET /projects`
  with the token lists them, without it gives 401
- [ ] 4.3 Archive this change
