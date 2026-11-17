#!/usr/bin/env bash
# Installs the Jarvis agent host side (jarvis-full-agent, task 1.15, design D32-D36).
#
#   sudo agent_host/install.sh [--dry-run] [--memory-dir DIR] [--pubkey FILE --from ADDRESS]
#   agent_host/install.sh --print-authorized-keys --pubkey FILE --from ADDRESS
#
# What it writes, all root-owned:
#   /usr/local/lib/jarvis-agent/   the wrapper (turn), the gate, the confirm switch, the
#                                  action log hook, the rules, the prompt, the turn settings
#   /etc/jarvis-agent/             config.toml (only when absent), config.toml.example,
#                                  environment (only when absent), an empty rules.d/
#
# What it never does: edit any authorized_keys file (it prints the line), touch Claude
# Code's managed or user settings, or write below a home directory.
#
# DESTDIR (or --destdir DIR) prefixes every path written, as in `make install`. That is
# how CI tests it with a fake root; the paths *inside* the installed files stay the real
# ones. docs/how-to/agent-host.md is the guide.
set -euo pipefail

LIB_DIR=/usr/local/lib/jarvis-agent
# The one interpreter, for this script and for what it installs: a fixed path, so that a
# PATH from anywhere cannot choose another. Installed entry points get the shebang
# "#!$PYTHON -I" (isolated: no PYTHONPATH, no PYTHONSTARTUP, no user site directory).
PYTHON=/usr/bin/python3
ETC_DIR=/etc/jarvis-agent
FORCED_COMMAND="$LIB_DIR/turn"

# Entry points: executables in agent_host/ that get a fixed path directly under LIB_DIR.
# `turn` is phase 1a; `gate`, `confirm` and `action-log` are phase 1b. The installer
# refuses to install without any of them: turn settings that name a gate that is not
# there would let every call through.
ENTRY_POINTS=(turn gate confirm action-log)
# Data files that get a fixed path directly under LIB_DIR as well.
DATA_FILES=(gate_rules.toml prompt.md)
# The modules of bridge/ the wrapper imports: the marker line of the spoken summary, and
# what both sides share of the wire contract.
BRIDGE_FILES=(__init__.py contract.py summary.py)
# Files in agent_host/ that are not part of what runs on the host.
NOT_INSTALLED=(install.sh config.example.toml turn-settings.json)

usage() {
    sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'
    cat <<'EOF'

  --dry-run               say what would be done, write nothing
  --destdir DIR           prefix for every path written (also: DESTDIR in the environment)
  --memory-dir DIR        the memory directory of the coding session, written into the turn
                          settings as autoMemoryDirectory (design D34). Without it, a value
                          already installed is kept
  --pubkey FILE           the bridge's public key (one line, as ssh-keygen writes it)
  --from ADDRESS          the one address the bridge's connections come from
  --print-authorized-keys print the authorized_keys line and do nothing else (no root needed)
EOF
}

die() { echo "install.sh: $*" >&2; exit 1; }

is_in() {
    local needle=$1 x; shift
    for x in "$@"; do [ "$x" = "$needle" ] && return 0; done
    return 1
}

dry_run=0
print_only=0
destdir=${DESTDIR:-}
memory_dir=
pubkey_file=
from_addr=

