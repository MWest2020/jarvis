# Design: run-bridge-in-cluster

## Context

- The cluster runs the Tailscale Kubernetes operator. It puts a Service of type
  `LoadBalancer` with `loadBalancerClass: tailscale` on the tailnet: a proxy pod in the
  operator's namespace joins the tailnet under the Service's `tailscale.com/hostname` and
  forwards to the Service.
- The bridge holds about 1.8 GB resident once both speech models are loaded.
- Headless Claude Code needs credentials (`CLAUDE_CODE_OAUTH_TOKEN`) and the projects it
  answers about as files on disk.
- The phone (HTTP Shortcuts) already talks plain HTTP to port 8787 over the tailnet.

## Goals / Non-Goals

- Goals: the bridge as a container that starts without internet for its models; reachable
  only over the tailnet plus the token, exactly as on a workstation; read-only copies of a
  set of projects, refreshed at every start.
- Non-Goals: horizontal scaling (one replica, the session file is local); TLS on top of the
  tailnet (D2's departure from the first change still holds); write access for Claude Code
  (D6 is unchanged); a public endpoint of any kind (no Funnel).

## Decisions

**D1. Listen on the pod IP behind an explicit opt-in.** In a pod there is no Tailscale
interface to bind to. The bridge binds to its pod IP (from the Downward API) only when
`JARVIS_BEHIND_TAILSCALE_PROXY=1` is set; then RFC 1918 (10/8, 172.16/12, 192.168/16) and
CGNAT (100.64/10) addresses are accepted as well. The value must be exactly `1`; anything
else but unset or `0` is an error. Wildcards and public addresses stay refused with or
without the opt-in. The opt-in logs a warning at start (stderr and the bridge log), so it
is never on by accident. Alternative considered: a Tailscale sidecar in the bridge pod so
the bridge can bind to a real Tailscale address. Rejected: a second tailnet node with its
own auth key per app, while the operator already owns tailnet membership in the cluster.

**D2. Tailnet-only Service plus a NetworkPolicy keep the Network exposure promise.** The
pod IP is reachable from the whole cluster network by default, so the bind address alone no
longer limits who can connect. Two things do: (a) the only way in from outside the cluster
is a `LoadBalancer` Service with `loadBalancerClass: tailscale` and hostname `jarvis`, with
no Funnel annotation and no NodePort (`allocateLoadBalancerNodePorts: false`), so it exists
on the tailnet only; (b) a NetworkPolicy admits ingress to the bridge's port only from the
Tailscale operator's proxy pod for this Service, selected by namespace and by the proxy's
pod labels: the operator's namespace also holds public Funnel proxies for other apps.
Egress is limited to DNS and HTTPS (GitHub, the Anthropic API). Kubelet probes come from
the node and are not affected. The token is still checked on every
request. Plain HTTP on port 8787, like on a workstation: the tailnet already encrypts, and
the phone's shortcut stays the same apart from the host name.

**D3. One image, everything baked in.** Base `node:22.23.5-bookworm-slim` (Node for Claude
Code), uv-managed CPython 3.12, `uv sync --frozen --no-dev`, Claude Code pinned
(`CLAUDE_CODE_VERSION`, the version the bridge's argv was tested with). The model weights
are fetched at pinned Hugging Face commits (`deploy/fetch_models.py`: Systran/faster-whisper-base
and hexgrad/Kokoro-82M by commit SHA, with the cache's `refs/main` pointed at them). Then the
bridge's own warm-up (`stt.warm_up`, one Kokoro render per voice in `KOKORO_VOICES`) runs
offline, so a file the pin misses fails the build. The running container sets
`HF_HUB_OFFLINE=1`: a pod start fetches no models. CI runs the built image with a read-only
root, only `/tmp` writable and no network, and checks that the bridge starts and answers 401
without a token. The image runs as uid 10001. Claude Code's auto-updater and
non-essential traffic are off. The same image runs the project sync as an init container.

**D4. CPU-only torch on Linux.** PyPI's Linux torch wheel pulls in about 5 GB of CUDA
libraries the bridge never uses (Whisper runs int8 on CPU, Kokoro on CPU). `pyproject.toml`
takes torch from PyTorch's CPU index on Linux (`[tool.uv.sources]`, explicit index); other
platforms keep PyPI's wheel. torch is listed as a direct dependency only so the source
applies. The spaCy model `en_core_web_sm` that misaki (Kokoro's G2P) downloads with pip at
first use is locked as a URL dependency instead, so the image needs neither pip nor
internet for it.

**D5. Projects as shallow, read-only copies, refreshed at start.** `deploy/sync-projects.sh`
takes `JARVIS_SYNC_REPOS` (owner/name list) and keeps one shallow clone per repo in
`JARVIS_SYNC_DIR`. Existing copies are reset to the remote default branch. A token for
private repos (`GIT_PAT`) is read by a git credential helper from the environment at run
time, so it is never written into `.git/config`, and it is set only in the init container,
never in the bridge container (Claude Code's Read tool could otherwise read it from the
process environment). A refresh failure keeps the old copy; a repo with no copy fails the
init container. The bridge container mounts the copies read-only, on top of Claude Code's
own read-only tool allow-list, which is unchanged. Alternative considered: refresh on a
timer while the bridge runs. Deferred: a restart refreshes, and a timer adds a second
writer next to a running turn.

**D6. Persistent state.** One volume holds the copies and the state: the session file, the
bridge log and Claude Code's home directory (its session transcripts, which `--resume`
needs). A restart keeps every project's conversation. The bridge log rotates (10 MB, three
old files, all `0600`). Transcripts and full replies go to that file only; stdout, which a
cluster turns into `kubectl logs`, gets the summary lines.

**D8. File tools stay out of /proc; secrets are redacted on top (review).** The bridge is
PID 1 in its container with the same uid as its Claude Code child, so `/proc/1/environ`
holds `JARVIS_TOKEN` and the child's own environ holds `CLAUDE_CODE_OAUTH_TOKEN`. Removing
`JARVIS_*` from the child's environment does not hide them. Claude Code 2.1.322's Read tool
refuses `/proc`, but Grep on `/proc/self/environ`, or on a committed symlink to it, returned
the environment (reproduced with a canary). Every turn therefore runs with `--restricted`
(file tools confined to the working directory; user, project and local settings files are
ignored, so a cloned repo's `.claude/settings.json` hooks have no effect) and with
`Read(//proc/**)` in `--disallowedTools` (Grep and Glob follow Read deny rules). Retested:
direct paths, a file symlink and a directory symlink into `/proc` are all denied, and
`--resume` keeps working under `--restricted`. The bridge refuses to start when
`claude --help` does not list `--restricted` (minimum Claude Code 2.1.322). Behind that, the log handlers redact the
values of `JARVIS_TOKEN` and `CLAUDE_CODE_OAUTH_TOKEN` and anything shaped like `sk-ant-…`
(messages and tracebacks; a filter error never breaks logging), keep Claude Code's stderr
and unexpected replies in the file with only a short line on stdout,
and the spoken summary falls back to the fixed line when it contains `sk-ant-` or any run
of 32 or more token characters (`[A-Za-z0-9_-]`).

**D7. Image build in CI.** `.github/workflows/image.yml` runs the tests, then builds the
image on every PR (no push) and pushes `:<sha>` and `:sha-<short>` on main with
`GITHUB_TOKEN` (`packages: write`; the workflow's default is `contents: read`). The package inherits the repository's visibility. The
deployment pins the digest, never a tag.

## Risks / Trade-offs

- The pod IP is reachable from the cluster network if the cluster's CNI does not enforce
  NetworkPolicy. The how-to says so; on such a cluster this setup is not safe, and the token
  is the only check left.
- Credentials in process environments: `JARVIS_TOKEN` in the bridge's, `CLAUDE_CODE_OAUTH_TOKEN`
  in the Claude Code child's. D8 closes the known path (file tools on `/proc`) and redacts
  log and speech. What it cannot redact: if a future Claude Code version opened a new way to
  read them, a reply quoting a credential would still land in Claude Code's own session
  transcript under `$HOME/.claude`, which no bridge filter sees. Re-check D8 on every Claude
  Code bump (`CLAUDE_CODE_VERSION`).
- One replica with a node-local volume: if that node is down, the bridge is down until it
  is back. Acceptable for a personal tool.
- The image is large (about 3 GB: torch, models, Node). It is pulled once per node.

## Open Questions

- None blocking. A health route was not added: a TCP probe is enough, because the server
  only listens once both models are loaded.
