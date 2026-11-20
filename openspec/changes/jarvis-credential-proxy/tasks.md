# Tasks: jarvis-credential-proxy

## Current phase: 0 (observe only), built; waiting for review, then the host

Approved by Mark on 2026-11-18 by mail ("defaults en proposals ok", #83): every open
question of the proposal takes its default. So: stage 1 on the turn's cgroup before #45,
stage 2 only with #45's turn user (1); a closed list for web lookups (2); git to GitHub
over https for turns (3); `sudo` kept in stage 1 and stated as a seatbelt, none for the
stage 2 turn user (4); DNS closed for the turn, after stage 0 shows what breaks (5); the
interception engine chosen after stage 1, in a PR with the reasons (6); Claude Code's own
login left with the turn (7).

One build does one phase. The last task of every phase moves the heading and updates
`docs/reference/roadmap.yml` (an item is done only with evidence). Every phase updates
`docs/` in the same pull request (AGENTS.md). Each stage records its host checks in the
agent-host how-to, as task 1.17 of `jarvis-full-agent` did.

- **(host)**: needs the agent host itself. Done by the coding session there; a build
  leaves it open and says so.
- **(Mark)**: needs Mark's decision or hands.

## 0. Observe (proposal, "Rollout", step 0)

The proxy runs, the turn's environment points at it, everything passes through by name and
is logged. Nothing is enforced, nothing is denied, nothing is decrypted, no credential.

- [x] 0.1 `[egress]` in `config.toml` (`agent_host/egress.py`, `agent_host/config.py`):
  `mode = "off" | "observe"`, default `"off"`; `"enforce"` refused until stage 1 is built;
  `proxy` a loopback address and port (default `127.0.0.1:3128`); `log_dir` absolute
  (default `/var/log/jarvis-egress`); unknown keys refused. In the template as commented
  dotted keys
- [x] 0.2 The observing proxy (`agent_host/egress.py`, entry point `agent_host/egress-proxy`),
  standard library only: `CONNECT host:port` resolved by the proxy and relayed (no TLS
  interception, E5); the TLS server name read from the client's first bytes and logged,
  never acted on; a plain `http://` request in absolute form forwarded with
  `Connection: close`; at most 256 connections, a 64 KB head, an idle line closed after an
  hour; a connection that sends nothing (the wrapper's probe) is not logged
- [x] 0.3 The request log (E11): one JSON line per connection, a file per UTC day in
  `log_dir`, mode 0640, pruned after `retention_days`. The `Event` fields of the protocol
  (time, method, origin, path without query, status, decision, reason, duration,
  credential injected) plus `address`, `private`, `sni`, `sni_matches`. Never a body, a
  header or a query
- [x] 0.4 The wrapper (`agent_host/wrapper.py`): with `"observe"` and a proxy that answers
  within 0.3 s, the `claude` process gets `HTTPS_PROXY`, `HTTP_PROXY` (both spellings) and
  `NO_PROXY` for loopback, also in the record of its trusted values; a proxy that does not
  answer adds nothing (the turn runs as with `"off"`). The wrapper's own work (notify,
  PR status, clone) keeps the environment without the proxy (E2)
- [x] 0.5 The turn record's `egress` field: mode, `proxy: up | down`, and the turn window's
  requests, failures, private addresses, server-name mismatches and origins (at most 20)
- [x] 0.6 The installer, behind `--egress` (E3): `/etc/systemd/system/jarvis-egress.service`
  (own user `jarvis-egress`, group `jarvis-egress-log`, hardened, `LogsDirectory`), the
  user and the group when absent, `systemctl daemon-reload`; not enabled, nobody added to
  the group; the entry point always installed under the fixed interpreter
- [x] 0.7 The gate (E12): with egress on, the unit and `log_dir` are in the class `self`.
  The proxy variables are not pinned in the turn settings: a checkout's `env` already makes
  the checkout unsafe, and the gate compares its whole environment with `claude`'s
- [x] 0.8 Tests in CI (`tests/test_agent_host_egress.py`): the configuration, the proxy
  against loopback servers (pass-through, server name, plain HTTP without the query in the
  log, an upstream that does not answer, a bad target, a probe), pruning, the summary
  window, the wrapper with egress off, on and with the proxy down, the class `self`; the
  installer with and without `--egress`
- [x] 0.9 Docs: the agent-host how-to ("Egress: what a turn reaches", the installed files,
  the configuration keys), the index, the roadmap
- [ ] 0.10 **(host)** Install on the agent host from the merged commit: `install.sh --egress`,
  the coding session's user in `jarvis-egress-log`, `systemctl enable --now jarvis-egress`,
  `egress.mode = "observe"`. Host checks, recorded in the how-to: a voice turn through the
  proxy works (model traffic, `gh`, git over https, `WebFetch`, `WebSearch`); the turn
  record has `egress` with the origins; `startup_gap_ms` and `claude_wall_ms` within noise
  of the turns before (E15); the desk session unchanged; the gate's checks 4 to 6 and 12
  of task 1.17 again, since the turn's environment grew
- [ ] 0.11 **(host)** A week of voice turns in `"observe"`; then the candidate list for
  stage 1 from the log (origins, private addresses, server-name mismatches), in a PR as
  the first `egress.toml`, for Mark's yes at the desk
- [ ] 0.12 Not seen by stage 0, said in the how-to: programs that ignore the proxy
  variables, DNS lookups and ssh. Seeing them needs the turn's cgroup (E1, E2): the first
  task of stage 1 logs them (an nftables rule that logs and accepts) before anything drops

## 1. Enforce egress (no new user)

Outline; detailed when stage 0 is done.

- [ ] 1.1 `jarvis-turn.slice` and the `turn-cgroup` helper with its one `sudoers` line;
  the wrapper's child moves into the slice and executes `claude` in place (E2)
- [ ] 1.2 The nftables table `inet jarvis_egress`: first log-and-accept for the slice
  (observing direct connections and DNS, 0.12), then drop with the log prefix
  `jarvis-egress-drop` (E1); the per-turn check of the slice id, `egress_unavailable` on a
  mismatch (E2)
- [ ] 1.3 `/etc/jarvis-agent/egress.toml`: the policy format of E4 with its validation,
  pass-through by name for listed hosts, a fixed `403` body for the rest (E11)
- [ ] 1.4 `mode = "enforce"`; the installer refuses it without the table, the slice and
  the service (E12)
- [ ] 1.5 **(host)** The checks of E15 for stage 1
- [ ] 1.6 **(Mark)** The list, at the desk

## 2. Credentials (with #45's turn user)

Outline; waits for #45.

- [ ] 2.1 The interception engine chosen, in a PR with the reasons (question 6, E14)
- [ ] 2.2 The per-start CA, the bundle for turns only (E6); placeholders and
  `LoadCredential=` (E7); GitHub read-only first
- [ ] 2.3 git over https for turns (E8), the jump host's key in an `ssh-agent` of another
  user
- [ ] 2.4 **(host)** The stage 2 checks of E15
