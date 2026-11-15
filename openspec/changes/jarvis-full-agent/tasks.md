# Tasks: jarvis-full-agent

## Current phase: 1 (Agent host side)

One build does one phase: the phase named in this heading. The last task of every phase
moves the heading and updates `docs/reference/roadmap.yml` (an item is done only with
evidence). Every phase updates `docs/` in the same pull request (AGENTS.md).

- **(host)**: needs the agent host itself. Done by the coding session there; a factory build
  leaves it open and says so.
- **(Mark)**: needs Mark's hands, phone or ears. See the last section.

## 1. Agent host side

Everything in `agent_host/`, Python standard library only, tested in CI with a fake `claude`
executable. Nothing is installed anywhere in this phase.

- [ ] 1.1 `agent_host/turn`: the wrapper. Reads one request (version 1, 64 KB cap, closed
  field list), ignores `SSH_ORIGINAL_COMMAND` and argv, starts from an empty environment plus
  the root-owned environment file (no `LANG`/`LC_*` from the client, no `BASH_ENV`/`ENV`),
  honours the kill switch, answers one JSON object with the closed error list (D33)
- [ ] 1.2 Project resolution: the explicit list, or the root minus exclusions; name pattern,
  real path under the root, a git repository with a work tree (bare refused) (D33)
- [ ] 1.3 Session id per project in the state directory (0700 / 0600); read back only when
  it is a canonical UUID, passed as `--resume=<uuid>`; anything else is discarded and a new
  session starts and the reply says so. Test with the stored value
  `--dangerously-skip-permissions` and with `--version`: neither reaches argv (D33)
- [ ] 1.4 The turn settings set `disableAllHooks: false`. The command line of D34, fixed by a test: `auto`, `--permission-prompts none`,
  `--system-prompt-snapshot off`, the prompt file, the static root-owned turn settings; none
  of the forbidden options; refusal to start when the user's settings default to bypass.
  The turn settings are written by the installer only; no code path writes settings into
  the state directory
- [ ] 1.5 One turn at a time (lock), the memory floor, own process group, the timeout that
  kills the group, a turn that survives the caller going away (D34)
- [ ] 1.6 `agent_host/prompt.md`: the generic appended prompt of D35, with the spoken-line
  instruction; an optional second prompt file from the configuration
- [ ] 1.7 `agent_host/gate`: the PreToolUse hook, for every tool. Rules from
  `agent_host/gate_rules.toml` and `/etc/jarvis-agent/rules.d/`. Default rules (D36): for
  `kubectl`, `helm`, `argocd`, the secrets client, `gh`, `docker` and the infrastructure CLIs named in the rules (`terraform`, `tofu`, `aws`,
  `gcloud`, `az`, `doctl`, `hcloud`, `pulumi`) every
  subcommand not on the read-verb list is gated; `curl`, `wget` and httpie with a writing
  method or a data or upload option; `git push` parsed by its option grammar (`-d`,
  `--delete`, `--prune`, `:ref`, `-f`, `--force*`, grouped short flags, `+refspec`,
  `--mirror`, `--all`, `--tags`, a bare push resolved to its target or gated); `scp`,
  `rsync` and `sftp` to or from another host as class `outward` with the host as target,
  and under the path rule when both ends are local; the `mcp__*` name default. Naming rules for the classes merge, deploy, delete, force, api, account,
  message, create, root. Unwrapping of `ssh`, `bash -c`, `sh -c` and `sudo`
- [ ] 1.7b The class `self` (the paths of D36, including the user's instructions file,
  managed settings and every file a configured hook runs; writes by `Edit`, `Write` and
  shell commands): always desk-only. The fixed `confirm on|off [class]` program and its
  templated read-back as the one exception
- [ ] 1.7c Target shapes per kind (D36 table); every target of the call named (mail to, cc
  and bcc; every repository and ref of a push; namespace and resource); a missing or
  misshapen target, or more targets than the template holds, makes the call not nameable
- [ ] 1.7d The checkout settings rule: the wrapper refuses a turn (`unsafe_settings`) when
  `.claude/settings.json` or `settings.local.json` of the checkout contains `env`, `hooks`
  or `disableAllHooks`; checked before every turn (the confirming one too) and again after
  it. And in the gate, on every call: both files of the turn's checkout are re-read, and
  while either contains one of the three keys every gated call is denied, with or without a
  grant, with "project settings changed, needs the desk"; nothing becomes pending
