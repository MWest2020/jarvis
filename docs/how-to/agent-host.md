---
status: current
last_reviewed: 2026-11-16
---
# The agent host: installing the side that runs `full` turns

In `full` mode a Jarvis turn does not run in the bridge's pod. It runs on the *agent
host*: the machine where the coding session lives, as the coding session's own user, with
the same instructions, memory, hooks and credentials (change `jarvis-full-agent`, design
[D30 to D40](../../openspec/changes/jarvis-full-agent/design.md)). This page is about that
machine: what is installed where, how to configure it, how to switch it off, and what to
check after an update of Claude Code.

**State on 2026-11-16.** Everything in the repository is built: the wrapper (phase 1a),
the gate with its read-back, grant, action log and `confirm` switch (phases 1b1 and 1b2),
this installer (phase 1c) and the bridge side (phase 2). Nothing here says it is installed
on any host. The host checks of task 1.17 are all open (see
[Host checks](#host-checks-task-117)). **`full` mode is enabled nowhere until every one of
them has passed**, and the configuration template ships with the kill switch set.

## What this trusts

Read this before installing. It is design D40 in plain words.

- **Whoever can start a `full` turn is you at this machine's keyboard.** That is anyone
  who holds the phone's token, or who holds the bridge's key and can connect from the
  bridge's address. They can put any prompt to an agent that has your GitHub login, your
  ssh keys and your secrets client. What stands in between is the agent's own judgement,
  your permission rules and the gate. A holder of the key can also answer the gate's
  question with a yes. The forced command limits the *form* of what can be asked (one
  prompt, one answer); it does not limit the *power* of what answers.
- **The cluster gets a road to this machine that did not exist.** Anyone who can read the
  Secret with the bridge's key, or run code in the bridge's pod or connect from the address `from=` names (in
  a cluster: any pod on the bridge's node, and the node itself), inherits the line above. What keeps it narrow: `from=` on the
  key, the Secret mounted only in the bridge's pod, the NetworkPolicy, the kill switch
  below, and a ping to your phone after every confirmed action.
- **The gate is not a wall against the agent.** The agent has a shell as the user that
  owns the gate's state, and `sudo`. The gate's guarantees hold for an agent that works
  through its tools and follows its rules, including one that was misled into *trying* a
  gated action in a shape the rules know. They do not hold for a script in a repository, a
  `make` target or a git alias that does the same thing, nor for an agent that
  deliberately edits state or kills the gate. `sudo` is gated and writes to Jarvis's own
  rules are refused by voice: that closes the obvious road, not every road.
- **Reading is not gated.** Data can leave in the address of a plain `curl` GET or a web
  fetch, and no read-back mentions it.
- **A copy to another host** is read back with the host's name, not the files. After a
  yes, whatever the command copies leaves.
- **The classifier does not see calls that match one of your allow rules.** That is so
  for the coding session too.
- **Nothing enforces "review and green checks before a merge".** The merge read-back
  states both, so the yes is an informed one. The gate does not verify the review.
- **The records are written by the agent's own user.** They are an account of what an
  agent that follows its rules did, not tamper-proof evidence. sshd's log and the notify
  ping are the parts the agent does not write.

## What is installed where

`agent_host/install.sh` writes two directories, both owned by root, and nothing else.

| Path | What |
|---|---|
| `/usr/local/lib/jarvis-agent/turn` | The wrapper: the forced command of the bridge's key. Reads one JSON request on stdin, answers one JSON object (D33). |
| `/usr/local/lib/jarvis-agent/gate` | The PreToolUse hook that refuses a gated call without a grant (D36). |
| `/usr/local/lib/jarvis-agent/confirm` | The switch for voice confirmation: `sudo /usr/local/lib/jarvis-agent/confirm on\|off [class]`. |
| `/usr/local/lib/jarvis-agent/action-log` | The PostToolUse hook that writes one line per tool call that can change something (D38). |
| `/usr/local/lib/jarvis-agent/gate_rules.toml` | The gate's rules from the repository. |
| `/usr/local/lib/jarvis-agent/prompt.md` | The appended prompt (D35). |
| `/usr/local/lib/jarvis-agent/turn-settings.json` | The static settings every turn gets with `--settings`: the two hooks (the gate with a hook `timeout` of 10 seconds), `disableAllHooks: false`, and `autoMemoryDirectory`. Written by the installer only. |
| `/usr/local/lib/jarvis-agent/src/` | The code itself (`agent_host/` and the one module of `bridge/` it imports). The six paths above the settings are symbolic links into it. The Python entry points get the shebang `#!/usr/bin/python3 -I` at install: a fixed interpreter in isolated mode, so no `PATH` or `PYTHONPATH` of a turn can swap the interpreter or shadow a module. |
| `/usr/local/lib/jarvis-agent/VERSION` | The commit that was installed. |
| `/etc/jarvis-agent/config.toml` | The configuration. Written from the template only when it does not exist. |
| `/etc/jarvis-agent/config.toml.example` | The template of the installed version, refreshed on every run. |
| `/etc/jarvis-agent/environment` | The only environment a turn starts from. Written only when it does not exist. |
| `/etc/jarvis-agent/rules.d/` | Host-local gate rules. Created empty. |

Everything is readable by all and writable by root only: the wrapper runs as the coding
session's user and must read it, and the agent must not be able to change it. So **no
secrets** in `config.toml` or `environment`.

The installer never edits an `authorized_keys` file, never touches Claude Code's managed
settings or user settings, and writes nothing below a home directory. The wrapper's own
state (session ids, records, the lock) is created by the wrapper, as the user it runs as,
in `state_dir`.

## Install

Needs Python 3.11 or newer as `/usr/bin/python3`, and `git`. From a clean clone of this
repository at a commit you have reviewed. The installer copies only files that git tracks,
refuses a checkout with uncommitted changes under `agent_host/`, and writes the commit
into `VERSION`, so that file names exactly what runs:

```sh
agent_host/install.sh --dry-run --memory-dir /home/you/.claude/projects/-home-you/memory
sudo agent_host/install.sh --memory-dir /home/you/.claude/projects/-home-you/memory
```

- `--dry-run` says what would be done and writes nothing.
- `--memory-dir` is the memory directory the coding session uses. It goes into the turn
  settings as `autoMemoryDirectory`: Claude Code keys its memory to the working
  directory, and a turn runs in a project checkout while the coding session runs
  elsewhere, so without it Jarvis starts with an empty memory. On a later run without the
  option the installed value is kept.
- `--pubkey FILE --from ADDRESS` also prints the `authorized_keys` line (next section).
- `DESTDIR=/some/dir` (or `--destdir`) stages the whole tree under a prefix instead, as
  `make install` does. CI tests the installer this way; no root is needed for it.

Running it again is safe: the code is replaced as a whole, `config.toml`, `environment`
and everything in `rules.d/` are kept.

The installer stops with a list when the checkout lacks the gate, the `confirm` switch,
the action log or the rules. Turn settings that name a gate that is not there would let
every call through.

## The `authorized_keys` line

The bridge's key may do one thing on this machine: start the wrapper. The line (D32):

```
restrict,from="<address>",command="/usr/local/lib/jarvis-agent/turn" ssh-ed25519 AAAA… jarvis-bridge
```

Print it for a public key and a source address, then add it yourself to
`~/.ssh/authorized_keys` of the coding session's user:

```sh
agent_host/install.sh --print-authorized-keys --pubkey bridge.pub --from 192.0.2.7
```

- `restrict` switches off the pty, every kind of forwarding and the user's rc file.
- `from=` is the one address the bridge's connections arrive from, as this machine sees
  them. On a workstation that is the bridge machine's own address. In a cluster it is
  the address of the **node** the bridge's pod runs on: the pod's traffic is
  source-translated to it. The pod stays on that node because its state volume is
  node-local; if the volume ever moves to another node, this line needs the new address.
  The key is then pinned to the node, not to the pod: every pod on that node connects
  from the same address (design D32). The key is useless from anywhere else. The
  installer accepts exactly one IPv4 or IPv6 address (a `/32` or `/128` suffix is accepted
  and dropped) and prints it in canonical form. It refuses every network, every host
  name, wildcards and lists.
- `command=` replaces whatever the client asks to run. The wrapper ignores
  `SSH_ORIGINAL_COMMAND` and takes no arguments.

The key file must hold exactly one bare public key; a line that already carries options
is refused. sshd enforces all three options before any of this repository's code runs.

## Configuration

`/etc/jarvis-agent/config.toml`. The template explains each key in place. Installed, the
wrapper, the gate and the action log read this one fixed path: `JARVIS_AGENT_CONFIG` in
the environment is ignored (it counts in a source checkout only, for the tests), so
nothing a turn or a project's settings can set chooses another configuration.

| Key | Required | Meaning |
|---|---|---|
| `enabled` | no (default `true`; the template sets `false`) | The kill switch, see below. |
| `projects_root` | one of the two | Every directory directly under it is a project, named after the directory. |
| `project_exclusions` | no | Names under `projects_root` that are not projects (a bare repository, another agent's checkout). |
| `[projects]` | one of the two | An explicit `name = "/path"` table instead of a root. |
| `state_dir` | yes | The wrapper's and the gate's state, created 0700 with files 0600: `sessions/`, `records/`, `actions/`, `gate/`, `pending/`, `turn.lock`. |
| `environment_file` | yes | `/etc/jarvis-agent/environment`. |
| `turn_settings` | yes | `/usr/local/lib/jarvis-agent/turn-settings.json`. |
| `prompt_file` | yes | `/usr/local/lib/jarvis-agent/prompt.md`. |
| `second_prompt_file` | no | Your own additions to the appended prompt. With it, both texts travel in `--append-system-prompt`. |
| `session_identity` | yes | The identity builds and pings go out under, exported to the turn. |
| `identity_env_var` | no (default `IDENT`) | The variable that carries it. |
| `user_settings_path` | yes | Your Claude Code user settings. Read only: a turn is refused (`unsafe_settings`) when they default to the bypass permission mode. |
| `forge_host` | no (default `github.com`) | The only host whose repositories can be named in a read-back. |
| `memory_floor_mb` | no (default `600`) | No turn starts below this much available memory (`low_memory`). |
| `timeout_s` | no (default `600`) | A turn's process group is killed after this long (`timeout`). |
| `append_system_prompt_fallback` | no (default `false`) | Set only when a host check shows `--append-system-prompt-file` is missing. |
| `session_rotation_fallback` | no (default `false`) | Set only when a host check shows `--system-prompt-snapshot off` does not work: a new session per project at the first turn after 04:00. |
| `confirm` | no (default `"on"`) | `"off"` lets gated calls through without a yes. |
| `confirm_skip` | no | A list of classes that need no yes: `merge`, `deploy`, `delete`, `force`, `api`, `account`, `message`, `create`, `outward`, `root`. Never `self`. |
| `confirm_program` | no (default `/usr/local/lib/jarvis-agent/confirm`) | The root-owned `confirm on\|off [class]` program: the one way to change the two keys above by voice. |
| `rules_dir` | no (default `/etc/jarvis-agent/rules.d`) | Host-local gate rules. |
| `managed_settings_path` | no (default `/etc/claude-code/managed-settings.json`) | Where Claude Code's managed settings are on this host. A write to it is in the class `self`. Never written by Jarvis or by `install.sh`. |
| `own_owners` | no (default none) | Owner names on the forge host whose repositories are your own. A read-back says "name" for these and "name of owner" for all others. |
| `notify_command` | no (default none) | A list of words. Run by the wrapper, not the agent, after a confirmed call ran, with the read-back appended as the last word. Empty: no notification. |
| `pr_status_command` | no (default `["gh", "pr", "view"]`) | Asked for a merge's review decision and check roll-up; the wrapper adds the number, `--repo` and `--json`. |
| `retention_days` | no (default `90`) | How long records and action logs are kept. |

A project name must match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, its real path must lie
directly under the root, and it must be a git repository with a work tree. A project must
also be on the bridge's own list to be reachable by voice.

`/etc/jarvis-agent/environment` holds `KEY=value` lines, no quoting and no expansion. A
turn starts from nothing but this file plus the turn variables: `JARVIS_TURN=1`,
`JARVIS_TURN_ID`, the identity variable, `JARVIS_AGENT_CONFIG` (the path of the configuration; informational once installed,
see below), `JARVIS_TURN_CHECKOUT` (the checkout the
turn runs in), and in a confirming turn only `JARVIS_GRANT` and `JARVIS_GRANT_BASE` (the
grant for the one call that was read back). Do not set any of these in the file. Give it the coding session user's `HOME` and a
`PATH` on which `claude`, `git` and `gh` are found. Nothing the ssh client sends and
nothing a shell rc file sets reaches the turn.

## Checkouts that get read-only turns

A project's own `.claude/settings.json` and `.claude/settings.local.json` are loaded by
Claude Code, and they arrive without anyone editing them: a `git pull` or a
`gh pr checkout` brings them. So the wrapper reads both before every turn and again after
it, and the gate reads them on every call. A checkout whose settings could run something
or widen something gets no `full` turn: the wrapper answers `unsafe_settings`, and the
bridge answers that turn read-only and says so.

The rule is an allow-list. A settings file is safe only when every top-level key is one
of:

- `$schema`, `model`, `includeCoAuthoredBy`, `cleanupPeriodDays`, `outputStyle`;
- `permissions`, and then only with `allow`, `deny` and `ask` in it.

Everything else makes the checkout unsafe. In particular:

- project MCP servers: `enabledMcpjsonServers`, `enableAllProjectMcpServers`,
  `mcpServers`;
- `hooks`, `env`, `disableAllHooks`, and the helper keys that run a command
  (`apiKeyHelper`, `statusLine`, `awsAuthRefresh`, `otelHeadersHelper`);
- `permissions.defaultMode` and `permissions.additionalDirectories`;
- any key not on the list above, and a settings file that is a symbolic link, is not a
  regular file, is too large or is not a JSON object.

A file that does not exist is safe. To use such a checkout by voice, take the key out of
the project's settings (your user settings are the place for hooks and MCP servers), or
work on it at the desk.

## The kill switch

```toml
enabled = false
```

in `/etc/jarvis-agent/config.toml` (needs root). The wrapper then answers `disabled`
without reading the request, and the bridge answers that turn read-only and says so.
Nothing in sshd changes. Set it back to `true` to switch `full` mode on again. To cut the
road itself, remove the line from `authorized_keys`.

## Host-local rules

`/etc/jarvis-agent/rules.d/` holds gate rules that belong to this machine and that the
repository must not know: your chat ping, your build trigger, your mailer, as gated
commands with their read-backs, and the rule that refuses a ping or build under any
identity but Jarvis's. The files are TOML in the format of
`/usr/local/lib/jarvis-agent/gate_rules.toml`, root-owned. The installer creates the
directory and never writes into it. A write to it by the agent is in the class `self`:
always refused by voice.

## Switching confirmation off

Everything is allowed; the gate guards against misheard speech in a car. Three ways to
switch it off, all recorded as "not confirmed: confirmation is off" in the turn record:

- for everything: `confirm = "off"` in `config.toml`;
- for single classes: `confirm_skip = ["message"]`;
- by voice, or from a shell: `sudo /usr/local/lib/jarvis-agent/confirm off` (or
  `off <class>`, `on`). By voice this is itself read back with a fixed sentence and needs
  a yes; no other edit of the configuration can be made by voice.

The class `self` (Jarvis's own rules, settings, hooks, `authorized_keys`) stays desk-only
whatever the setting.

## Reading the records

In `state_dir`, owned by the coding session's user, 0700 with files 0600:

- `records/<turn id>.json`: one per turn. Time, project, session id, the prompt as
  received, the full reply, duration, outcome, and every pending,
  confirmed, cancelled or refused gated action with its read-back. The bridge's log
  carries the same turn id.
- `sessions/sessions.json`: the Claude Code session id per project.
- `actions/<turn id>.jsonl`: the action log, one line per tool call that can change
  something, with the tool, the command or target and the exit status.
- `gate/`: what the gate noted per turn (a refused call, the one permitted repair).
- `pending/`: the one call per project that waits for a yes, between the read-back and
  the answer. It expires after 60 seconds.

`records/`, `actions/` and `gate/` are cleaned after `retention_days`. The agent can
write all of this, so the wrapper trusts none of it: it derives the read-back and the
hash again from the stored call.

```sh
ls -t ~/.local/state/jarvis-agent/records | head          # newest first
jq '{time, project, outcome, prompt, gated_actions}' ~/.local/state/jarvis-agent/records/<turn id>.json
jq -r 'select(.outcome != "ok") | [.time, .project, .outcome] | @tsv' ~/.local/state/jarvis-agent/records/*.json
```

Values shaped like an Anthropic key are redacted before a record is written. sshd logs
every connection of the bridge's key by itself (`journalctl -u ssh`).

## Updating

**Jarvis itself.** Pull the repository, review, and run `install.sh` again. Compare
`/etc/jarvis-agent/config.toml` with `config.toml.example` for new keys.

**Claude Code.** The gate depends on how Claude Code treats hooks and settings, and that
can change with a version. On every version bump on the agent host, **before Jarvis uses
the new version**:

1. Set the kill switch (`enabled = false`).
2. Update Claude Code.
3. **First, the mid-turn settings check** (the sixth row of the table below). With the
   wrapper's start check switched off for the test, put `env` (with a planted grant),
   `hooks` and `disableAllHooks: true` into a checkout's `.claude/settings.json`: present
   at the start of a turn, appearing in the middle of one, in the turn's checkout and in a
   worktree the agent entered during the turn. A gated call must not get through in any
   of these cases. **If it does, stop here**: leave the kill switch set, `full` mode stays
   off, and the design is reopened (D36). No other setting makes up for it.
4. Then the gate timeout check (row 12), with the same hard stop: if a gate that times out
   lets the call through, leave the kill switch set.
5. Then the rest of the table.
6. Write the version and the outcomes into the table, and only then set `enabled = true`.

### Host checks (task 1.17)

To run on the agent host, as the coding session's user, with the real Claude Code. A
build in CI cannot run them. **All open on 2026-11-16**; the version to run against is
the one on the agent host at that time (2.1.322 when this page was written).

| # | Check | Version | Outcome |
|---|---|---|---|
| 1 | Auto mode is active in a `-p` turn (`claude auto-mode config`), and a call the classifier refuses is refused | | open |
| 2 | A blocked call is denied and the turn ends; it does not hang until the timeout | | open |
| 3 | `--append-system-prompt-file` and `--system-prompt-snapshot off` are accepted, and a changed prompt reaches a resumed session (else: the two fallback switches) | | open |
| 4 | The gate from `--settings` runs next to the user's hooks; a gated dummy command is refused without a grant and runs once with one | | open |
| 5 | A call that matches a user allow rule (`ssh`, `gh pr merge`) is still stopped by the gate | | open |
| 6 | **Mid-turn settings**: `env`, `hooks` and `disableAllHooks` in checkout settings, at the start and appearing mid-turn, in the checkout and in a worktree, never get a gated call through. A failure stops phase 1 | | open |
| 7 | The employer hook refuses in a `-p` turn | | open |
| 8 | `autoMemoryDirectory` from `--settings` gives the turn the coding session's memory (else: the symlink fallback, per listed checkout) | | open |
| 9 | A real merge call in the explicit shape is refused, read back and released; without `--repo` it gets the naming refusal and one repair | | open |
| 10 | A dropped caller does not kill the turn | | open |
| 11 | The classifier's own refusals (force push, `reset --hard`, rebase, `branch -D`, deleting a volume claim, namespace or resource definition): refused after a yes? If so they are marked desk-only in the rules | | open |
| 12 | **Gate timeout**: a gate that runs past its hook `timeout` (10 seconds in the turn settings) denies the call; it does not let it through. A failure stops phase 1 | | open |

Which memory road is in use (`autoMemoryDirectory`, or the symlink fallback): not yet
determined (check 8).

## Optional hardening: the gate in managed settings

**Yours to install, by hand.** Claude Code's managed settings are the coding session's own
configuration. The installer does not touch them and the coding session never installs
this. No phase depends on it.

What it adds: project settings cannot override or switch off a hook that comes from
managed settings, so the gate is also loaded from a place a checkout cannot reach.

**What it does not add: anything against `disableAllHooks`.** If the mid-turn settings
check fails, phase 1 does not ship, with or without this entry.

The entry, to **merge** into the existing `/etc/claude-code/managed-settings.json` as one
more element of `hooks.PreToolUse`. Never copy a file over the existing one:

```json
{
  "matcher": "*",
  "hooks": [
    {
      "type": "command",
      "command": "[ -n \"${JARVIS_TURN:-}\" ] || exit 0; exec /usr/local/lib/jarvis-agent/gate",
      "timeout": 10
    }
  ]
}
```

One way to merge it, into a new file that you compare before moving it into place:

```sh
jq --slurpfile entry entry.json '.hooks.PreToolUse = ((.hooks.PreToolUse // []) + $entry)' \
  /etc/claude-code/managed-settings.json > managed-settings.new.json
diff <(jq -S . /etc/claude-code/managed-settings.json) <(jq -S . managed-settings.new.json)
```

How the entry behaves:

- Its first step looks for the wrapper's turn marker (`JARVIS_TURN`) in the environment.
  In the coding session the marker is absent, so it exits 0 at once, without reading the
  call or starting the gate. A missing or broken gate script therefore cannot block the
  coding session: there it fails open.
- The timeout is short (10 seconds), so it cannot hold up a call either.
- In a Jarvis turn the copy from the turn settings stays the one that decides. It is
  expected to fail closed; for a gate that runs into its timeout, host check 12 has to
  confirm that.
