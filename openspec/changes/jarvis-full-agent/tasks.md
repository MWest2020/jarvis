# Tasks: jarvis-full-agent

## Current phase: 3 (Wiring)

Phases 1 (1a, 1b1, 1b2, 1c) and 2 are built. **The host checks of phase 1 (task 1.17) ran
on `agent-lxc` on 2026-11-16 (Claude Code 2.1.322), and both hard stops failed: the mid-turn
settings check and the gate timeout check.** D36 got an answer the same day (#47: the gate's
trusted values and its own deadline), and against that commit (`bf8821a`) both hard stops
**passed** on the host, and checks 1 to 5 and 7 to 10 passed again. After the review of
#47 (whole-environment comparison, `env` pins in the turn settings, data-holding cluster
deletes desk-only), commit `37a9b9e` passed checks 4, 5, 6 (with new environment cases),
11 and 12. **1.17 is done**: every check has an outcome and both hard stops pass. `full`
mode stays off until it is switched on in task 3.2 and onward, which is **(host)** work
and comes first in phase 3.

One build does one phase: the phase named in this heading. Phase 1 is four builds (1a, 1b1,
1b2, 1c). The last task of every phase
moves the heading and updates `docs/reference/roadmap.yml` (an item is done only with
evidence). Every phase updates `docs/` in the same pull request (AGENTS.md).

- **(host)**: needs the agent host itself. Done by the coding session there; a factory build
  leaves it open and says so.
- **(Mark)**: needs Mark's hands, phone or ears. See the last section.

## 1. Agent host side

Everything in `agent_host/`, Python standard library only, tested in CI with a fake `claude`
executable. Four builds: 1a, 1b1, 1b2, 1c. Nothing is installed anywhere before 1c, and 1c only
adds the installer: running it is phase 3.

**`full` mode is enabled nowhere until every check of task 1.17 has passed on the agent
host.** A build can land all of phase 1 and it is still worth nothing until then. A failed
mid-turn settings check reopens the design (D36).

### 1a. The wrapper and one turn, no gate

- [x] 1.1 `agent_host/turn`: the wrapper. Reads one request (version 1, 64 KB cap, closed
  field list), ignores `SSH_ORIGINAL_COMMAND` and argv, starts from an empty environment plus
  the root-owned environment file (no `LANG`/`LC_*` from the client, no `BASH_ENV`/`ENV`),
  honours the kill switch, answers one JSON object with exactly the version 1 fields of D33 (`v`, `status`, `error`,
  `turn`, `spoken`, `pending`, `readback`, `confirm`, `notice`) and the closed error list; a
  request with `confirm` must carry an empty `prompt`, a non-empty one is `bad_request`
- [x] 1.2 Project resolution: the explicit list, or the root minus exclusions; name pattern,
  real path under the root, a git repository with a work tree (bare refused) (D33)
- [x] 1.3 Session id per project in the state directory (0700 / 0600); read back only when
  it is a canonical UUID, passed as `--resume=<uuid>`; anything else is discarded and a new
  session starts and the reply says so. Test with the stored value
  `--dangerously-skip-permissions` and with `--version`: neither reaches argv (D33)
- [x] 1.4 The command line of D34, fixed by a test (`agent_host/command.py`,
  `tests/test_agent_host_command.py`): `auto`, `--permission-prompts none`,
  `--system-prompt-snapshot off`, the prompt file (with the two fallbacks of D34 behind
  configuration switches: the prompt's contents in `--append-system-prompt`, and a new
  session per project at the first turn after 04:00), the path of the static root-owned turn
  settings; none of the forbidden options; refusal to start when the user's settings default
  to bypass. No code path writes settings into the state directory. What is left of this
  task is the installer's and is done under 1.15: the turn settings file itself, with
  `disableAllHooks: false`, written by the installer only. No build before 1c writes a
  settings file anywhere
- [x] 1.5 One turn at a time (lock), the memory floor, own process group, the timeout that
  kills the group, a turn that survives the caller going away (D34)
- [x] 1.6 `agent_host/prompt.md`: the generic appended prompt of D35, with the spoken-line
  instruction, the explicit call shape per gated class (`--repo`, `--match-head-commit`,
  `-n` and `--context`, an explicit remote and refspec, explicit recipients) and the one
  permitted repair after a refusal that names missing parts; an optional second prompt file from the configuration
