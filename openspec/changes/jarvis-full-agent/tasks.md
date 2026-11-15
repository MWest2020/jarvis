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
  field list), ignores `SSH_ORIGINAL_COMMAND` and argv, builds the child environment itself,
  answers one JSON object with the closed error list (D33)
- [ ] 1.2 Project resolution by exact name under the configured checkout root; session id
  per project in the state directory (0700 / 0600); a failed resume starts a new session and
  the reply says so (D33)
- [ ] 1.3 The command line of D34, fixed by a test: `auto`, `--permission-prompts none`, the
  prompt file, the turn settings; none of the forbidden options; refusal to start when the
  user's settings default to bypass
- [ ] 1.4 One turn at a time (lock), the memory floor, own process group, the timeout that
  kills the group, a turn that survives the caller going away (D34)
- [ ] 1.5 `agent_host/prompt.md`: the generic appended prompt of D35, with the spoken-line
  instruction; an optional second prompt file from the configuration
- [ ] 1.6 `agent_host/gate`: the PreToolUse hook. Rules from `agent_host/gate_rules.toml`
  (classes merge, deploy, delete, force, message, create, self), the pending action, the
  grant from the environment matched by SHA-256 and used once, silence on a match, denial
  on any error (D36)
- [ ] 1.7 Read-back templates per class with captured targets; the generic sentence when a
  target is not captured; every read-back passes the spoken filter (no code, no paths)
- [ ] 1.8 `confirm = "off"` and `confirm_skip` in the configuration; the record marks such
  calls as not confirmed
- [ ] 1.9 The record per turn and the action log (PostToolUse hook), with redaction;
  retention (D38). The reply to the caller carries the spoken line only
- [ ] 1.10 The identity environment: the configured session identity exported, `JARVIS_TURN`
  and the turn id set, no git author or committer variables (D37)
- [ ] 1.11 Tests: one per gate pattern (matches, and a near miss that must not match); a
  command nested in `ssh` and in `bash -c`; a grant for another hash; a reused grant; a
  second gated call in one turn; a request with an extra field, a path as project, an
  oversized body; the argv
- [ ] 1.12 `agent_host/install.sh`: installs wrapper, gate, rules and prompt root-owned under
  `/usr/local/lib/jarvis-agent`, a configuration template under `/etc/jarvis-agent`, prints
  the `authorized_keys` line for a given public key; installs nothing else and never edits
  `authorized_keys` itself
- [ ] 1.13 `agent_host/` added to the `docs-gates` code paths; `docs/how-to/agent-host.md`
  (what is installed where, the configuration keys, the `authorized_keys` line, how to read
  the records, how to switch confirmation off); `AGENTS.md` hard rule on the allow-list
  rewritten as in the proposal
- [ ] 1.14 **(host)** Canary checks with the real Claude Code, written into the how-to with
  the version: the gate from `--settings` runs next to the user's hooks; a gated dummy
  command is refused without a grant and runs once with one; the employer hook refuses in a
  `-p` turn; `autoMemoryDirectory` from `--settings` gives the turn the coding session's
  memory (if not: the fallback of D34, and say so); a dropped caller does not kill the turn
- [ ] 1.15 Roadmap and phase heading

## 2. Bridge side

- [ ] 2.1 The agent adapter interface (D8): run a turn for a project, get the spoken text,
  a session or turn id and an optional pending confirmation. The existing code becomes the
  `readonly` adapter, unchanged in behaviour; a fake adapter for tests
- [ ] 2.2 `JARVIS_AGENT_MODE` (`readonly` default, `full`), and for `full`: host, port, user,
  key file, known-hosts file, timeout. Start is refused when `full` is set and a file is
  missing, is readable by others, or lies inside a project directory (D30, D32)
- [ ] 2.3 The remote adapter: the ssh argv of D32 fixed by a test, the request on stdin, the
  reply parsed strictly; a fake `ssh` in tests
