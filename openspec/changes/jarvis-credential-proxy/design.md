# Design: jarvis-credential-proxy

## Context

What exists, from this repository:

- **Where a `full` turn runs** (`jarvis-full-agent`, D31 to D33; agent-host how-to). On the
  agent host, as the coding session's own Unix user, started by a root-owned wrapper that
  the bridge's key may run and nothing else. The turn's environment is
  `/etc/jarvis-agent/environment` (root-owned, world-readable, "no secrets": `PATH`,
  `HOME`, `USER`, `LOGNAME`) plus the turn's own `JARVIS_*` values. `config.toml` holds no
  secrets either. The installer writes nothing below a home directory.
- **How credentials reach the turn today: through `HOME`.** The turn finds what the coding
  session finds: `gh`'s stored login (an OAuth token in its configuration file under the
  home; its git protocol is ssh), git over ssh with keys in `~/.ssh`, Claude Code's own
  subscription login under `~/.claude`, and the secrets client's token. The agent host has
  no `kubectl` and no kubeconfig: a turn reaches the cluster only through an ssh hop to a
  jump host that has them, and the gate unwraps the `ssh` and judges the inner `kubectl`
  (host check 11). The private network's daemon (Tailscale) is root's.
- **The gate** (D36): a PreToolUse hook that reads the text of a call. `curl`, `wget` and
  httpie with a write method are in class `api`; `scp`, `rsync`, `sftp` in `outward`;
  `ssh`, `sudo`, `bash -c` are unwrapped. Its trusted values come from a record keyed by the
  `claude` process and from that process's exec-time environment in `/proc`; it compares its
  whole environment, and the turn settings pin `LD_*`, `GIT_CONFIG_COUNT`, `PATH` and
  `HOME` so a checkout's `env` cannot change them.