- [x] 1.13 The identity environment: the configured session identity exported, `JARVIS_TURN`
  and the turn id set, no git author or committer variables (D37)
- [x] 1.12a The record per turn (0700 / 0600) with redaction (D38). The reply to the caller
  carries the agent's marker line verbatim in `spoken` and nothing else of the reply
- [x] 1.6b Tests for this build: the argv byte for byte, in the normal form and in both
  fallback forms; a stored session id of `--dangerously-skip-permissions` and of `--version`
  never reaching argv; a request with an extra field, with a `confirm` and a non-empty
  prompt, a path as project, a symlinked project, a bare repository, an oversized body, all
  refused; a planted `LANG` and `BASH_ENV` never reaching the child; the kill switch
  answering `disabled`; the lock, the memory floor and the timeout; every reply has exactly
  the version 1 fields; `spoken` is the marker line with its marker, and
  `bridge/summary.extract_summary` gives the same summary for it as for the full reply
- [x] 1.6c Phase heading to 1b (split afterwards into 1b1 and 1b2)

### 1b. The gate: two builds, 1b1 then 1b2

Together these carry the change's guarantees; each is reviewed on its own. The architect
failed 1b as one build (run 20261116-001952-11796, jarvis#36) and gave this cut. After 1b1
the gate denies every gated call and nothing is confirmable: a safe state (nothing is
installed before 1c; it is the "changes stay desk-only" state of the proposal), and not yet
the finished guarantee.

### 1b1. The gate: what is gated, and what it refuses

- [x] 1.7 `agent_host/gate`: the PreToolUse hook, for every tool. Rules from
  `agent_host/gate_rules.toml` and `/etc/jarvis-agent/rules.d/`. Default rules (D36): for
  `kubectl`, `helm`, `argocd`, the secrets client, `gh`, `docker` and the infrastructure CLIs named in the rules (`terraform`, `tofu`, `aws`,
  `gcloud`, `az`, `doctl`, `hcloud`, `pulumi`) every
  subcommand not on the read-verb list is gated; `curl`, `wget` and httpie with a writing
  method or a data or upload option; `git push` parsed by its option grammar (`-d`,
  `--delete`, `--prune`, `:ref`, `-f`, `--force*`, grouped short flags, `+refspec`,
  `--mirror`, `--all`, `--tags`, a bare push resolved to its target or gated); `scp`,
  `rsync` and `sftp` to or from another host as class `outward` with the host as target,
  and under the path rule when both ends are local; the `mcp__*` name default. The rules that put a call in a class (merge, deploy, delete, force, api, account,
  message, create, outward, root); the sentence of each class is 1.10's. Unwrapping of `ssh`, `bash -c`, `sh -c` and `sudo`. In this build the gate's
  answer to every gated call is a denial that tells the agent to stop and end the turn:
  nothing is pending and nothing is confirmable before 1b2. It fails closed (unreadable
  rules, an unparsable call, a crash: denied) and exits 0 without reading anything when the
  turn marker is absent
- [x] 1.7b The class `self` (the paths of D36, including the user's instructions file,
  managed settings and every file a configured hook runs; writes by `Edit`, `Write` and
  shell commands): always desk-only, never pending
- [x] 1.7d The checkout settings rule: the wrapper refuses a turn (`unsafe_settings`) when
  `.claude/settings.json` or `settings.local.json` of the checkout contains `env`, `hooks`
  or `disableAllHooks`; checked before every turn (the confirming one too) and again after
  it. And in the gate, on every call: both files of the turn's checkout are re-read, and
  both files at the git top level of the hook input's `cwd` (the agent's own worktree), and
  while any of them contains one of the three keys every gated call is denied, with or without a
  grant, with "project settings changed, needs the desk"; nothing becomes pending
- [x] 1.8a A gated `Bash` call with a separator, pipe, substitution or newline anywhere in
  its string (quoted, or inside a nested shell or `ssh` argument), or with `eval`, `xargs`
  or `source`, denied with "run it alone"; it never becomes pending
