#!/bin/sh
# Clone or refresh the projects the bridge may answer about, as shallow,
# read-only working copies (run-bridge-in-cluster, D5).
#
#   JARVIS_SYNC_REPOS  space-separated owner/name list (GitHub), required
#   JARVIS_SYNC_DIR    where the copies live, one directory per repo name (default /work)
#   GIT_PAT            optional token for private repos; read from the environment by a
#                      credential helper at run time, never written to disk or argv
#
# A repo that cannot be refreshed keeps its previous copy (warning). A repo with
# no copy at all fails the run, so a broken setup is visible instead of a 502 later.
set -eu
export GIT_TERMINAL_PROMPT=0   # never hang on a prompt in a pod

: "${JARVIS_SYNC_REPOS:?JARVIS_SYNC_REPOS is required (owner/name ...)}"
dir="${JARVIS_SYNC_DIR:-/work}"
mkdir -p "$dir"

# The helper is a literal string; git's shell expands ${GIT_PAT} when it runs.
# shellcheck disable=SC2016
helper='!f() { test "$1" = get || exit 0; echo username=x-access-token; echo "password=${GIT_PAT}"; }; f'

git_() {
  if [ -n "${GIT_PAT:-}" ]; then
    git -c credential.helper= -c "credential.helper=$helper" "$@"
  else
    git "$@"
  fi
}

failed=0
for repo in $JARVIS_SYNC_REPOS; do
  name="${repo##*/}"
  target="$dir/$name"
  url="https://github.com/$repo.git"
  if [ -d "$target/.git" ]; then
    if git_ -C "$target" fetch -q --depth 1 origin HEAD \
       && git -C "$target" reset -q --hard FETCH_HEAD \
       && git -C "$target" clean -qffdx; then
      echo "sync: $repo refreshed to $(git -C "$target" rev-parse --short HEAD)"
    else
      echo "sync: WARNING $repo could not be refreshed; keeping $(git -C "$target" rev-parse --short HEAD)" >&2
    fi
  else
    rm -rf "$target"
    if git_ clone -q --depth 1 "$url" "$target"; then
      echo "sync: $repo cloned at $(git -C "$target" rev-parse --short HEAD)"
    else
      echo "sync: ERROR $repo could not be cloned and has no previous copy" >&2
      failed=1
    fi
  fi
done
exit "$failed"