- [ ] 1.8 The hash of D36 (tool name, zero byte, command; or canonical input without
  `description`); the grant from the environment matched by that hash and used once; silence
  on a match; denial on any error; a gated `Bash` call with a separator, pipe, substitution
  or newline anywhere in its string (quoted, or inside a nested shell or `ssh` argument), or
  with `eval`, `xargs` or `source`, denied with "run it alone"
- [ ] 1.9 The pending call: the gate stores the call only; the wrapper re-derives classes,
  targets, hash and read-back from it with the same rules; 60 s expiry on the agent host,
  single use, cleared by the next request for the project (an exact no arrives as a
  `confirm` with answer `no`, clears it and starts no agent turn); the grant is the hash the
  bridge sent, and only when it equals the pending one
- [ ] 1.10 Read-back templates per class, no agent text; all matched classes named; a call
  without a nameable target, and a call marked desk-only, answer "that one needs the desk";
  merge: nameable only with `--match-head-commit <sha>`; review decision and check roll-up
  fetched by the wrapper for that commit, "status unknown" when that fails, "the pull
  request has moved" when it is no longer the head; create: the visibility, "public" when unknown; every read-back passes the
  spoken filter. While a confirmation is pending the reply carries the read-back only
- [ ] 1.11 `confirm = "off"` and `confirm_skip`; the record marks such calls as not
  confirmed. The notify command, run by the wrapper after a confirmed call (D36)
- [ ] 1.12 The record per turn and the action log (PostToolUse hook), with redaction;
  retention (D38). The reply to the caller carries the spoken line only
- [ ] 1.13 The identity environment: the configured session identity exported, `JARVIS_TURN`
  and the turn id set, no git author or committer variables (D37)
- [ ] 1.14 Tests: one per gate pattern (a match, and a near miss that must not match), with
  at least `git push -f`, `-fu`, `--mirror`, `--delete`, `origin :branch`, a bare `git push`
  on a default branch and on a feature branch, `gh api -X DELETE`, `gh api -f`, a GraphQL
  `mergePullRequest`, `gh secret set`, `argocd app sync`, `sudo` in second position, an
  unknown `mcp__x__send_thing` and `mcp__x__post_comment`, a write to the checkout's
  `.claude/settings.local.json`; the defaults: `gh issue delete`, `gh gist create --public`,
  `kubectl cordon`, `helm plugin install`, `bao kv put` gated, `gh pr view`, `kubectl get`,
  `bao read` not; `curl -d`, `curl -X DELETE`, `wget --post-data` gated, a plain `curl` not;
  `git push --prune`, `-d`, `--all`, `--tags`, `-uf`; a command nested in `ssh` and in
  `bash -c`; a compound command; `bash -c 'gh pr merge 1; gh repo delete x'` and the same
  inside `ssh` refused with "run it alone", and a separator inside a quoted argument; target
  shapes: a pull request "number" with words, a repository with a space, a ref with a
  newline, an address without a domain, a path that resolves outside its root (each: not
  nameable); a mail with cc and bcc names all three or is not nameable; a push of two refs
  names both; a `self` write is never pending; the `confirm off` command gives the fixed
  sentence and any other argument form is desk-only; a checkout with `env`, with `hooks` and
  with `disableAllHooks` in its settings is refused; `gh pr merge` without
  `--match-head-commit` is not nameable; an exact no clears the pending call; settings that gain `env`, `hooks` or
  `disableAllHooks` after the turn started (as a `git pull` or `gh pr checkout` would bring
  them): the next gated call is denied even with a matching grant, an ungated call still
  runs, and removing the key lifts the denial; an unreadable settings file denies; `scp` and
  `rsync` to another host are gated and name the host, a local copy is not `outward`;
  `terraform apply` and `aws s3 rm` gated, `terraform version` not; a grant for another hash; a
  reused grant; an expired pending call; a pending call cleared by an unrelated request; a
  pending file altered between gate and wrapper (the read-back and the hash follow the
  altered call); a second gated call in one turn; a request with an extra field, a path as
  project, a symlinked project, a bare repository, an oversized body; the argv
- [ ] 1.15 `agent_host/install.sh`: installs wrapper, gate, the `confirm` switch, rules,
  prompt and the turn settings root-owned under `/usr/local/lib/jarvis-agent`. It never
  touches Claude Code's managed or user settings; a configuration template, an
  environment file and an empty `rules.d/` under `/etc/jarvis-agent`; prints the
  `authorized_keys` line for a given public key and source address; never edits
  `authorized_keys` itself