- [x] 1.14a Tests for build 1b1, carved out of 1.14: one per gate pattern (a match, and a
  near miss that must not match), with at least `git push -f`, `-fu`, `--mirror`,
  `--delete`, `origin :branch`, `--prune`, `-d`, `--all`, `--tags`, `-uf`, a bare `git push`
  on a default branch and on a feature branch; `gh api -X DELETE`, `gh api -f`, a GraphQL
  `mergePullRequest`, `gh secret set`, `gh issue delete`, `gh gist create --public`,
  `argocd app sync`, `kubectl cordon`, `helm plugin install`, `bao kv put`,
  `terraform apply`, `aws s3 rm` gated; `gh pr view`, `kubectl get`, `bao read`,
  `terraform version`, a plain `curl` not; `curl -d`, `curl -X DELETE`, `wget --post-data`
  gated; `sudo` in second position; an unknown `mcp__x__send_thing` and
  `mcp__x__post_comment`; `scp` and `rsync` to another host gated as `outward`, a local copy
  not; a command nested in `ssh` and in `bash -c`; a compound command;
  `bash -c 'gh pr merge 1; gh repo delete x'` and the same inside `ssh` refused with "run it
  alone", and a separator inside a quoted argument; a write to the checkout's
  `.claude/settings.local.json` as `self`, never pending; a checkout with `env`, with `hooks`
  and with `disableAllHooks` in its settings refused by the wrapper; settings that gain one
  of the three keys after the turn started, in the turn checkout and at the top level of a
  worktree named by the call's `cwd`: every gated call is denied, an ungated call still
  runs, and removing the key lifts the denial; an unreadable settings file denies; a broken
  rules file denies; no turn marker in the environment exits 0 and reads nothing
- [x] 1.14c Phase heading to 1b2; the `jarvis-full-agent` row of `docs/index.md`

### 1b2. Naming, the hash and grant, the read-back

- [x] 1.7f The fixed `confirm on|off [class]` program (`agent_host/confirm`, root-owned at
  install) and its templated read-back, the one exception to the class `self`; any other
  argument form is desk-only. Written and unit-tested here, installed by 1.15, proven by
  voice in 4.3
- [x] 1.7c A target longer than 100 characters, or matching the secret-shaped pattern in
  its spoken form, makes the call not nameable; for a ref the pattern is applied per part
  between `-`, `/` and `_`, the length cap to the whole ref. Target shapes per kind (D36 table); every target of the call named (mail to, cc
  and bcc; every repository and ref of a push; namespace and resource); a missing or
  misshapen target, or more targets than the template holds, makes the call not nameable
- [x] 1.7e Explicit targets only: no repository, namespace, context, remote, refspec or
  recipient is taken from the surroundings; `--context` is required on every cluster call,
  whatever the number of configured contexts. `cwd` is used for two things: the explicit
  `--repo` of a `gh` call must equal the checkout's GitHub remote when there is one, and a
  push's explicit remote name is resolved in the stored `cwd` with
  `git remote get-url --push --all` (so `pushurl` and `pushInsteadOf` apply): exactly one
  address, on the configured `forge_host`, in one of the three exact forms of D36
  (`git@<host>:owner/name`, `ssh://git@<host>/owner/name`, `https://<host>/owner/name`,
  optional `.git`), or the push is not nameable; the same rule judges the remote used in
  the `--repo` consistency check; the resolved address is
  part of the push's hash, and the gate resolves it again on release and denies with
  `target_changed` when it differs. A call that is not nameable
  only for missing explicit targets is denied with the list of exactly those parts; the gate
  remembers it and accepts one repair in that turn (the same words plus only options from
  that list, the same `cwd`); anything else after such a refusal is denied with `needs_desk`
- [x] 1.8 The hash of D36 (tool name, zero byte, `cwd`, zero byte, command; or canonical input
  without `description` in place of the command), computed in one module that both the gate
  and the wrapper import; a call with the right bytes from another `cwd` is refused; the
  grant from the environment matched by that hash and used once; silence on a match; denial
  on any error
