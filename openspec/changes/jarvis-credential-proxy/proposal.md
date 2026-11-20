# Change: jarvis-credential-proxy

**Status: approved by Mark on 2026-11-18 by mail ("defaults en proposals ok", #83); each of the seven open questions takes its default. Built in stages, observe first; stage 0 is built ([`tasks.md`](tasks.md)).**

## Why

Mark, 2026-11-17: read "External sandboxes for coding agents" (Prime Radiant blog, Simon
Willison, 16 Nov 2026, https://blog.primeradiant.com/external-sandboxes-for-coding-agents/)
and the protocol of https://github.com/prime-radiant-inc/external-sandbox (`PROTOCOL.md`,
section 7, "Managed HTTP(S) proxy"). Two ideas there fit `full` mode, where Claude Code
runs on the agent host with real credentials and the gate decides per call:

1. **Credential replacement.** The agent's environment holds only placeholders
   (`GITHUB_TOKEN=placeholder-…`). An egress proxy on the host puts the real secret in,
   only for allow-listed hosts and methods. A prompt-injected turn while driving cannot read
   or leak a token it never had.
2. **Default-deny egress** with a per-host, per-method allow-list and a request log. The
   gate judges commands; the proxy judges what leaves the machine.

What `full` mode trusts today is written down in `jarvis-full-agent` (D31, D39, D40) and in
[`docs/how-to/agent-host.md`](../../../docs/how-to/agent-host.md#what-this-trusts): the turn
runs as the coding session's own user, with that user's GitHub login, ssh keys and secrets
client. The gate reads the text of a call. It does not see a script that makes the same
request, and "reading is not gated: data can leave in the address of a plain `curl` GET or a
web fetch". D40 names the credential broker of #45 as the real fix (Mark, 2026-11-16: "a
now, b later").

**The finding that shapes this proposal.** Credential replacement protects only a secret the
turn cannot reach another way. Today the turn runs as the user whose home holds the GitHub
token, the ssh keys and Claude Code's own login: `cat`, Claude Code's `Read` tool,
`gh auth token` and `/proc` of any process of that user all reach them. The post's own
implementation guide says the same: "Host-side agent tools must not be able to read the
proxy's credentials through another route. A shell adapter alone does not restrict host
file tools." (`external-sandbox-v86`, `docs/proxy.md`). So:

- **idea 2 (egress) can ship on its own**, for the turn's processes, without touching the
  credentials;
- **idea 1 (credentials) needs the turn to run as a user that cannot read the real secrets**:
  the separate turn user of #45. Without that user it would be theatre, and it is not built.

## What Changes

- **An egress proxy on the agent host.** A small service under its own system user, on
  loopback, root-owned code and configuration. It reads an allow-list from a root-owned
  file and writes a request log.
- **Default-deny for the turn, enforced by the kernel.** On a host there is no virtual
  machine around the agent, so `HTTPS_PROXY` is only a hint a program can ignore. A
  root-owned nftables table drops every outbound connection of the turn's processes except
  to the proxy and to a short list of ssh destinations. It matches the turn by its cgroup
  (stage 1, same user as today) or by the turn user's uid (stage 2, #45). The coding
  session at the desk is not affected.
- **An allow-list per host, method and path**, as data (`/etc/jarvis-agent/egress.toml`),
  in the policy shape of the external-sandbox protocol plus two additions (design E4).
  Writing it is in the gate's class `self`: never by voice.
- **No TLS interception unless a rule needs it.** Hosts without a credential or a method
  rule (Claude Code's own API traffic, package registries) pass through by name. Only hosts
  with a credential or a method rule are intercepted, with a short-lived CA that only the
  turn's processes trust.
- **Credential replacement** (stage 2) for credentials that travel in an HTTP header: the
  GitHub API, git over https, the secrets client. The proxy holds them as systemd
  credentials; the turn holds placeholders.
- **ssh does not fit an HTTP proxy.** Git to GitHub moves to https for turns, so the proxy
  covers it. The remaining ssh hop (the cluster's jump host) uses a key held by an
  `ssh-agent` of another user: the turn can use the key, not copy it, and the firewall
  limits where it can use it.
- **A request log**: every request the proxy allowed or denied (time, method, origin, path
  without query, status, decision, reason, whether a credential was put in), the kernel's
  log of direct connection attempts, and a per-turn summary in the turn record.
- **Opt-in**: `[egress] mode = "off" | "observe" | "enforce"` in `config.toml`, default
  `"off"`.
- **The installer grows**: a system user, a systemd unit, an nftables table and two files
  under `/etc/jarvis-agent/`, behind an explicit option. Today it writes two root-owned
  directories and nothing else (agent-host how-to).
- No change to the bridge, the phone, the wire contract, the gate's rules or the
  read-backs. `readonly` mode is unchanged: it runs in the bridge's pod with `Read`, `Glob`
  and `Grep`, talks only to the Anthropic API, and the cluster's NetworkPolicy already does
  this job there.

## Gate and proxy: who decides what

| | Gate (unchanged) | Proxy and firewall (new) |
|---|---|---|
| Looks at | The text of a tool call, before it runs | Connections and HTTP requests, as they leave |
| Decides | Gated or not; class; read-back; released once by a spoken yes | Allowed or denied by host, method, path; credential in or not |
| Sees a script, `make` target, alias, SDK call | No (D39, D40) | Yes: every connection of the turn's processes |
| Sees a read (`GET`, web fetch) | No: reads are not gated | Yes: only listed hosts can be reached |
| Knows what Mark meant | Says what will run, in one sentence | Nothing; it knows only the list |
| Inside an allowed host | Judges the verb (`gh pr merge`, `kubectl delete`) | Judges method and path only; GitHub is one host for an issue comment and a merge |

Together: the gate keeps the spoken yes for irreversible actions; the proxy limits where
anything can go, including what the gate never sees. Neither replaces the other.

## Credential coverage

| Credential | Today | With this change | How |
|---|---|---|---|
| GitHub API (`gh`) | gh's stored login in the user's home | Placeholder in the turn (stage 2) | Header substitution on `api.github.com`, intercepted, methods and paths listed |
| git over https | Not used for GitHub (gh's git protocol is ssh) | Used by turns (stage 2) | Header substitution on `github.com` for the smart-HTTP paths; push limited to own owners |
| git over ssh | Keys in the user's `~/.ssh` | Not used for GitHub by turns | Replaced by https for GitHub; elsewhere see the next row |
| ssh hop to the cluster's jump host | Key in `~/.ssh` | Key in an `ssh-agent` of another user | Use without possession; nftables allows port 22 only to listed hosts |
| `kubectl` | Not on the agent host; runs on the jump host behind the ssh hop | Unchanged | Covered by the ssh row; a local `kubectl` is not added (design E9 on why a header proxy cannot swap a client certificate) |
| Secrets client (OpenBao/Vault) | Token of the user | Placeholder (stage 2, if used by turns) | `X-Vault-Token` header substitution. The secrets it reads still reach the turn. |
| Claude Code's own login | Subscription credentials in the user's `~/.claude` | Unchanged; traffic passes through without interception | Out of scope: Claude Code refreshes and rewrites its own login. A later substitution is an open question, unproven |
| Tailscale | Root's daemon and state; the turn's user cannot read them | Unchanged | Not HTTP. Inbound ssh from the bridge is untouched; outbound to tailnet addresses only by explicit rule |

## What breaks

With `mode = "enforce"`, in a voice turn (the desk is not affected):

- **Web lookups to hosts not on the list.** `WebFetch`, and so the read-only `web-fetch`
  helper of D58, works only for listed hosts. `WebSearch` is answered on Anthropic's side
  and keeps working (to confirm in a host check). Open question 2.
- **Package installs and `npx`-started tools** need their registry on the list.
- **MCP servers and hooks that call out** (a notification script, a build trigger) need a
  rule each, as do remote MCP connectors (`mcp-proxy.anthropic.com`).
- **ssh, scp and rsync** reach only listed hosts. An `outward` copy that Mark confirmed by
  voice to a host not on the list fails.
- **Clients that ignore the proxy settings** fail instead of connecting directly: by design.
  So do certificate-pinned clients on intercepted hosts, HTTP/2-only clients, WebSocket and
  other upgrades through an intercepted host (`kubectl exec` would be one, if `kubectl` ran
  locally).
- **DNS**, if it is closed for the turn (open question 5): a program that resolves a name
  before it uses the proxy fails.
- **Stage 2 only:** `gh` and git work only through the paths on the list; `gh auth token`
  prints a placeholder; git to GitHub runs over https; `sudo` is gone from the turn user
  (open question 4), so root-class actions become desk-only.

## Reuse or build

Their proxy does not lift out as a program. In `external-sandbox-v86` "the proxy runs inside
the same Node.js process as the API and v86. There is no separate proxy daemon", and "the
proxy belongs to one VM"; the Quicksand and Apple Container servers carry their own copies,
tied to their guest transport, and the post runs both on Apple silicon Macs. Jarvis has no
guest: the turn runs on the host.

**Recommendation: build a small proxy, and take from them what is worth most: the policy
format, its semantics and its test cases.** Their rules are precise and tested (exact
canonical origins, unknown keys rejected, credentials only over HTTPS and only by
environment-variable name, the whole header must equal the placeholder, public addresses
only unless a rule lists CIDRs, a redirect is authorized again, literal echoes redacted,
identity encoding so redaction cannot be bypassed by compression). The repositories are
Apache-2.0. Stage 1 (pass-through by name) is small in the Python standard library. For
stage 2's interception, the build either lifts their Python certificate and proxy modules
(about 45 KB; `aiohttp`, `h11`, `cryptography`, `truststore`) into the proxy's own
environment, or uses `mitmproxy` with a short addon; that choice is made after stage 1
(open question 6). Design E14 has the comparison.

## Risks and caveats

- **Their code is a prototype, not audited.** The post: "These sandboxes should still be
  considered experimental prototypes … they have not been audited to a level that you
  should trust them with sensitive workloads." Their coverage notes: "A green result is
  scoped to the selected profiles and listed checks, not a security certification." The
  same will be true of ours.
- **An allowed host can echo a credential back.** The post: "An allowed host could still
  echo a credential back in its response, so only use credential rules with services you
  trust, and keep their methods and paths narrow." Literal redaction "does not protect
  against an upstream that deliberately transforms the secret" (`PROTOCOL.md`, section 7).
- **An allowed host that accepts writes is a way out for data.** Credential replacement
  keeps the token in; it does not keep in what the turn can read. A gist, an issue comment
  or a push carries any file the turn can open. Narrow methods and paths reduce it; the
  gate's read-back still covers the gated classes.
- **TLS interception is a new trust.** The proxy can read and change everything on the
  hosts it intercepts, holds the real tokens and a CA key. It becomes the most sensitive
  process on the host: own user, key in memory only, CA trusted by the turn's processes
  only, never by the system store, regenerated at every start.
- **Claude Code's `Read` and `Edit` run on the host.** The post: "Claude Code's Read and
  Edit tools still run on the host … The mount rules only constrain Bash." This is why
  credential replacement waits for a separate turn user: as that user, `Read` cannot open
  the real secrets either.
- **`sudo` makes it a seatbelt.** A turn with passwordless `sudo` can read the proxy's
  secrets or flush the firewall after one spoken yes (class `root`), or through a script the
  gate does not read (D40). Stage 1 says so; stage 2 removes `sudo` from the turn user.
- **Same user, stage 1.** A turn could try to leave its cgroup. The design puts the cgroup
  where the turn's user cannot move processes out of it (E2); a host check proves it.

## Rollout

0. **Observe.** `mode = "observe"`: the proxy runs, the turn's environment points at it,
   everything passes through by name and is logged. Nothing is enforced. A week of voice
   turns gives the real list.
1. **Enforce egress** (no new user): the firewall drops what is not the proxy or a listed
   ssh destination, for the turn's cgroup; the proxy allows listed hosts by name. Host
   checks first (E15). Credentials as today.
2. **Credentials** (with #45's turn user): the turn user holds placeholders, GitHub over
   https through the proxy, the jump host's key in an `ssh-agent` of another user, no
   `sudo`. Read-only first: GitHub rules start with `GET`/`HEAD` and the fetch paths of git;
   write methods and push paths are added per path after Mark's yes on the list.
3. **Later, not in this change:** writes on the forge only with a fresh grant (#45's broker,
   #22's fingerprint), checked by the proxy at the request.

The kill switch (`enabled = false`) still stops every turn; `mode = "off"` stops only the
proxy's part.

## Open questions for Mark

1. **Staging.** Stage 1 (egress enforcement on the turn's cgroup, same user as today) before
   #45, and stage 2 (credentials) only together with #45's turn user? *Default: yes, as
   proposed.*
2. **Web lookups by voice.** A closed list (`WebFetch` only to listed hosts; `WebSearch`
   unaffected), or "any host, `GET` only", which needs interception of every other host and
   keeps the data-in-the-address hole D39 names? *Default: closed list.*
3. **git to GitHub over https for turns** (needed for the proxy to hold the credential),
   ssh kept only for the jump host? *Default: yes.*
4. **`sudo` for the turn.** Keep it in stage 1 (and say it is a seatbelt), and give the
   stage 2 turn user none (root-class actions become desk-only)? *Default: yes.*
5. **DNS for the turn.** Closed (names resolved by the proxy only; closes DNS as a way out;
   some tools fail), or open to the local resolver? *Default: closed, and stage 0 shows what
   breaks.*
6. **Interception engine for stage 2.** Lift their Python modules, or `mitmproxy` with an
   addon? *Default: decided after stage 1, in a PR, with the reasons.*
7. **Claude Code's own login.** Leave it with the turn (passed through, not intercepted), or
   try a placeholder for a long-lived token later, after a host check? *Default: leave it.*

## Not in this change

- **`CLAUDE_CODE_SHELL_PREFIX`.** It routes every Bash call through a prefix script and could
  run commands in a sandbox. Not now (design E13): `readonly` turns have no Bash; in `full`
  mode the prefix "also applies to other shell invocations such as shell hooks"
  (external-sandbox, `claude-code/README.md`), so it would wrap the gate, whose D36 check
  walks `/proc` from the hook up to `claude`; and `Read`/`Edit` would still run on the host.
  A separate turn user covers the file tools too.
- **The broker of #45** (separate user, writes only with a grant). This change is designed
  as its HTTP half and depends on its user for stage 2.
- Anything in the bridge, the phone or the gate's rules.
