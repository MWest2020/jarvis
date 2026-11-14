# Tasks: run-bridge-in-cluster

## 1. Bridge
- [x] 1.1 `JARVIS_BEHIND_TAILSCALE_PROXY=1` opt-in in `bridge/config.py`: private addresses
  only, never a wildcard or a public address, warning logged at start (D1)
- [x] 1.2 Tests for the opt-in (accepted, refused, exact value, the warning)

## 2. Image
- [x] 2.1 CPU-only torch on Linux and the spaCy model locked (`pyproject.toml`, `uv.lock`) (D4)
- [x] 2.2 `deploy/Dockerfile`: uv, Python 3.12, Node, pinned Claude Code, models at build
  time, non-root (D3)
- [x] 2.3 `.github/workflows/image.yml`: tests, build on PR, push to GHCR on main (D7)
- [ ] 2.4 First image pushed from main and its digest pinned in the deployment

## 3. Projects
- [x] 3.1 `deploy/sync-projects.sh`: shallow clone or refresh, token via credential helper (D5)

## 4. Deployment
- [x] 4.1 `docs/how-to/run-in-cluster.md`: image, Service, NetworkPolicy, secrets, volume
- [ ] 4.2 Deployed on a cluster behind the Tailscale operator (manifests in the operator's
  GitOps repository)
- [ ] 4.3 Verified: no token gives 401 over the tailnet; a real voice turn answers; the pod
  is not reachable from a namespace other than the operator's