- [x] 1.9 The pending call: the gate stores the call and the hook input's `cwd` (real path),
  nothing derived; the wrapper re-derives classes,
  targets, hash and read-back from it with the same rules; 60 s expiry on the agent host,
  single use, cleared by the next request for the project (an exact no arrives as a
  `confirm` with answer `no`, clears it and starts no agent turn); the grant is the hash the
  bridge sent, and only when it equals the pending one; a `confirm` for a call that is gone or older than 60 s on the
  agent host answers `expired`
- [x] 1.10 Read-back templates per class, no agent text; all matched classes named; a call
  without a nameable target, and a call marked desk-only, answer "that one needs the desk";
  merge: nameable only with `--match-head-commit <sha>`; review decision and check roll-up
  fetched by the wrapper for that commit, "status unknown" when that fails, "the pull
  request has moved" when it is no longer the head; create: the visibility, "public" when unknown; spoken forms: a commit as its first
  seven hex digits (hash and pin keep the full value), a `/` in a ref as "slash", a
  repository as "name" or "name of owner". The read-back goes out in the `readback` field,
  never as the marker line, and is not checked against the free-text filter; when it cannot
  be built nothing is pending and the notice is `needs_desk`. While a confirmation is
  pending `spoken` is empty
- [x] 1.11 `confirm = "off"` and `confirm_skip`; the record marks such calls as not
  confirmed. The notify command, run by the wrapper after a confirmed call (D36)
- [x] 1.12b The action log (PostToolUse hook) with redaction; gated actions and their
  outcome added to the turn record; the retention setting and a pruning function with its test (D38); the timer that
  calls it is 4.1 and 4.2
- [x] 1.14 Tests for build 1b2: the rest of this list (1.14a proves the gating cases in
  1b1; with the grant they are proven again as released or refused). One per gate pattern (a match, and a near miss that must not match), with
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
  `disableAllHooks` after the turn started, in the turn checkout and at the top level of a
  worktree named by the call's `cwd` (as a `git pull` or `gh pr checkout` would bring
  them): the next gated call is denied even with a matching grant, an ungated call still
  runs, and removing the key lifts the denial; an unreadable settings file denies; `scp` and
  `rsync` to another host are gated and name the host, a local copy is not `outward`;
  `terraform apply` and `aws s3 rm` gated, `terraform version` not; a grant for another hash; a
  reused grant; an expired pending call; a pending call cleared by an unrelated request; a
  pending file altered between gate and wrapper (the read-back and the hash follow the
  altered call); a second gated call in one turn (denied, not pending); `gh pr merge 21`
  without `--repo` and without the pin (refused with exactly those two parts named, then the
  repaired call pending); a repair that also changes the number, a repair with another tool,
  and a second repair (each `needs_desk`); `kubectl delete pod x` without `-n` and a
  `git push` without a remote (each refused with the missing part named); `kubectl delete
  pod x -n y` without `--context`, with one context configured and with two (refused both
  times, `--context` named); a remote with a `pushurl` (the read-back names the push
  address, not the fetch address); a `pushInsteadOf` rewrite (the rewritten address is
  named); two push addresses (not nameable); an address that does not parse (not nameable); a
  `pushurl` on another host with the user's own owner and name (not nameable, the name is
  never spoken); a `pushInsteadOf` that rewrites to another host (not nameable); on the
  right host: the three exact forms with and without `.git` (nameable), another user, a
  port, credentials in the address, `git://`, `http://` and an extra path part (each not
  nameable); a `gh` call whose checkout remote is on another host (not nameable); a
  60-character hyphenated branch name (nameable), a ref with a 32-character token part and
  a ref of 101 characters (not nameable);
  a `pushurl` set between read-back and release (denied, `target_changed`, nothing pushed);
  a target of 101 characters and a target of 32 token characters (not nameable); a `--repo` that is
  not the checkout's repository (not nameable); the confirmed bytes from another `cwd`
  (refused); a ref `release/1.2` and a 40-hex commit in a read-back (spoken as built,
  "slash" and seven digits, no fallback sentence); a read-back that cannot be built (nothing
  pending, `needs_desk`); a late `confirm` (`expired`)
- [x] 1.14b Phase heading to 1c; the `jarvis-full-agent` row of `docs/index.md`

### 1c. Installer, docs and the host checks