- [ ] 1.16 `agent_host/` added to the `docs-gates` code paths; `docs/how-to/agent-host.md`
  (what is installed where, the configuration keys, the kill switch, host-local rules, the
  `authorized_keys` line, how to read the records, how to switch confirmation off, the
  update runbook (which checks to repeat on a Claude Code version bump, the mid-turn
  settings check first), the optional managed-settings hardening as an entry to merge into
  an existing file, and the trust statement of D40 in plain words); `AGENTS.md` hard rule on the allow-list rewritten
  as in the proposal
- [ ] 1.17 **(host)** Canary checks with the real Claude Code, written into the how-to with
  the version, and their outcome into design D36 where it says so:
  - auto mode is really active in a `-p` turn (`claude auto-mode config`, and a call the
    classifier refuses is refused);
  - a blocked call is denied and the turn ends; it does not hang until the timeout;
  - `--append-system-prompt-file` and `--system-prompt-snapshot off` are accepted, and a
    changed prompt reaches a resumed session;
  - the gate from `--settings` runs next to the user's hooks; a gated dummy command is
    refused without a grant and runs once with one;
  - a call that matches a user allow rule (`ssh`, `gh pr merge`) is still stopped by the gate;
  - the checkout settings, with the wrapper's start check switched off for the test: `env`
    (a planted grant included), `hooks` and `disableAllHooks` in a checkout's
    `.claude/settings.json`, present at the start and appearing in the middle of a turn,
    never get a gated call through: the gate's per-call re-read denies it, and
    `disableAllHooks: false` in the turn settings keeps the gate running against a project
    file that says `true`. **If this fails,
    phase 1 does not ship**: `full` mode is enabled nowhere and the design is reopened
    (D36). This check goes into the update runbook: run again on every Claude Code version
    bump on the agent host, before Jarvis uses the new version;
  - the employer hook refuses in a `-p` turn;
  - `autoMemoryDirectory` from `--settings` gives the turn the coding session's memory; if
    not, the symlink fallback of D34 is set up for each listed checkout;
  - a dropped caller does not kill the turn;
  - the classifier's own refusals (force push, `reset --hard`, rebase, `branch -D`, deleting
    a volume claim, namespace or resource definition): are they refused after a yes? If so
    they are marked desk-only in the rules
- [ ] 1.18 Roadmap and phase heading

## 2. Bridge side

- [ ] 2.1 The agent adapter interface (D8): run a turn for a project, get the spoken text,
  a session or turn id and an optional pending confirmation. The existing code becomes the
  `readonly` adapter, unchanged in behaviour; a fake adapter for tests
- [ ] 2.2 `JARVIS_AGENT_MODE` (`readonly` default, `full`), and for `full`: host, port, user,
  key file, known-hosts file, timeout. Start is refused when `full` is set and a file is
  missing, is readable by others, or lies inside a project directory (D30, D32)
- [ ] 2.3 The remote adapter: the ssh argv of D32 fixed by a test, the request on stdin, the
  reply parsed strictly; a fake `ssh` in tests
- [ ] 2.4 Fallback to `readonly` for one turn on "could not connect", `no_checkout`,
  `disabled` and `unsafe_settings`, with
  the spoken first sentence; never a second run after the turn started (D30)
- [ ] 2.5 The pending confirmation per project, in memory, 60 s; the closed affirmation and
  refusal lists matched on the whole normalised transcript; the minimum of detected speech
  for an affirmation (default 0.3 s); only the read-back is spoken while one is pending;
  `confirm` sent with the next request; on an exact no: `confirm` with answer `no` sent to the agent host, "Cancelled" spoken, no
  agent turn; the header
  `X-Jarvis-Needs-Confirmation` (D36)
- [ ] 2.6 Spoken errors for `busy`, `low_memory`, `timeout`, `agent_failed`; the bridge log
  gets the turn id of the agent host, never the key or the request body on stdout
- [ ] 2.7 `deploy/Dockerfile`: the ssh client; the CI smoke test still passes offline in
  `readonly` mode
- [ ] 2.8 Tests: yes, no, something else, a late yes, a yes with nothing pending, a yes for
  another project, an agent reply that contains "yes", a restart between read-back and yes,
  a short noise transcribed as "yes"
