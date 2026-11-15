#!/bin/sh
# Clone or refresh the projects the bridge may answer about, as shallow,
# read-only working copies (run-bridge-in-cluster D5; add-project-list).
#
#   JARVIS_SYNC_REPOS    space-separated owner/name list (GitHub), or '*' for every
#                        repository JARVIS_SYNC_OWNER owns (see below); required
#   JARVIS_SYNC_DIR      where the copies live, one directory per repo name (default /work)
#   GIT_PAT              token for private repos (required with '*'); read from the
#                        environment by a credential helper at run time, never written
#                        to disk or argv
#
# With '*' only:
#   JARVIS_SYNC_OWNER    the GitHub account; only repositories it owns are listed
#                        (GET /user/repos?affiliation=owner, filtered on the owner's
#                        login): never an organisation's, whatever the token can read
#   JARVIS_SYNC_MAX_KB   skip repositories larger than this, as the API counts them
#                        (default 512000, 500 MB)
#   JARVIS_SYNC_EXCLUDE  names (or owner/name) never to sync, space- or comma-separated
#   Archived repositories and forks are skipped. Every skip is logged.
#
# Failure semantics. A repo that cannot be refreshed keeps its previous copy
# (warning). With an explicit list, a repo with no copy at all fails the run, so a
# broken setup is visible instead of a 502 later. With '*', such a repo is logged and
# skipped, and the run fails only when no repository is available at all. When the
# list itself cannot be read, the copies already there are refreshed and kept. When
# it can, a copy whose repository is no longer on it (archived, excluded, gone) is
# removed, so the bridge stops offering it.
set -eu
export GIT_TERMINAL_PROMPT=0   # never hang on a prompt in a pod

: "${JARVIS_SYNC_REPOS:?JARVIS_SYNC_REPOS is required (owner/name ..., or *)}"
dir="${JARVIS_SYNC_DIR:-/work}"
# Test hooks: where the repositories are cloned from, and the program that lists them.
base="${JARVIS_SYNC_BASE_URL:-https://github.com}"
lister="${JARVIS_SYNC_LISTER:-jarvis-list-repos}"
mkdir -p "$dir"

# The helper is a literal string; git's shell expands ${GIT_PAT} when it runs.
# shellcheck disable=SC2016
helper='!f() { test "$1" = get || exit 0; echo username=x-access-token; echo "password=${GIT_PAT}"; }; f'

git_() {
  # A transfer slower than 1 KB/s for a minute is given up, so one stuck repo
  # cannot hold the init container forever.
  if [ -n "${GIT_PAT:-}" ]; then
    git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=60 \
        -c credential.helper= -c "credential.helper=$helper" "$@"
  else
    git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=60 "$@"
  fi
}

# sync_one owner/name: clone or refresh it. Succeeds when a copy exists afterwards.
sync_one() {
  repo="$1"
  name="${repo##*/}"
  target="$dir/$name"
  if [ -d "$target/.git" ]; then
    if git_ -C "$target" fetch -q --depth 1 origin HEAD \
       && git -C "$target" reset -q --hard FETCH_HEAD \
       && git -C "$target" clean -qffdx; then
      echo "sync: $repo refreshed to $(git -C "$target" rev-parse --short HEAD)"
    else
      echo "sync: WARNING $repo could not be refreshed; keeping $(git -C "$target" rev-parse --short HEAD)" >&2
    fi
    return 0
  fi
  rm -rf "$target"
  if git_ clone -q --depth 1 "$base/$repo.git" "$target"; then
    echo "sync: $repo cloned at $(git -C "$target" rev-parse --short HEAD)"
    return 0
  fi
  rm -rf "$target"
  echo "sync: ERROR $repo could not be cloned and has no previous copy" >&2
  return 1
}

if [ "$JARVIS_SYNC_REPOS" != "*" ]; then
  failed=0
  for repo in $JARVIS_SYNC_REPOS; do
    sync_one "$repo" || failed=1
  done
  exit "$failed"
fi

available=0
if list=$("$lister") && [ -n "$list" ]; then
  names=" "
  for repo in $list; do
    names="$names${repo##*/} "
    if sync_one "$repo"; then available=$((available + 1)); fi
  done
  for git_dir in "$dir"/*/.git; do
    [ -e "$git_dir" ] || continue
    copy="${git_dir%/.git}"
    case "$names" in
      *" ${copy##*/} "*) ;;
      *) rm -rf "$copy"; echo "sync: removed ${copy##*/}: no longer on the list" ;;
    esac
  done
else
  echo "sync: WARNING the repository list could not be read; refreshing the copies already here" >&2
  for git_dir in "$dir"/*/.git; do
    [ -e "$git_dir" ] || continue
    copy="${git_dir%/.git}"
    if sync_one "kept/${copy##*/}"; then available=$((available + 1)); fi
  done
fi

if [ "$available" -eq 0 ]; then
  echo "sync: ERROR no repository is available" >&2
  exit 1
fi
echo "sync: $available repositories available in $dir"
