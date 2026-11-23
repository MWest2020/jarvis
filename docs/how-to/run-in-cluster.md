---
status: current
last_reviewed: 2026-11-15
---
# Running the bridge in a Kubernetes cluster

The bridge as one pod on a cluster that runs the
[Tailscale Kubernetes operator](https://tailscale.com/kb/1236/kubernetes-operator), reachable
only over the tailnet plus the token. Why and the decisions: the change
[`run-bridge-in-cluster`](../../openspec/changes/archive/2026-11-23-run-bridge-in-cluster/design.md). For a
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
- The faster-whisper `base` weights and Kokoro with the `bm_george` (default, British male) and `af_heart` voices, fetched at build
  time at pinned Hugging Face commits (`deploy/fetch_models.py`), then checked by running
  the bridge's own warm-up offline. The container sets `HF_HUB_OFFLINE=1`, so a pod start
  downloads nothing. Switching between the two baked voices is an env change
  (`JARVIS_KOKORO_VOICE=af_heart`); any other voice means a rebuild with
  `--build-arg KOKORO_VOICES="bm_george af_heart bf_emma"`; another Whisper size also needs its
  commit added to `fetch_models.py`.
- `jarvis-sync-projects` (the project sync below).
- The OpenSSH client, for `full` mode only (below). Never started in `readonly` mode.
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
| `JARVIS_SYNC_REPOS` | Space-separated GitHub `owner/name` list, or `*` for every repository of `JARVIS_SYNC_OWNER` (below). |
| `JARVIS_SYNC_DIR` | Where the copies go, one directory per repo name (default `/work`). |
| `GIT_PAT` | Token for private repos; required with `*`, where it also lists the repositories. Read by a git credential helper from the environment, never written to `.git/config`. Set it on the init container only, never on the bridge container: Claude Code's Read tool could read the process environment. |
| `JARVIS_SYNC_OWNER` | With `*`: the GitHub account whose repositories are synced. |
| `JARVIS_SYNC_MAX_KB` | With `*`: skip repositories larger than this many KB, as the API's `size` counts them (default `512000`, 500 MB). |
| `JARVIS_SYNC_EXCLUDE` | With `*`: names (or `owner/name`) never to sync, space- or comma-separated. |

Copies are shallow (depth 1) and reset to the remote default branch at every start. A repo
that cannot be refreshed keeps its old copy (warning in the init container's log).

**An explicit list:** a repo with no copy at all fails the init container.
`JARVIS_PROJECTS` on the bridge container then points at them, e.g.
`demo=/work/demo,other=/work/other`.

**Every repository (`*`):** `jarvis-list-repos` asks the GitHub API for
`GET /user/repos?affiliation=owner` with the PAT, page by page, and keeps a repository only
when its owner's login is `JARVIS_SYNC_OWNER`: an organisation's repositories are never
cloned, whatever else the token can read. Archived repositories, forks, excluded names and
repositories over the size limit are skipped, each with a line in the log; the log ends with
the count and the total size. A first clone that fails is logged and skipped; the init
container fails only when no repository is available at all. When the list cannot be read
(GitHub down, a revoked token), the copies already on the volume are refreshed and kept.
When it can, a copy whose repository is no longer on it (archived, excluded, deleted) is
removed, so the bridge stops offering it. Narrowing the PAT (for example to public repos only) counts as "left the list": the private copies are removed on the next start. An empty list (everything excluded, or a token of another user) removes nothing. On the bridge container set `JARVIS_PROJECTS=*`
and `JARVIS_PROJECTS_ROOT=/work`: every copy is a project, and `GET /projects` lists them.
Egress stays DNS and 443: the API is `api.github.com` on 443.

Mount the copies read-only in the bridge container; Claude Code's tools are read-only anyway
(`Read`, `Glob`, `Grep`).

**Volume size.** Count about twice the size of each repository's checked-out files: the
shallow pack plus the working tree (measured 2026-11-15: a 28 MB tree took 53 MB). The API's
`size` is the whole history, compressed, and can be far off either way: one repository of
44 MB had a 237 MB tree, another of 274 MB a 28 MB tree. Leave room for growth and the
state directory. On `local-path` the requested size is not enforced and a PVC cannot be
resized; what counts is the free space on the node's disk. To get a larger volume elsewhere,
create a new PVC and let the init container clone into it (the copies are disposable; copy
`state/` across to keep the sessions).

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
      - { name: JARVIS_SYNC_REPOS, value: "you/demo you/other" }   # or "*" with JARVIS_SYNC_OWNER
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
      - { name: JARVIS_PROJECTS, value: "demo=/work/demo,other=/work/other" }   # or "*" with JARVIS_PROJECTS_ROOT=/work
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

## Full mode: the road to the agent host

Only for `JARVIS_AGENT_MODE=full` ([`bridge-service.md`](bridge-service.md), "Agent
modes"; change [`jarvis-full-agent`](../../openspec/changes/jarvis-full-agent/design.md),
D32 and D40). Without it nothing in this section applies and the pod stays as described
above. The pieces belong in your own cluster repository, like the rest.

**This changes D2 of `run-bridge-in-cluster`.** Egress was DNS and HTTPS only. In `full`
mode it is DNS, HTTPS and ssh to the one address of the agent host, and nothing else.

**The road is the private network the cluster's nodes and the agent host share, straight
to the agent host's sshd.** There is no Tailscale egress Service in it. Design D32 first described
one (an `ExternalName` Service that the Tailscale operator fills in); it was dropped when
this was wired (2026-11-16), because the operator rewrites that Service after it is applied,
which a GitOps tool then reports as drift unless it is told to ignore the difference. What
D32 asks of the connection holds without it: ssh encrypts and authenticates both ends (the
bridge's key, the pinned host key), the key's `authorized_keys` line forces the wrapper and
names the one source address, and the NetworkPolicy allows the one destination. What is
different: the packets cross the local network, not a WireGuard tunnel, and the source
address the agent host sees is a **node's**, not the bridge's own (below). D32 now describes
this road, and keeps the egress Service as the alternative for a cluster that shares no
network with the agent host.

1. **No Service.** `JARVIS_AGENT_HOST` is the agent host's address on that network, and
   `JARVIS_AGENT_PORT` its sshd port (22).

2. **One more egress rule** in the bridge's NetworkPolicy: TCP 22 to that single address,
   as a `/32`, never a range or a namespace.

   ```yaml
   egress:
     - to:
         - ipBlock: { cidr: "<agent host address>/32" }
       ports:
         - { protocol: TCP, port: 22 }
   ```

   **Which address the agent host sees.** A pod's traffic to an address outside the cluster
   leaves through its node and is rewritten to the node's address (SNAT, the CNI's
   masquerading). So `from=` on the key's `authorized_keys` line is the address of the
   **node the bridge runs on**. Two consequences. Keep the bridge on that node (a
   `nodeSelector`), or a rescheduled pod is refused and every turn is answered read-only
   with "the agent host could not be reached"; or list every node's address in `from=`.
   And `from=` then narrows the key to that node, not to the bridge: every pod and process
   on the node has the same source address. What keeps another pod out is that it does not
   have the key, and that its own NetworkPolicy, where there is one, does not allow the
   destination. Check the address once from a throwaway pod on that node, and read it back
   in the agent host's sshd log.

   The `known_hosts` file has one line: the address exactly as in `JARVIS_AGENT_HOST`, then
   the agent host's ed25519 public host key (`<address> ssh-ed25519 AAAA…`; as
   `[<address>]:<port>` when the port is not 22). Take the key on the agent host itself
   (`/etc/ssh/ssh_host_ed25519_key.pub`), not with `ssh-keyscan` from the cluster: that
   would be trust on first use by another name.

   In Mark's setup (verified 2026-11-16 with a throwaway pod: the ssh banner came back and
   the agent host logged the node's address as the source): the agent host is
   `192.168.178.58`, the bridge's node sends as `192.168.178.207`, so the rule is
   `192.168.178.58/32`, `JARVIS_AGENT_HOST=192.168.178.58` and the key's line starts with
   `restrict,from="192.168.178.207",command=…`. These values live in the cluster's own
   repository and on the agent host; nothing in the bridge's code knows them.

3. **The key and the pinned host key as files, from a Secret.** Never as environment
   variables, and never under `/work` or any other project directory: the read-only agent
   in the pod is confined to the project directory, so it cannot read them where they are.
   The bridge refuses to start when either file gives any permission to group or others,
   and the bridge runs as uid 10001, so the files must be `0400` **and owned by that
   user**. A Secret volume cannot do that by itself (its files are root's; with an
   `fsGroup` they become group-readable, which the bridge refuses). Copy them once per
   start, in an init container, into a memory-backed `emptyDir` that only the bridge
   container mounts:

   ```yaml
   initContainers:
     - name: agent-key
       image: ghcr.io/mwest2020/jarvis-bridge@sha256:<digest>
       command: ["sh", "-c", "install -m 0400 /secret/key /secret/known_hosts /agent/"]
       volumeMounts:
         - { name: agent-secret, mountPath: /secret, readOnly: true }
         - { name: agent-key, mountPath: /agent }
   containers:
     - name: bridge
       env:
         - { name: JARVIS_AGENT_MODE, value: full }
         - { name: JARVIS_AGENT_HOST, value: "<agent host address>" }
         - { name: JARVIS_AGENT_USER, value: "<the coding session's user>" }
         - { name: JARVIS_AGENT_KEY_FILE, value: /agent/key }
         - { name: JARVIS_AGENT_KNOWN_HOSTS_FILE, value: /agent/known_hosts }
       volumeMounts:
         - { name: agent-key, mountPath: /agent, readOnly: true }
   volumes:
     - name: agent-secret
       secret: { secretName: jarvis-agent-key, defaultMode: 0440 }
     - name: agent-key
       emptyDir: { medium: Memory, sizeLimit: 1Mi }
   securityContext: { runAsUser: 10001, runAsGroup: 10001, fsGroup: 10001 }
   ```

**Which projects.** The agent host's list decides (change `jarvis-general-conversation`,
D52): `GET /projects` asks the agent host, and a turn for a project that is only there runs
there. The pod's synced copies, still cloned at pod start, serve the read-only fallback
only: a project that is only in the pod is answered read-only, and when the agent host
cannot be reached the phone gets the pod's list. A repository created by voice reaches the
pod at its next restart; nothing in the pod's configuration changes for this.