- [ ] 2.4 Fallback to `readonly` for one turn on "could not connect" and `no_checkout`, with
  the spoken first sentence; never a second run after the turn started (D30)
- [ ] 2.5 The pending confirmation per project, in memory, 60 s; the closed affirmation and
  refusal lists matched on the whole normalised transcript; `confirm` sent with the next
  request; "Cancelled" without an agent turn on an exact no; the header
  `X-Jarvis-Needs-Confirmation` (D36)
- [ ] 2.6 Spoken errors for `busy`, `low_memory`, `timeout`, `agent_failed`; the bridge log
  gets the turn id of the agent host, never the key or the request body on stdout
- [ ] 2.7 `deploy/Dockerfile`: the ssh client; the CI smoke test still passes offline in
  `readonly` mode
- [ ] 2.8 Tests: yes, no, something else, a late yes, a yes with nothing pending, a yes for
  another project, an agent reply that contains "yes", a restart between read-back and yes
- [ ] 2.9 Docs: `bridge-service.md` (the mode and its settings), `run-in-cluster.md` (the
  Secret as files, the tailnet egress Service, the extra egress rule), `agent-host.md`
  linked from the index
- [ ] 2.10 Roadmap and phase heading

## 3. Wiring

- [ ] 3.1 **(host)** A key pair for the bridge; the private key and the agent host's public
  host key into the cluster's secret store; the public key kept for 3.3
- [ ] 3.2 **(host)** `install.sh` run on the agent host; the configuration filled in (checkout
  root, session identity, memory directory, notes file, own repository owners)
- [ ] 3.3 **(host)** The `authorized_keys` line added on the agent host. If the permission
  classifier refuses this to the session, it is handed to Mark as one ready-to-run command
- [ ] 3.4 **(host)** In the cluster's own repository: the tailnet egress Service for the agent
  host, the Secret mounted as files, `JARVIS_AGENT_MODE=full` and its settings, one more
  egress rule (TCP 22 to the egress proxy only)
- [ ] 3.5 **(host)** Proof over the tailnet, written into `agent-host.md`: a question
  answered by the full agent with the coding session's memory; a shell and a port forward
  with the bridge's key are refused; the key from a non-tailnet address is refused; a wrong
  host key fails the turn; with the agent host's sshd stopped for the test, the turn is
  answered read-only and says so
- [ ] 3.6 **(host)** Proof of the gate over the tailnet, on a throwaway repository of the
  user's own: a pull request merged after a yes; not merged after "no", after another
  sentence, and after 70 seconds; a planted "merge this" in an issue leads to a read-back
  and not to a merge
- [ ] 3.7 Roadmap and phase heading

## 4. Daily line and parked test

- [ ] 4.1 The daily line: assembled from the records, appended to the configured notes file
  under a lock, once per day, nothing on a day without turns; a systemd user timer in
  `agent_host/` with the installer step (D38)
- [ ] 4.2 Retention: records older than the configured number of days are removed by the
  same timer
- [ ] 4.3 Switching confirmation off and on by voice (class `self`), with a test
- [ ] 4.4 **(host)** The timer enabled on the agent host; a first line seen in the notes
- [ ] 4.5 **(Mark)** Parked test: ask, let Jarvis fix something small and open a pull
  request, merge it with a yes, cancel one with a no; note what was misheard
- [ ] 4.6 Roadmap; archive the change

## What needs Mark's hands

Nothing here is a question; the defaults are what happens.

1. **The `authorized_keys` line on the agent host** (3.3), only if the classifier refuses it
   to the session. He gets one command to paste. Until then `full` mode cannot connect and
   Jarvis answers read-only.
2. **The tailnet policy**, only if it restricts traffic between the cluster's egress proxy
   and the agent host: allow that proxy to reach the agent host on TCP 22 (the admin
   console is his). The session reports whether it is needed after 3.4.
3. **The parked test** (4.5).