- [x] 1.15 `agent_host/install.sh`: installs wrapper, gate, the `confirm` switch, rules,
  prompt and the turn settings root-owned under `/usr/local/lib/jarvis-agent`. It never
  touches Claude Code's managed or user settings; a configuration template, an
  environment file and an empty `rules.d/` under `/etc/jarvis-agent`; prints the
  `authorized_keys` line for a given public key and source address; never edits
  `authorized_keys` itself
- [x] 1.16 `agent_host/` added to the `docs-gates` code paths; `docs/how-to/agent-host.md`
  (what is installed where, the configuration keys, the kill switch, host-local rules, the
  `authorized_keys` line, how to read the records, how to switch confirmation off, the
  update runbook (which checks to repeat on a Claude Code version bump, the mid-turn
  settings check first), the optional managed-settings hardening as an entry to merge into
  an existing file, with the plain statement that it adds nothing against
  `disableAllHooks` (a failed mid-turn check stops phase 1, hardening or not), and the
  trust statement of D40 in plain words); `AGENTS.md` hard rule on the allow-list rewritten
  as in the proposal
- [x] 1.17 **(host)** Canary checks with the real Claude Code on the agent host (a factory build
  cannot run them and leaves each one open, naming the host and the version to run against), written into the how-to with
  the version, and their outcome into design D36 where it says so:

  **Run on 2026-11-16** on the agent host (`agent-lxc`, as the coding session's user)
  against Claude Code 2.1.322 and the installed commit `1fc6d97`. The outcomes, with the
  commands, are in `docs/how-to/agent-host.md`, "Host checks". Ticked: passed. **Two hard
  stops failed** (the gate timeout check and the mid-turn settings check), so this task
  stays open, phase 1 does not ship, and the design is reopened (D36). Every check is run
  again once D36 has an answer.
  **Re-run on 2026-11-16 after D36's answer** (#47, installed commit `bf8821a`, Claude Code
  2.1.322): checks 6 and 12 **passed**, checks 4 and 5 passed again, and the action log now
  records a failed call (`PostToolUseFailure`, "ran, failed"). Checks 1 to 3 and 7 to 10
  were run again against `bf8821a` the same day and pass.
  **Second re-run, after the review of #47** (installed commit `37a9b9e`): checks 4, 5, 6
  and 12 pass; check 6 now includes a checkout `env` that sets `LD_PRELOAD`, `PATH`,
  `HOME` and `GIT_CONFIG_*`, at the start, mid-turn and brought in and taken away again
  (denied every time; the pins held: no preload, no redirected `git`, no `HOME`); the
  cluster part of the last check has its outcome (below). **Every check has an outcome and
  both hard stops pass: 1.17 is done.**

  - [x] auto mode is really active in a `-p` turn (`claude auto-mode config`, and a call the
    classifier refuses is refused);
  - [x] a blocked call is denied and the turn ends; it does not hang until the timeout;
  - [x] a gate that times out (the hook `timeout` in the turn settings, now 30 s) denies the call;
    it does not let it through. **If Claude Code lets the call through on a hook timeout,
    phase 1 does not ship**: `full` mode is enabled nowhere and the design is reopened.
    **2026-11-16: FAIL.** Claude Code 2.1.322 lets the call through on a hook timeout.
    **Re-run, `bf8821a`: pass.** The gate keeps its own 5 s deadline and decides in a
    worker; with the worker frozen, the call was denied at the deadline and did not run.
    Claude Code itself still fails open on a hook timeout (D36 says so);
  - [x] the gate from `--settings` runs next to the user's hooks; a gated dummy command is
    refused without a grant and runs once with one;
  - [x] a call that matches a user allow rule (`ssh`, `gh pr merge`) is still stopped by the gate;
  - [x] the checkout settings, with the wrapper's start check switched off for the test: `env`
    (a planted grant included), `hooks` and `disableAllHooks` in a checkout's
    `.claude/settings.json`, present at the start and appearing in the middle of a turn,
    in the turn checkout and in a worktree the agent entered during the turn,
    never get a gated call through: the gate's per-call re-read denies it, and
    `disableAllHooks: false` in the turn settings keeps the gate running against a project
    file that says `true`. **If this fails,
    phase 1 does not ship**: `full` mode is enabled nowhere and the design is reopened
    (D36). This check goes into the update runbook: run again on every Claude Code version
    bump on the agent host, before Jarvis uses the new version. **2026-11-16: FAIL.**
    `disableAllHooks: false` held, but project `env` reaches the gate, at the start and
    mid-turn: a mid-turn `env` that set `JARVIS_GRANT` and redirected
    `JARVIS_TURN_CHECKOUT` released a gated call made from a clean worktree.
    **Re-run, `bf8821a`: pass**, all six cases plus a file that came and went: every gated
    call denied with `settings_changed`;
  - [x] the employer hook refuses in a `-p` turn;
  - [x] `autoMemoryDirectory` from `--settings` gives the turn the coding session's memory; if
    not, the symlink fallback of D34 is set up for each listed checkout;
  - [x] `--append-system-prompt-file` exists, else the wrapper's fallback switch is set (the
    contents in `--append-system-prompt`); `--system-prompt-snapshot off` exists and a
    changed prompt reaches a resumed session, else the daily session rotation is set (D34);
  - [x] a real merge call in the explicit shape is refused, read back and released; the same
    call without `--repo` gets the naming refusal and one repair;
  - [x] a dropped caller does not kill the turn;
  - [x] the classifier's own refusals (force push, `reset --hard`, rebase, `branch -D`, deleting
    a volume claim, namespace or resource definition): are they refused after a yes? If so
    they are marked desk-only in the rules. **2026-11-16, git part:** a force push was
    released and run after a yes; `reset --hard`, rebase and `branch -D` run when named
    (not gated); nothing marked desk-only, and that is accepted (D36: recoverable through
    the reflog, except uncommitted work under `reset --hard`; no new class). The cluster
    deletions were not run (outside the sandbox), so this stays open.
    **2026-11-16, cluster part, named but never run (`bf8821a`):** `ssh jumpy kubectl delete
    namespace …` and `… delete crd …` are refused as desk-only (not nameable), nothing
    pending; `… delete pvc … -n default --context default` is read back and becomes pending
    (no yes given). **Outcome of the cluster part: not run against the cluster (the
    operator's permission classifier refuses running a delete, even of a nonexistent
    object); data-holding kinds made desk-only by rule instead; other deletes need a
    spoken yes.** `kubectl delete` of a volume claim, volume, namespace, resource definition
    or stateful set, and `--all`, `-l`/`--selector`, `-f`/`--filename`, `-k`, are not
    nameable, through `ssh <host>` too; on `37a9b9e` the volume claim delete through
    `ssh jumpy` is refused as `needs_desk`