**What a reader of that Secret can now reach.** Before this, the cluster had no road to
the agent host. With it, anyone who can read the Secret and send from the bridge's node,
or run code in the bridge's pod, can put any prompt to a full agent on the agent host, and can
answer its read-backs with a yes: that is the coding session's reach, with its GitHub
token, its ssh keys and its secrets client. The forced command limits the form (one
prompt, one answer), not the power. What keeps it narrow: `from=` on the key (the node's
address, see above), the Secret
mounted in this pod only, the NetworkPolicy, the kill switch on the agent host, and the
notification on every confirmed action. Give read access to that Secret, and `exec` into
that pod, to nobody who should not have a shell on the agent host. This trust edge is the
question put to the owner in [#25](https://github.com/MWest2020/jarvis/issues/25).

The image has the OpenSSH client for this. In `readonly` mode it is never started, and the
CI smoke test (offline, no `JARVIS_AGENT_MODE`) passes as before.

## Resources and probes

- Memory: measured in the pod, about 2.3 GiB at rest with both models loaded and a peak of
  2.78 GiB during a turn (Claude Code's own process comes on top while it runs). A request
  of 2.5 Gi with a limit of 3 Gi works but leaves little room; 3 Gi / 4 Gi is safer.
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

## Test on 2026-11-14

The first deployment, on a three-worker cluster with Cilium and the Tailscale operator,
image `jarvis-bridge:sha-a663a26`, the manifests as described above (one replica, memory
request 2.5 Gi, limit 3 Gi), six projects synced:

- Image pull about 2 min 50 s (1.34 GB compressed); project sync about a minute; model load
  about a minute. The pod became ready with no restarts, read-only root filesystem included.
- The start log shows the `JARVIS_BEHIND_TAILSCALE_PROXY=1` warning, then "listening".
- From a tailnet machine, no token and a wrong token: 401.
- Turn 1, the WAV "What is this project about in one sentence?": 200 `audio/wav` in 16.9 s
  end to end (curl's total, over the tailnet: upload, transcription, the Claude Code turn,
  synthesis). Spoken summary: "Jarvis lets you talk to Claude Code from your Android phone,
  hands-free, while it runs on your own machines over Tailscale and speaks back a short
  summary."
- Turn 2, the follow-up "And which speech models does it use?": 200 in 18.7 s, the same
  session id, and an answer about faster-whisper and Kokoro.
- The request source in the log is the operator's proxy pod. From a pod in another
  namespace, the pod IP and the Service both time out; from the LAN, the pod IP and port
  8787 on every node time out.
- `kubectl logs` holds request lines and summaries only; transcripts and full replies are
  in the `0600` log file. The project sync (GitHub) and Claude Code (the Anthropic API) work
  with egress limited to DNS and 443.
- Memory (cgroup): about 2.3 GiB anonymous at rest after two turns, peak 2.78 GiB, no OOM.
  That is close to a 3 Gi limit; give it 4 Gi if turns grow (larger repos, longer replies).