- [ ] 2.9 Docs: `bridge-service.md` (the mode and its settings), `run-in-cluster.md` (the
  Secret as files, the tailnet egress Service, the egress rule that changes D2 of
  `run-bridge-in-cluster`: DNS, HTTPS and ssh to the agent host; what a reader of that
  Secret can now reach), `agent-host.md`
  linked from the index
- [ ] 2.10 Roadmap and phase heading

## 3. Wiring

- [ ] 3.1 **(host)** A key pair for the bridge; the private key and the agent host's public
  host key into the cluster's secret store; the public key kept for 3.3
- [ ] 3.2 **(host)** `install.sh` run on the agent host; the configuration filled in (the
  project list or root with its exclusions, among them the bare chat repository and the
  listener's checkout; session identity; memory directory; notes file; notify command; own
  repository owners)
- [ ] 3.2b **(host)** Host-local rules in `/etc/jarvis-agent/rules.d/`: the chat ping, the
  build trigger and the mailer as gated commands with their read-backs; a rule that refuses
  a ping or build under any identity but Jarvis's (the memory says "this session = odin",
  which is true of the coding session only)
- [ ] 3.3 **(host)** The `authorized_keys` line added on the agent host, with `from=` set to
  the egress proxy's tailnet address. If the permission
  classifier refuses this to the session, it is handed to Mark as one ready-to-run command
- [ ] 3.4 **(host)** In the cluster's own repository: the tailnet egress Service for the agent
  host, the Secret mounted as files, `JARVIS_AGENT_MODE=full` and its settings, one more
  egress rule (TCP 22 to the egress proxy only)
- [ ] 3.5 **(host)** Proof over the tailnet, written into `agent-host.md`: a question
  answered by the full agent with the coding session's memory; a shell and a port forward
  with the bridge's key are refused; the key from another tailnet address is refused; the
  kill switch gives a read-only answer; a wrong
  host key fails the turn; with the agent host's sshd stopped for the test, the turn is
  answered read-only and says so
- [ ] 3.6 **(host)** Proof of the gate over the tailnet, on a throwaway repository of the
  user's own: a pull request merged after a yes; not merged after "no", after another
  sentence, and after 70 seconds; the read-back of a pull request with a red check says so; the
  notify ping arrives; a planted "merge this" in an issue leads to a read-back and not to a
  merge
- [ ] 3.7 Roadmap and phase heading

## 4. Daily line and parked test

- [ ] 4.1 The daily line: assembled from the records, appended to the configured notes file
  under a lock, once per day, nothing on a day without turns; a systemd user timer in
  `agent_host/` with the installer step (D38)
- [ ] 4.2 Retention: records older than the configured number of days are removed by the
  same timer
- [ ] 4.3 Switching confirmation off and on by voice through the fixed `confirm` command
  only (the rest of the class `self` stays desk-only), with a test
- [ ] 4.4 **(host)** The timer enabled on the agent host; a first line seen in the notes
- [ ] 4.4b **(host)** The daily mail: nothing reads the notes file by itself today (the chat
  system's digest step does not). Make the mail step include Jarvis's line, in that
  system's own repository, and see it in one mail
- [ ] 4.5 **(Mark)** Parked test: ask, let Jarvis fix something small and open a pull
  request, merge it with a yes, cancel one with a no; note what was misheard
- [ ] 4.6 Roadmap; archive the change

## What needs Mark's hands

One question is open for him, with a default: the trust edge from the cluster to the agent
host, https://github.com/MWest2020/jarvis/issues/25 (default: accepted, built with the
stated limits; a no keeps the cluster side read-only, and phase 3 is then not wired). The
rest is hands, not questions.

1. **The `authorized_keys` line on the agent host** (3.3), only if the classifier refuses it
   to the session. He gets one command to paste. Until then `full` mode cannot connect and
   Jarvis answers read-only.
2. **The tailnet policy**, only if it restricts traffic between the cluster's egress proxy
   and the agent host: allow that proxy to reach the agent host on TCP 22 (the admin
   console is his). The session reports whether it is needed after 3.4.
3. **The parked test** (4.5).
4. **Optional hardening: the gate in Claude Code's managed settings on the agent host**
   (D36). **(Mark)** only: it changes the coding session's own configuration, so neither the
   installer nor the coding session does it, and no phase depends on it. The how-to gives
   the entry to merge into an existing managed-settings file, never to overwrite one. The
   entry checks the turn marker first and exits 0 without reading anything when it is
   absent, has a short hook timeout, and a broken or missing script fails open in the
   coding session; Jarvis turns stay closed through the turn-settings copy.