- [x] 1.18 Roadmap and phase heading to 2. The phase-1 items stay open in the roadmap, with the
  code as evidence, until 1.17 is done on the host. (Phase 2 was built before 1c was
  merged, so the heading went from 1c straight to 3)

## 2. Bridge side

- [x] 2.1 The agent adapter interface (D8): run a turn for a project, get the marker line
  for `bridge/summary.extract_summary` (unchanged), or a read-back with its confirmation
  ids, a turn id, and an error or notice code. The existing code becomes the
  `readonly` adapter, unchanged in behaviour; a fake adapter for tests
- [x] 2.2 `JARVIS_AGENT_MODE` (`readonly` default, `full`), and for `full`: host, port, user,
  key file, known-hosts file, timeout. Start is refused when `full` is set and a file is
  missing, is readable by others, or lies inside a project directory (D30, D32)
- [x] 2.3 The remote adapter: the ssh argv of D32 fixed by a test, the request on stdin, the
  reply parsed strictly; a fake `ssh` in tests
- [x] 2.4 Fallback to `readonly` for one turn on "could not connect", `no_checkout`,
  `disabled` and `unsafe_settings`, with
  the spoken first sentence; never a second run after the turn started (D30)
- [x] 2.5 The pending confirmation per project, in memory, 60 s; the closed affirmation and
  refusal lists matched on the whole normalised transcript; the minimum of detected speech
  for an affirmation (default 0.3 s); only the read-back is spoken while one is pending,
  taken from the `readback` field and sent to text-to-speech as built, without the
  free-text filter and never replaced by the fallback sentence; a `pending` reply with an
  empty or over-long read-back is answered with `confirm` `no` and the desk sentence;
  `confirm` sent with the next request; on an exact no: `confirm` with answer `no` sent to the agent host, "Cancelled" spoken, no
  agent turn; the header
  `X-Jarvis-Needs-Confirmation` (D36)
