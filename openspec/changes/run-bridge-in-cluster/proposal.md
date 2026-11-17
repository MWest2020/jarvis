# Change: run-bridge-in-cluster

## Why

The bridge has only run on a workstation, and the first tailnet test (2026-11-14) showed
that a small machine cannot carry it: torch, faster-whisper and Kokoro hold about 1.8 GB
resident, and with other work on the same host a Kokoro render took minutes and a Claude
turn timed out. Mark (2026-11-14): "Dit moet 1 van de nodes worden. die moeten genoeg
hebben" (it must be one of the cluster nodes; they have enough). So the bridge should run
as a container on a Kubernetes cluster, still reachable only over the tailnet, so the phone
can use it while the workstation is off or busy.

Today the bridge refuses to start in a pod: its bind check accepts only a Tailscale address
or loopback, and a pod has neither. A pod has a private pod IP; the tailnet reaches it
through the Tailscale Kubernetes operator's proxy.

## What Changes

- **Bind check:** an explicit opt-in, `JARVIS_BEHIND_TAILSCALE_PROXY=1`, lets the bridge
  listen on a private address (RFC 1918 or CGNAT), so on its pod IP. Never a wildcard,
  never a public address. The opt-in is logged as a warning at every start. Without it,
  nothing changes.
- **Container image:** `deploy/Dockerfile` with Python 3.12 (uv, `uv sync --frozen`),
  Node and a pinned Claude Code CLI, the faster-whisper and Kokoro weights downloaded at
  build time, running as a non-root user. A GitHub Actions workflow builds it on every PR
  and pushes `ghcr.io/mwest2020/jarvis-bridge:<sha>` on main.
- **CPU-only torch on Linux:** the lockfile takes torch from PyTorch's CPU index on Linux,
  and the spaCy model Kokoro's G2P needs is locked as a dependency instead of being
  pip-installed at first use.
- **Project sync:** `deploy/sync-projects.sh` clones or refreshes a list of GitHub
  repositories as shallow working copies (an init container runs it at every start), so
  `JARVIS_PROJECTS` can point at them.
- **Docs:** `docs/how-to/run-in-cluster.md` describes the deployment pattern: the image,
  the tailnet-only Service through the Tailscale operator, the NetworkPolicy, the secrets.

The cluster manifests themselves (namespace, Deployment, Service, NetworkPolicy, secrets)
live in the operator's own GitOps repository, not here: nothing of one person's setup goes
into this repository (AGENTS.md).

## Impact

- Modified capability: `voice-bridge`, requirement Network exposure (same promise, a second
  way to keep it).
- Affected code: `bridge/config.py`, `bridge/server.py` (logging only), `pyproject.toml`,
  `uv.lock`; new `deploy/`, `.github/workflows/image.yml`.
- The read-only tool allow-list, the token check and everything the phone sees stay the same.
