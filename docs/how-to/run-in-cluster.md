---
status: current
last_reviewed: 2026-11-14
---
# Running the bridge in a Kubernetes cluster

The bridge as one pod on a cluster that runs the
[Tailscale Kubernetes operator](https://tailscale.com/kb/1236/kubernetes-operator), reachable
only over the tailnet plus the token. Why and the decisions: the change
[`run-bridge-in-cluster`](../../openspec/changes/run-bridge-in-cluster/design.md). For a
workstation, see [`bridge-service.md`](bridge-service.md).

The manifests belong in your own cluster repository; this page gives the pieces.

## The image

`deploy/Dockerfile`, built by `.github/workflows/image.yml`: on every PR it is only built, on
main it is pushed as `ghcr.io/mwest2020/jarvis-bridge:<sha>` (and `:sha-<short>`). The run's
summary shows the digest. Pin the digest in the Deployment, never a tag:

```sh
docker buildx imagetools inspect ghcr.io/mwest2020/jarvis-bridge:<sha>   # Digest: sha256:…
```

What is in it:

- uv-managed Python 3.12 and `uv sync --frozen --no-dev`. torch comes from PyTorch's CPU
  index on Linux (no CUDA), and the spaCy model Kokoro needs is locked, not pip-installed at
  first use.
- Node and Claude Code, pinned (`CLAUDE_CODE_VERSION` build argument, 2.1.322 or newer:
  the bridge refuses to start without Claude Code's `--restricted` flag).
- The faster-whisper `base` weights and Kokoro with the `af_heart` voice, fetched at build
  time at pinned Hugging Face commits (`deploy/fetch_models.py`), then checked by running
  the bridge's own warm-up offline. The container sets `HF_HUB_OFFLINE=1`, so a pod start
  downloads nothing. Another voice means a rebuild with
  `--build-arg KOKORO_VOICES="af_heart bf_emma"`; another Whisper size also needs its
  commit added to `fetch_models.py`.
- `jarvis-sync-projects` (the project sync below).
- User uid 10001. Claude Code's auto-updater and non-essential traffic are off.

CI runs every build with `--read-only --tmpfs /tmp --network none` and `HOME=/tmp`: the
bridge must load both models, listen, and answer 401 without a token. That is the proof
that a read-only root filesystem and an offline start work.

The package is private when the repository is. The cluster needs an image pull secret with a
token that may read GHCR packages (`read:packages`).

Building locally needs about 4 GB of free disk and, for the warm-up step, about 2 GB of free
memory: `docker build -f deploy/Dockerfile -t jarvis-bridge .`

## Listening behind the Tailscale proxy

A pod has no Tailscale interface, so the bridge listens on its pod IP. That needs the opt-in:

| Variable | Value |
|---|---|
| `JARVIS_BEHIND_TAILSCALE_PROXY` | `1` (exactly). Allows a private bind address: RFC 1918 (10/8, 172.16/12, 192.168/16) or CGNAT (100.64/10). Wildcards and public addresses stay refused. Logged as a warning at every start. |
| `JARVIS_BIND_HOST` | the pod IP, from the Downward API (`fieldRef: status.podIP`) |

The opt-in is only safe with both of these in place, because a pod IP is reachable from the
whole cluster network by default:

1. **A tailnet-only Service**, no Funnel:

   ```yaml
   apiVersion: v1
   kind: Service
   metadata:
     name: jarvis
     annotations:
       tailscale.com/hostname: "jarvis"   # no tailscale.com/funnel annotation
   spec:
     type: LoadBalancer
     loadBalancerClass: tailscale
     allocateLoadBalancerNodePorts: false   # the proxy uses the ClusterIP; no NodePort
     selector: { app: jarvis }
     ports:
       - { name: http, port: 8787, targetPort: 8787 }
   ```

   The phone then calls `http://jarvis.<tailnet>.ts.net:8787/turn?project=<name>`.

2. **A NetworkPolicy** that admits the bridge's port only from the operator's proxy pod for
   this Service. The operator's namespace (`tailscale` by default) can also hold public
   Funnel proxies for other apps, so narrow the rule to the proxy's pod labels
   (`kubectl -n tailscale get pods --show-labels`; the operator labels each proxy with the
   Service it serves). The namespace-only form below is the starting point:

   ```yaml
   apiVersion: networking.k8s.io/v1
   kind: NetworkPolicy
   metadata:
     name: jarvis-tailscale-only
   spec:
     podSelector: { matchLabels: { app: jarvis } }
     policyTypes: [Ingress]
     ingress:
       - from:
           - namespaceSelector:
               matchLabels: { kubernetes.io/metadata.name: tailscale }
         ports:
           - { protocol: TCP, port: 8787 }
   ```

   Limit egress too: DNS (53) and HTTPS (443) are all the bridge and the project sync need
   (GitHub, the Anthropic API). This only works on a CNI that enforces NetworkPolicy
   (Cilium, Calico). Without that, do
   not use the opt-in. Tested on Cilium (2026-11-14): the operator's proxy gets through;
   a pod in another namespace, the LAN to the pod IP, and the LAN to a NodePort all time
   out. Host-network processes on the pod's own node can still connect (Cilium admits the
   local host for kubelet probes); the token covers that.

## Projects

An init container runs `jarvis-sync-projects` from the same image at every start:

| Variable | Meaning |
|---|---|
| `JARVIS_SYNC_REPOS` | Space-separated GitHub `owner/name` list. |
| `JARVIS_SYNC_DIR` | Where the copies go, one directory per repo name (default `/work`). |
| `GIT_PAT` | Optional token for private repos. Read by a git credential helper from the environment, never written to `.git/config`. Set it on the init container only, never on the bridge container: Claude Code's Read tool could read the process environment. |

Copies are shallow and reset to the remote default branch at every start. A repo that cannot
be refreshed keeps its old copy (warning in the init container's log); a repo with no copy
at all fails the init container. `JARVIS_PROJECTS` on the bridge container then points at
them, e.g. `demo=/work/demo,other=/work/other`. Mount the copies read-only in the bridge
container; Claude Code's tools are read-only anyway (`Read`, `Glob`, `Grep`).

## State and Claude Code

- `JARVIS_STATE_DIR` and `HOME` on a persistent volume: the session file, the bridge log and
  Claude Code's own session transcripts (which `--resume` needs) survive a restart.
- `CLAUDE_CODE_OAUTH_TOKEN` from a Secret, for headless Claude Code (`claude setup-token`).
- `JARVIS_TOKEN` from a Secret: the phone's token. Never in git.
- With `readOnlyRootFilesystem`, mount an `emptyDir` on `/tmp`.
- `automountServiceAccountToken: false` and `enableServiceLinks: false`: the bridge needs
  no Kubernetes API token and no environment variables for other Services. Claude Code's
  file tools are kept out of `/proc` and confined to the project directory (`--restricted`),
  but there is no reason to hand it more than it needs.
- Logs: `kubectl logs` shows request lines, summaries and short error lines, never
  transcripts, full replies, Claude Code's stderr or tracebacks; those are in `$JARVIS_STATE_DIR/bridge.log` (rotated at 10 MB, three old files).
  Both redact the token and the Claude credentials.

A sketch of the pod spec, one volume with two sub-paths (the init container creates them):

```yaml
initContainers:
  - name: sync-projects
    image: ghcr.io/mwest2020/jarvis-bridge@sha256:<digest>
    command: ["jarvis-sync-projects"]
    env:
      - { name: JARVIS_SYNC_DIR, value: /data/repos }
      - { name: JARVIS_SYNC_REPOS, value: "you/demo you/other" }
      - name: GIT_PAT
        valueFrom: { secretKeyRef: { name: git-pat, key: GIT_PAT } }
    volumeMounts:
      - { name: data, mountPath: /data }
containers:
  - name: bridge
    image: ghcr.io/mwest2020/jarvis-bridge@sha256:<digest>
    env:
      - { name: JARVIS_BEHIND_TAILSCALE_PROXY, value: "1" }
      - name: JARVIS_BIND_HOST
        valueFrom: { fieldRef: { fieldPath: status.podIP } }
      - { name: JARVIS_PROJECTS, value: "demo=/work/demo,other=/work/other" }
      - { name: JARVIS_STATE_DIR, value: /state/jarvis }
      - { name: HOME, value: /state/home }
      - name: JARVIS_TOKEN
        valueFrom: { secretKeyRef: { name: jarvis-token, key: token } }
      - name: CLAUDE_CODE_OAUTH_TOKEN
        valueFrom: { secretKeyRef: { name: claude-oauth-token, key: token } }
    volumeMounts:
      - { name: data, mountPath: /work, subPath: repos, readOnly: true }
      - { name: data, mountPath: /state, subPath: state }
```

The init container must also create `/data/state` (owned by uid 10001) so the second
sub-path exists; run it as `sh -c 'mkdir -p /data/state && exec jarvis-sync-projects'`.

## Resources and probes

- Memory: the bridge holds about 1.8 GB resident with both models loaded. A request of
  2.5 Gi and a limit of 3 Gi leave room for a turn.
- There is no health route. Use a TCP probe on 8787: the server only listens once both
  models are loaded, so an open port means ready. A startup probe with a few minutes of
  slack covers the model load on a slow CPU.

## Checking it

From a machine on the tailnet:

```sh
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  "http://jarvis.<tailnet>.ts.net:8787/turn?project=demo"            # 401: no token
curl -X POST "http://jarvis.<tailnet>.ts.net:8787/turn?project=demo" \
  -H "Authorization: Bearer $JARVIS_TOKEN" --data-binary @question.wav -o reply.wav
```

And from a pod in another namespace, the pod IP on 8787 must time out (the NetworkPolicy).
The bridge's start log shows the `JARVIS_BEHIND_TAILSCALE_PROXY=1` warning.