while [ $# -gt 0 ]; do
    case $1 in
        --dry-run) dry_run=1 ;;
        --print-authorized-keys) print_only=1 ;;
        --destdir) [ $# -ge 2 ] || die "--destdir needs a directory"; destdir=$2; shift ;;
        --memory-dir) [ $# -ge 2 ] || die "--memory-dir needs a directory"; memory_dir=$2; shift ;;
        --pubkey) [ $# -ge 2 ] || die "--pubkey needs a file"; pubkey_file=$2; shift ;;
        --from) [ $# -ge 2 ] || die "--from needs an address"; from_addr=$2; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown argument: $1" ;;
    esac
    shift
done

[ -x "$PYTHON" ] || die "$PYTHON not found"
"$PYTHON" -I -c 'import sys, tomllib; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null \
    || die "$PYTHON must be Python 3.11 or newer (the wrapper reads its configuration with tomllib)"
# The gate reads agent files with PyYAML (#57, D58). Without it every subagent
# is refused by voice: said here, not found out in a turn.
"$PYTHON" -I -c 'import yaml' 2>/dev/null \
    || echo "install.sh: warning: $PYTHON has no PyYAML (python3-yaml): the gate will refuse every subagent by voice" >&2

# --- the authorized_keys line (D32) -------------------------------------------------

# Prints: restrict,from="<address>",command="<LIB_DIR>/turn" <type> <key> <comment>
# The key file must hold exactly one bare public key: no options in front of it, so a
# file that already carries `command=` or `from=` cannot smuggle in a second set.
authorized_keys_line() {
    local file=$1 addr=$2 lines type blob comment
    [ -f "$file" ] || die "public key file not found: $file"
    lines=$(grep -c -v -E '^[[:space:]]*(#|$)' "$file" || true)
    [ "$lines" = 1 ] || die "$file must hold exactly one public key (found $lines lines)"
    read -r type blob comment < <(grep -v -E '^[[:space:]]*(#|$)' "$file") || true
    [[ $type =~ ^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)$ ]] \
        || die "$file does not start with a public key type (options in front of the key are refused)"
    [[ $blob =~ ^[A-Za-z0-9+/]{32,}={0,3}$ ]] || die "$file: the key is not base64"
    # The comment is free text in the key file; only a plain label survives.
    [[ ${comment:-} =~ ^[A-Za-z0-9@._-]{1,64}$ ]] || comment=jarvis-bridge
    # Exactly one IPv4 or IPv6 address (a /32 or /128 suffix is accepted and dropped).
    # No network, no host name, no pattern, no list: `from=` is what makes the key
    # useless from anywhere else, and a name would hand that to whoever answers DNS.
    addr=$(one_address "$addr") \
        || die "--from must be exactly one IPv4 or IPv6 address (no network, host name, wildcard or list)"
    printf 'restrict,from="%s",command="%s" %s %s %s\n' "$addr" "$FORCED_COMMAND" "$type" "$blob" "$comment"
}

# Prints the address in canonical form, or fails. Strict by construction: the standard
# library parses it, and only the full-length prefix of its family is accepted.
one_address() {
    "$PYTHON" -I - "$1" <<'PY'
import ipaddress
import sys

text = sys.argv[1]
address, slash, prefix = text.partition("/")
try:
    parsed = ipaddress.ip_address(address)
except ValueError:
    sys.exit(1)
if "%" in address or address != address.strip():
    sys.exit(1)
if slash and prefix != str(parsed.max_prefixlen):
    sys.exit(1)
if parsed.is_unspecified:
    sys.exit(1)
print(parsed.compressed)
PY
}

if [ -n "$pubkey_file" ] || [ -n "$from_addr" ]; then
    if [ -z "$pubkey_file" ] || [ -z "$from_addr" ]; then die "--pubkey and --from go together"; fi
    key_line=$(authorized_keys_line "$pubkey_file" "$from_addr")
else
    key_line=
fi

if [ "$print_only" = 1 ]; then
    [ -n "$key_line" ] || die "--print-authorized-keys needs --pubkey FILE and --from ADDRESS"
    printf '%s\n' "$key_line"
    exit 0
fi

# --- checks before anything is written ----------------------------------------------

src_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(dirname "$src_dir")

# Only what git tracks is installed, at the state of HEAD: no untracked file, no
# uncommitted edit. So VERSION names exactly what runs. The checkout may belong to another
# user than the one running this (root), hence safe.directory; nothing in the checkout's
# own git configuration may run a program for these read-only commands.
command -v git >/dev/null || die "git not found"
repo_git() {
    git -c safe.directory="$repo_dir" -c core.fsmonitor=false -c core.hooksPath=/dev/null \
        -C "$repo_dir" "$@"
}
bridge_paths=()
for f in "${BRIDGE_FILES[@]}"; do bridge_paths+=("bridge/$f"); done
version=$(repo_git rev-parse --verify HEAD 2>/dev/null) \
    || die "$repo_dir is not a git checkout with a commit; install from a clone of the repository"
if [ -n "$(repo_git status --porcelain --untracked-files=no -- agent_host "${bridge_paths[@]}")" ]; then
    die "uncommitted changes under agent_host/ or in bridge/: install from a clean checkout of a reviewed commit"
fi
tracked=()
while IFS= read -r -d '' rel; do
    tracked+=("$rel")
    if [ -L "$repo_dir/$rel" ] || [ ! -f "$repo_dir/$rel" ]; then
        die "$rel is not a regular file; refusing to install it"
    fi
done < <(repo_git ls-files -z -- agent_host "${bridge_paths[@]}")