- [x] 2.5b The project list in `full` mode: `POST /turn` and `GET /projects` keep using the
  bridge's own list (the pod's copies); a project not on it is refused before the agent
  host is contacted. Stated in `bridge-service.md` and `run-in-cluster.md`: a project that
  exists only on the agent host cannot be reached by voice
- [x] 2.6 Spoken errors for `busy`, `low_memory`, `timeout`, `agent_failed`, and for
  `expired` its own sentence ("That took too long. Ask again."); fixed sentences for the
  notices `needs_desk`, `run_alone`, `settings_changed`, `target_changed`, `out_of_budget`,
  `new_session`; the bridge log
  gets the turn id of the agent host, never the key or the request body on stdout
- [ ] 2.6b (agent host side) The wrapper's half of `out_of_budget`: map a usage-limit
  failure of `claude` to `agent_failed` with the notice `out_of_budget` (design, Risks:
  shared usage limits), with a test against a recorded failure. No build of phase 1b does
  this; until it is built the fixed sentence of 2.6 is unreachable
- [x] 2.7 `deploy/Dockerfile`: the ssh client; the CI smoke test still passes offline in
  `readonly` mode
- [x] 2.8 Tests: yes, no, something else, a late yes, a yes with nothing pending, a yes for
  another project, an agent reply that contains "yes", a restart between read-back and yes,
  a short noise transcribed as "yes"
- [x] 2.9 Docs: `bridge-service.md` (the mode and its settings), `run-in-cluster.md` (the
  Secret as files, the egress rule that changes D2 of
  `run-bridge-in-cluster`: DNS, HTTPS and ssh to the agent host's one address; that `from=`
  names the pod's node and why the pod stays there (D32); what a reader of that
  Secret can now reach), `agent-host.md`
  linked from the index
- [x] 2.10 Roadmap and phase heading (moved to 3 together with 1.18)

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
  the address the bridge's connections come from (in a cluster: the address of the pod's
  node, D32). If the permission
  classifier refuses this to the session, it is handed to Mark as one ready-to-run command
- [ ] 3.4 **(host)** In the cluster's own repository: the Secret mounted as files,
  `JARVIS_AGENT_MODE=full` and its settings (the agent host's address among them), one more
  egress rule (TCP 22 to an `ipBlock` /32 of the agent host's address only)
- [ ] 3.5 **(host)** Proof from the phone, end to end, written into `agent-host.md`: a question
  answered by the full agent with the coding session's memory; a shell and a port forward
  with the bridge's key are refused; the key from another address than the one in `from=` is refused; the
  kill switch gives a read-only answer; a wrong
  host key fails the turn; with the agent host's sshd stopped for the test, the turn is
  answered read-only and says so
- [ ] 3.6 **(host)** Proof of the gate from the phone, on a throwaway repository of the
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
  same timer, which calls the pruning function of 1.12b (the setting and the function are
  built and tested there; only the timer is this task's)
- [ ] 4.3 Switching confirmation off and on by voice through the fixed `confirm` command
  only (the rest of the class `self` stays desk-only), with a test. The program is written
  in 1.7f and installed by 1.15; this task proves it by voice
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
2. **The network between the cluster's nodes and the agent host**, only if something there
   (a firewall on the agent host or between the two) blocks TCP 22 from the bridge's node.
   The session reports whether it is needed after 3.4.
3. **The parked test** (4.5).
4. **Optional hardening: the gate in Claude Code's managed settings on the agent host**
   (D36). **(Mark)** only: it changes the coding session's own configuration, so neither the
   installer nor the coding session does it, and no phase depends on it. The how-to gives
   the entry to merge into an existing managed-settings file, never to overwrite one. The
   entry checks the turn marker first and exits 0 without reading anything when it is
   absent, has a short hook timeout, and a broken or missing script fails open in the
   coding session; Jarvis turns stay closed through the turn-settings copy. It adds nothing
   against `disableAllHooks`: if the mid-turn check of 1.17 fails, phase 1 does not ship,
   with or without it.