- **What it does not cover** (D39, D40): a script, a `make` target or an SDK call that does
  the same thing; reads ("data can leave in the address of a plain `curl` GET or a web
  fetch"); an agent that uses its shell and `sudo` against the host. D40 names the
  credential broker of #45 as the real fix; Mark, 2026-11-16: "a now, b later".
- **The agent host's code** is Python standard library only, run as
  `/usr/bin/python3 -I` from a root-owned tree.

What the sources say (read 2026-11-17; `external-sandbox` at `0681c98a08b2`,
`external-sandbox-v86` at `4134917381e7`, `external-sandbox-quicksand` at `5c79da9e3e10`):

- **The post.** The agent runs on the host; its commands run in a Linux guest that "can only
  see the folders and hosts you allow". Networking is off by default; `--proxy` gives HTTP(S)
  through a proxy that controls "which hosts can be accessed and the HTTP verbs that can be
  used with them", with a log on the `/proxy` page. The guest gets `HTTP_PROXY`,
  `HTTPS_PROXY` and the proxy's CA in `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`,
  `NODE_EXTRA_CA_CERTS`; "code that ignores these settings and tries to connect directly is
  still blocked" (by the guest, which has no other network). Credential replacement: the
  guest holds a placeholder, the host-side proxy swaps in the value of a host environment
  variable. Caveats, verbatim below under Risks.
- **`PROTOCOL.md`, section 7.** A `Policy` is `{allow:[Rule], guestEnv?}`, at most 64 rules.
  A `Rule` is `{origin, methods?, paths?, allowCidrs?, credential?}`: an exact canonical
  origin, no wildcard; `paths` are "exact URL pathnames"; without `allowCidrs` "only publicly
  routable resolved addresses are allowed", and the address is "validated and pinned for the
  connection". A `Credential` is `{header, placeholder, secretEnv, prefix?}`, "only applies
  over HTTPS", the whole header value must equal the placeholder, the schema "takes
  environment-variable names only". "Redirect targets need their own authorization." HTTPS
  "uses an ephemeral CA trusted by the guest, with a separately verified TLS connection to
  the upstream". Not supported: "Arbitrary TCP and UDP, HTTP/2 and WebSocket upgrades". An
  `Event` has `timestamp, method, origin, path, status, decision, reason, durationMs,
  credentialInjected`; snapshots "omit request and response bodies and headers, and redact
  real credentials".
- **`external-sandbox-v86`, `docs/proxy.md`.** "The proxy runs inside the same Node.js
  process as the API and v86. There is no separate proxy daemon." "Proxy environment
  variables are convenience settings, not the security boundary." No CA private key is
  written to disk; no CA is added to the host trust store. Responses must use identity
  encoding "so literal secret redaction cannot be bypassed by compression". "Host-side agent
  tools must not be able to read the proxy's credentials through another route. A shell
  adapter alone does not restrict host file tools." "The proxy belongs to one VM."
- **`claude-code/README.md`.** `CLAUDE_CODE_SHELL_PREFIX` receives Claude's complete command
  as one argument; "the prefix also applies to other shell invocations such as shell hooks,
  status-line commands, and stdio MCP startup commands"; "native file tools such as
  Read/Edit still access host files".
- **Claude Code's network documentation** (code.claude.com, "Enterprise network
  configuration"): Claude Code respects `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` and
  `NODE_EXTRA_CA_CERTS`, does not support SOCKS, and lists the hosts it needs
  (`api.anthropic.com`, `platform.claude.com` for OAuth refresh, `claude.ai`,
  `mcp-proxy.anthropic.com` for connectors, and others). The `WebFetch` tool calls
  `api.anthropic.com` for a domain safety check.

Decision numbers: E1 to E15.

## Goals / Non-Goals

Goals:
- Nothing leaves the agent host from a voice turn except to listed hosts, with listed
  methods where a method rule is set, whatever program makes the request.
- Every request a voice turn makes is in a log Mark can read.
- In stage 2, the GitHub token and other HTTP header credentials are not readable by the
  turn, and are used only on the hosts, methods and paths listed for them.
- The coding session at the desk is unchanged.

Non-Goals:
- A wall against a turn that has root. Stage 1 keeps `sudo` and says so (E2).
- Sandboxing commands (`CLAUDE_CODE_SHELL_PREFIX`, E13).
- Judging what a request on an allowed host means (an issue comment and a merge are both on
  `api.github.com`). That stays the gate's read-back.
- Grant-bound writes at the proxy (#45's broker); stage 3, a later change.
- Anything in `readonly` mode, the bridge or the phone.

## Decisions

**E1. The boundary is the kernel, not the environment.** On the host there is no guest whose
only road is the proxy. `HTTPS_PROXY` is a hint: `curl --noproxy '*'`, a Python socket or a
Go binary with its own transport ignores it. So a root-owned nftables table
(`inet jarvis_egress`) holds an output chain that, for the turn's processes only, accepts
established traffic, TCP to the proxy's loopback port and TCP port 22 to the listed ssh
destinations, and drops everything else with a log prefix. The environment variables stay,
as the post uses them: so that well-behaved clients find the proxy.
Alternatives considered:
- *Environment variables alone.* Rejected: the same text-level seatbelt the gate already is.
- *A network namespace per turn.* Rejected for now: needs root at every turn start and a
  veth or a socket relay to the proxy; the cgroup match (E2) gives the same filter with less.

**E2. Who it applies to: the turn's processes, matched by cgroup (stage 1) or uid (stage 2).**
The turn and the coding session share a Unix user today, so a uid match would cut the desk
too.
- *Stage 1.* The wrapper starts `claude` inside a fixed system-level cgroup,
  `jarvis-turn.slice` (a slice unit kept active by systemd). Every process the turn starts
  inherits it, `sudo` children included unless PAM opens a new session (host check). The
  nftables rule matches `socket cgroupv2 level 1 "jarvis-turn.slice"`.
  - *Getting in:* moving a process between cgroups needs write access to the common
    ancestor, which for a system slice is root's. A root-owned helper,
    `/usr/local/lib/jarvis-agent/turn-cgroup`, allowed by one fixed `sudoers` line, moves
    exactly its caller's process into the slice after checking that the caller is the
    wrapper's child and runs as the configured user. The wrapper's child calls it and then
    executes `claude` in place, so the pid, its start time and its exec-time environment
    (what D36's trusted record is keyed on) stay those of the `claude` process.
  - *Getting out:* for the same reason a turn process without root cannot move itself out
    of a system slice (host check).
  - *Failing closed:* nftables resolves a cgroup path when the rule is loaded. A slice that
    is stopped and recreated gets a new id and the rule would stop matching, which fails
    open. So the helper compares the slice's current id with the id in the loaded rule at
    every turn start; when they differ, or the table is missing, the turn is refused
    (`egress_unavailable`) and the bridge answers it read-only, as for `disabled`.
  - The wrapper itself stays outside the slice: its notify ping, `pr_status_command` and
    `clone_command` are its own work, not the turn's.
- *Stage 2.* #45's turn user has its own uid; the rule becomes `meta skuid <turn user>`, and
  the cgroup helper goes away.
- *With `sudo`*, root can flush the table or read the proxy's secrets. In stage 1 that is
  one spoken yes away (class `root`), or zero through a script the gate does not read (D40).
  Stage 2's user has no `sudo` (open question 4).

**E3. The proxy is its own service under its own user.** A system user `jarvis-egress`, a
systemd unit `jarvis-egress.service` listening on `127.0.0.1` only, with
`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp`, no
capabilities, and `LoadCredential=` for the secrets (E7). It reads the policy at start and
on `SIGHUP`, and writes the log (E11). Its code lives in `/usr/local/lib/jarvis-agent/`,
root-owned, like the gate. It never runs as the turn's user and the turn's user cannot read
its memory, its credentials directory or its CA key.

**E4. The policy: their format, two additions.** `/etc/jarvis-agent/egress.toml`,
root-owned, world-readable (it holds names of secrets, never values), in the shape of
`PROTOCOL.md` section 7 written as TOML: `[[allow]]` rules with `origin`, `methods`,
`paths`, `allow_cidrs`, `credential = {header, placeholder, secret, prefix}`, and their
validation (exact canonical origins, unique, unknown keys rejected, a credential only on an
`https` origin, a secret named, never given, and present before the policy is accepted, a
policy that would expose a secret through a placeholder rejected, an invalid policy leaves
the running one in force). Two additions:
- `paths` may end in `/*` (a prefix), because their paths are "exact URL pathnames" and the
  forge's API is not: `/repos/<owner>/<name>/pulls/<n>/merge` has variable parts. A pattern
  is a literal prefix plus `*` at the end only, matched against the normalized path.
- `intercept = false|true` per rule (E5). Their HTTPS always terminates TLS.
Writing this file, the unit, the table or the credentials directory is in the gate's class
`self` (desk-only, never pending). Mark widens the list at the desk.

**E5. Pass through by name; intercept only where a rule needs it.** For a rule without a
credential and without `methods` or `paths`, the proxy answers `CONNECT host:443` by
checking the host against the list, resolving it itself, checking the address (public only,
unless `allow_cidrs`), and then relaying bytes. It reads the TLS ClientHello and refuses a
server name that differs from the `CONNECT` host. It never sees the request. Interception
(E6) is used only for a rule with a credential or with method or path limits.
Why: Claude Code's own model traffic must not be decrypted, slowed or broken by a
prototype in the middle of every turn (speed is a goal of its own, roadmap "voice and
speed"); package registries need no credential. The price: on a pass-through host every
method and path is allowed. That is said in the list's comments.

**E6. Interception and its CA.** At start the proxy makes a CA whose key stays in memory,
valid for a few days and remade at every start, and writes the public certificate,
appended to a copy of the system bundle, to `/run/jarvis-egress/ca-bundle.pem`. The turn's
environment points `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`,
`GIT_SSL_CAINFO` and `NODE_EXTRA_CA_CERTS` at it (a bundle, not the CA alone, so pass-through
hosts still verify against the real roots). Nothing is added to the system store, and the
coding session never trusts it. Leaf certificates are made per host, short-lived. The
upstream connection is a fresh TLS connection verified against the system store. The
proxy asks for identity encoding and refuses an upstream that ignores it on an intercepted
host (as `external-sandbox-v86` does), so literal redaction works.

**E7. Credentials (stage 2).** The secrets are files under `/etc/jarvis-egress/credentials/`
(root, 0600), handed to the service with `LoadCredential=`; the policy names them. The
turn's environment and configuration hold placeholders: random per install, never the word
"placeholder". The rules follow theirs: the whole header must equal the placeholder or the
request is denied; the header becomes `prefix + secret`; never added on a redirect, which is
authorized again; literal echoes of the secret in a response or in the log are redacted.
Each credential rule names its methods and paths. The ones in scope:
- **GitHub API.** `gh` with `GH_TOKEN=<placeholder>` sends it in `Authorization`; the exact
  form gh sends is a build-time check, and the rule's `prefix` follows it.
- **git over https.** The turn user's credential helper answers the placeholder as password;
  git sends `Authorization: Basic base64(user:placeholder)`; the rule matches that whole
  value and replaces it with the encoded real pair (the secret file holds the encoded value).
  Paths: `/<owner>/<name>.git/info/refs`, `/git-upload-pack` (fetch, which is a `POST`) and,
  once Mark agrees, `/git-receive-pack` (push), limited by prefix to own owners.
- **Secrets client** (OpenBao/Vault), if turns use it: `X-Vault-Token`. The secrets it reads
  still reach the turn: replacement protects the token, not what it unlocks.

**E8. ssh does not go through the proxy.** It is not HTTP, and its authentication is a
signature in the handshake, not a header.
- *Git to GitHub* moves to https for the turn user (`url."https://github.com/".insteadOf`
  for the ssh forms), so E7 covers it. The gate already names
  `https://<host>/owner/name` as a push target (`agent_host/pushaddr.py`); the push hash
  includes the https address after the rewrite, which is what git will use.
- *The remaining hop* (the cluster's jump host, and any other listed host): the key moves
  out of the turn user's home into an `ssh-agent` run by another user, whose socket the turn
  user may use. The turn can make signatures with the key; it cannot read or copy it. The
  firewall (E1) allows port 22 only to the listed destinations, so the key is usable only
  there. `ssh` stays judged by the gate (it unwraps the inner command).
- Alternatives considered: tunnelling ssh over `CONNECT` (no gain: the proxy sees only
  bytes); short-lived ssh certificates (good, more moving parts; later if wanted).

**E9. `kubectl`.** Today it runs on the jump host, behind the ssh hop: its credentials are
there and outside the proxy's reach, covered by E8. If a local `kubectl` were ever added:
- `kubectl` reads proxy settings, but it verifies the API server against the CA in its
  kubeconfig, not the system store. Interception needs that kubeconfig to name the proxy's
  CA, with the proxy verifying upstream against the real cluster CA (and an `allow_cidrs`
  rule: the API server is a private address).
- A **bearer token** (a ServiceAccount token or an OIDC token) is a header and can be
  replaced like the GitHub token.
- A **client certificate** (mutual TLS, the usual admin kubeconfig) cannot: it is presented
  in the TLS handshake to whoever terminates TLS. A proxy that terminates TLS would have to
  present the certificate itself on the upstream connection, and hold the cluster-admin key.
  Their protocol has no such feature.
- Upgrades (`exec`, `attach`, `port-forward`) do not pass an intercepting proxy that does
  not support them.
So: keep the hop. If a local `kubectl` comes, give it a ServiceAccount token with narrow
RBAC (read-only for voice), replaced at the proxy, never an admin client certificate.

**E10. Claude Code's own login.** It stays with the turn, in its credentials file, and its
traffic passes through by name (E5): Claude Code refreshes the login itself and writes it
back, and it needs several Anthropic hosts. A placeholder for a long-lived token, replaced
at the proxy, might work but is unproven: it needs interception of every model request and
a host check of what Claude Code sends where. Open question 7; not built.

**E11. The log and the record.** The proxy writes one JSON line per request, with their
`Event` fields (time, method, origin, path without query or fragment, status, decision,
reason, duration, credential injected), never bodies or headers, credentials redacted, to
`/var/log/jarvis-egress/events.jsonl`, readable by the turn's user through a group (nothing
secret is in it). Pass-through connections are logged with `method = "CONNECT"`. The
kernel logs dropped direct attempts with the prefix `jarvis-egress-drop`. Turns are one at
a time (`turn.lock`), so the wrapper adds to the turn record a summary of the events in the
turn's window: allowed and denied counts and the denied origins. Kept for `retention_days`.
A denied request gets `403` with a fixed body ("jarvis egress: <method> <origin><path> is
not on the list"), so the agent can say in words what it could not reach.

**E12. What changes for the gate.** Its rules and read-backs do not change. Added:
- class `self` for `/etc/jarvis-agent/egress.toml`, `/etc/jarvis-egress/`, the unit, the
  nftables configuration, the `turn-cgroup` helper and its `sudoers` line;
- the turn settings pin `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` and the CA variables, like
  `PATH` and `HOME`, so a checkout's `env` cannot point a turn elsewhere; the gate's
  whole-environment comparison then notices any change;
- the installer refuses `mode = "enforce"` when the table, the slice or the service is
  missing (as it refuses turn settings that name a missing gate).

**E13. `CLAUDE_CODE_SHELL_PREFIX`: not now.** `readonly` turns have no `Bash` at all, so the
prefix has nothing to do there. In `full` mode it would also wrap shell hooks
(`claude-code/README.md`), that is the gate and the action log: the gate's D36 check walks
`/proc` from the hook up to `claude`, which a sandbox with its own pid namespace breaks.
`Read` and `Edit` would still run on the host as the same user. A separate turn user (#45)
puts the file tools behind the same wall as `Bash`. If a sandbox is wanted later, it is its
own change, with the gate moved out of the prefix's reach.

**E14. Build a small proxy; take their format, semantics and tests.**

| Option | For | Against |
|---|---|---|
| Run one of their servers | Their whole stack, conformance-tested | The proxy is inside a sandbox server and "belongs to one VM"; the post runs two of the three on Apple silicon Macs; Jarvis has no guest |
| Lift their Python modules (`proxy.py`, `proxy_policy.py`, `proxy_certificates.py` of the Quicksand server; Apache-2.0) | Their exact credential semantics, already tested | About 45 KB of prototype code to own (the repositories date from October 2026, announced 16 November); `aiohttp`, `h11`, `cryptography`, `truststore`; built around their guest relay |
| `mitmproxy` with an addon | Mature TLS and HTTP handling, HTTP/2 and WebSockets | A large dependency; its own CA handling to tame; no policy format of its own |
| **Own small proxy** | Pass-through (stage 1) is small in the standard library; fits the root-owned tree and rules-as-data; only what Jarvis needs | Interception code is where bugs hide; ours is not audited either |

Recommendation: own proxy for stage 1, with their policy format and validation. For stage
2's interception, choose between lifting their modules and `mitmproxy` after stage 1, in a
PR with the reasons (open question 6). Either way port their proxy test cases
(`src/external_sandbox/suite/test_proxy.py`: wrong placeholder denied, echo redacted,
redirect authorized again, private address denied, policy update revokes connections) as
our own tests; their runner itself cannot run against us, because it drives requests
through a guest's exec API.

**E15. Host checks before `enforce`.** Each with its consequence:

| Check | If no |
|---|---|
| The agent host may load its own nftables table and `socket cgroupv2` matches (a container host may not allow it) | stage 1 waits for #45's uid match |
| A turn process without root cannot leave `jarvis-turn.slice` | stage 1 waits for #45 |
| A recreated slice makes the next turn `egress_unavailable`, never unfiltered | stage 1 does not ship |
| `curl --noproxy '*' https://example.com` from a turn is dropped and logged | stage 1 does not ship |
| Claude Code works through the proxy (model traffic, OAuth refresh, connectors), and `startup_gap_ms` and `claude_wall_ms` stay within noise | find what it needs; no `enforce` until it does |
| `WebSearch` works with a closed list; `WebFetch` to an unlisted host fails with the proxy's reason | the voice prompt says web pages are not reachable |
| The gate and the action log behave as before (checks 4 to 6 and 12 of task 1.17 again) | stage 1 does not ship |
| `sudo` children stay in the slice | the how-to says that `sudo` leaves the filter |
| Stage 2: `gh` and git work with placeholders through the proxy; `Read` of the proxy's secrets fails as the turn user; the `ssh-agent` key cannot be copied | stage 2 does not ship |

## Risks / Trade-offs

Verbatim from the sources, because they apply to us as much as to them:

- The post: "These sandboxes should still be considered experimental prototypes: they are
  an effective demonstration of the external sandbox pattern, but they have not been
  audited to a level that you should trust them with sensitive workloads."
- The post: "An allowed host could still echo a credential back in its response, so only use
  credential rules with services you trust, and keep their methods and paths narrow."
- `PROTOCOL.md`: literal echo redaction "does not protect against an upstream that
  deliberately transforms the secret".
- `COVERAGE.md`: "A green result is scoped to the selected profiles and listed checks, not a
  security certification"; DNS rebinding and resource exhaustion "need separate security
  testing".
- The post: "Claude Code's read and edit tools do not run via the sandbox."

Ours:

- **Allowed write hosts carry data out.** A gist, an issue comment or a push takes any file
  the turn can read. The lethal-trifecta argument of the post holds: the list narrows it,
  the gate's read-back covers the gated classes, nothing removes it while the forge allows
  writes.
- **Pass-through hosts allow every method.** Kept to hosts that need no credential and that
  Mark accepts as such (Anthropic, package registries).
- **The proxy is the new crown jewel** in stage 2: tokens and a CA key in one process.
  Small, own user, hardened unit, key in memory, CA trusted only by turns.
- **Fail-open paths.** A slice recreated under a loaded rule (E2 checks it every turn); a
  proxy that is down (the firewall still drops, so a dead proxy fails closed); a mode left on
  `observe` (the record and the how-to say which mode ran).
- **Breakage costs trips to the desk.** E15 and stage 0 find most of it before `enforce`.
- **`sudo`** (E2): stated, not solved, until stage 2.

## Migration

- `mode = "off"` is the default; installing the new version changes nothing until the option
  and the mode are set.
- Stage 0 to 2 as in the proposal. Each stage is its own PR with its host checks recorded in
  the agent-host how-to, as task 1.17 was.
- Rolling back: `mode = "off"` and `systemctl stop jarvis-egress`; the table is removed with
  it. Stage 2 rolls back only together with #45.

## Open Questions

In the proposal, numbered 1 to 7, each with its default. Settled by host checks: E15.