missing=()
for f in "${ENTRY_POINTS[@]}" "${DATA_FILES[@]}" turn-settings.json config.example.toml __init__.py; do
    is_in "agent_host/$f" "${tracked[@]}" || missing+=("agent_host/$f")
done
for f in "${bridge_paths[@]}"; do
    is_in "$f" "${tracked[@]}" || missing+=("$f")
done
if [ ${#missing[@]} -gt 0 ]; then
    die "not installing, this checkout lacks: ${missing[*]} (the gate and its parts are phase 1b of jarvis-full-agent; without them a turn would run ungated)"
fi

if [ -n "$memory_dir" ]; then
    [[ $memory_dir == /* ]] || die "--memory-dir must be an absolute path"
fi
if [ -n "$destdir" ]; then
    [[ $destdir == /* ]] || die "the destination prefix must be an absolute path"
    destdir=${destdir%/}
fi

own=()
if [ "$(id -u)" = 0 ]; then
    own=(-o root -g root)
elif [ -z "$destdir" ]; then
    [ "$dry_run" = 1 ] || die "run as root (sudo), or set DESTDIR to stage the tree somewhere else"
else
    echo "note: not root, so the staged tree under $destdir is owned by $(id -un), not by root"
fi

lib=$destdir$LIB_DIR
etc=$destdir$ETC_DIR

say() { echo "  $*"; }
run() {
    if [ "$dry_run" = 1 ]; then say "would run: $*"; else "$@"; fi
}

if [ "$dry_run" = 1 ]; then echo "Dry run: nothing is written."; fi
echo "Installing the Jarvis agent host side from $repo_dir"

# --- /usr/local/lib/jarvis-agent ----------------------------------------------------

# The code lives in <lib>/src as the packages `agent_host` and `bridge`, the layout the
# entry points expect (they put the directory two levels above their own real path on
# sys.path). The fixed paths the design names (<lib>/turn, <lib>/gate, ...) are symlinks
# into it. The new tree is staged next to the old one and swapped in, so an update never
# leaves a half-copied package behind.
run install -d -m 0755 "${own[@]}" "$lib"
stage=$lib/src.new
run rm -rf "$stage" "$lib/src.old"
run install -d -m 0755 "${own[@]}" "$stage" "$stage/agent_host" "$stage/bridge"

for tracked_path in "${tracked[@]}"; do
    case $tracked_path in
        agent_host/*) rel=${tracked_path#agent_host/} ;;
        *) run install -m 0644 "${own[@]}" "$repo_dir/$tracked_path" "$stage/$tracked_path"; continue ;;
    esac
    is_in "$rel" "${NOT_INSTALLED[@]}" && continue
    path=$src_dir/$rel
    mode=0644
    if [ -x "$path" ] || is_in "$rel" "${ENTRY_POINTS[@]}"; then mode=0755; fi
    if [ "$(dirname "$rel")" != . ]; then
        run install -d -m 0755 "${own[@]}" "$stage/agent_host/$(dirname "$rel")"
    fi
    run install -m "$mode" "${own[@]}" "$path" "$stage/agent_host/$rel"
    # A Python entry point runs under the fixed interpreter in isolated mode, whatever
    # its shebang says in the repository (there it is `env python3`, for the tests).
    if is_in "$rel" "${ENTRY_POINTS[@]}" && head -n 1 "$path" | grep -q '^#!.*python'; then
        run sed -i "1s|.*|#!$PYTHON -I|" "$stage/agent_host/$rel"
    fi
done

if [ -d "$lib/src" ]; then run mv -T "$lib/src" "$lib/src.old"; fi
run mv -T "$stage" "$lib/src"
run rm -rf "$lib/src.old"

for f in "${ENTRY_POINTS[@]}" "${DATA_FILES[@]}"; do
    run ln -sfn "src/agent_host/$f" "$lib/$f"
    if [ ${#own[@]} -gt 0 ]; then run chown -h root:root "$lib/$f"; fi
done

# The turn settings: the static file of the repository, plus the one value that differs
# per host (the coding session's memory directory, D34). Written here and nowhere else.
installed_settings=$lib/turn-settings.json
settings_json=$("$PYTHON" -I - "$src_dir/turn-settings.json" "$installed_settings" "$memory_dir" "$etc/environment" <<'PY'
import json
import sys

template_path, installed_path, memory_dir, environment_path = sys.argv[1:5]
with open(template_path) as fh:
    settings = json.load(fh)
if settings.get("disableAllHooks") is not False or "PreToolUse" not in settings.get("hooks", {}):
    sys.exit("turn-settings.json must set disableAllHooks to false and name the gate")
# PATH and HOME are pinned to the values of the environment file: env in
# --settings outranks a checkout's env (design D36), so a project cannot
# change them for the hooks and the tools of a turn. Per host, so here.
try:
    with open(environment_path) as fh:
        lines = fh.read().splitlines()
except OSError:
    lines = []
for line in lines:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, _, value = line.partition("=")
    if key.strip() in ("PATH", "HOME"):
        settings.setdefault("env", {})[key.strip()] = value.strip()
if not memory_dir:
    try:
        with open(installed_path) as fh:
            memory_dir = json.load(fh).get("autoMemoryDirectory", "")
    except (OSError, ValueError, AttributeError):
        memory_dir = ""
if memory_dir:
    settings["autoMemoryDirectory"] = memory_dir
print(json.dumps(settings, indent=2, sort_keys=True))
PY
)
if [ "$dry_run" = 1 ]; then
    say "would write $installed_settings"
else
    tmp=$(mktemp "$lib/.turn-settings.XXXXXX")
    printf '%s\n' "$settings_json" > "$tmp"
    chmod 0644 "$tmp"
    if [ ${#own[@]} -gt 0 ]; then chown root:root "$tmp"; fi
    mv -f "$tmp" "$installed_settings"
fi

if [ "$dry_run" = 1 ]; then
    say "would write $lib/VERSION ($version)"
else
    printf '%s\n' "$version" > "$lib/VERSION"
    chmod 0644 "$lib/VERSION"
    if [ ${#own[@]} -gt 0 ]; then chown root:root "$lib/VERSION"; fi
fi

# --- /etc/jarvis-agent --------------------------------------------------------------

run install -d -m 0755 "${own[@]}" "$etc" "$etc/rules.d"
run install -m 0644 "${own[@]}" "$src_dir/config.example.toml" "$etc/config.toml.example"

if [ -e "$etc/config.toml" ]; then
    say "kept $etc/config.toml (compare with config.toml.example after an update)"
else
    run install -m 0644 "${own[@]}" "$src_dir/config.example.toml" "$etc/config.toml"
    say "wrote $etc/config.toml from the template: the kill switch is set (enabled = false)"
fi

if [ -e "$etc/environment" ]; then
    say "kept $etc/environment"
elif [ "$dry_run" = 1 ]; then
    say "would write $etc/environment"
else
    tmp=$(mktemp "$etc/.environment.XXXXXX")
    cat > "$tmp" <<'EOF'
# The only environment a Jarvis turn starts from (design D33), one KEY=value per line,
# no quoting, no expansion. Root-owned and world-readable: no secrets in here.
# HOME and PATH must be those of the coding session's user, so that the turn finds the
# same `claude`, `gh`, `git` and credentials. Fill in before enabling full mode.
PATH=/usr/local/bin:/usr/bin:/bin
# HOME=/home/CHANGE-ME
# USER=CHANGE-ME
# LOGNAME=CHANGE-ME
EOF
    chmod 0644 "$tmp"
    if [ ${#own[@]} -gt 0 ]; then chown root:root "$tmp"; fi
    mv -f "$tmp" "$etc/environment"
    say "wrote $etc/environment (fill in HOME and PATH)"
fi

# --- what is left for a person ------------------------------------------------------

echo
if [ "$dry_run" = 1 ]; then echo "Dry run done: nothing was written."; else echo "Installed ($version)."; fi
if ! grep -q autoMemoryDirectory <<<"$settings_json"; then
    echo "note: no memory directory in the turn settings; run again with --memory-dir DIR,"
    echo "      or a turn starts with an empty memory (design D34)."
fi
echo "Not done by this script, on purpose:"
echo "  - authorized_keys is not edited. Add the line yourself to the coding session"
echo "    user's ~/.ssh/authorized_keys:"
if [ -n "$key_line" ]; then
    echo
    printf '%s\n' "$key_line"
    echo
else
    echo "      $0 --print-authorized-keys --pubkey FILE --from ADDRESS"
fi
echo "  - Claude Code's managed and user settings are not touched."
echo "  - full mode stays off: enabled = false in $ETC_DIR/config.toml until the host"
echo "    checks have passed (docs/how-to/agent-host.md)."
