---
status: current
last_reviewed: 2026-11-15
---
# Jarvis docs

Start here. Agents: read this page and follow the links; there is no need to scan the repo.

| What | Where |
|---|---|
| What we are building and why | [`openspec/changes/archive/2026-11-14-add-voice-bridge/proposal.md`](../openspec/changes/archive/2026-11-14-add-voice-bridge/proposal.md) |
| Design decisions (D1 to D9) | [`openspec/changes/archive/2026-11-14-add-voice-bridge/design.md`](../openspec/changes/archive/2026-11-14-add-voice-bridge/design.md) |
| Tasks, with the weekend slice on top | [`openspec/changes/archive/2026-11-14-add-voice-bridge/tasks.md`](../openspec/changes/archive/2026-11-14-add-voice-bridge/tasks.md) |
| Requirements as they stand (the living spec) | [`openspec/specs/voice-bridge/spec.md`](../openspec/specs/voice-bridge/spec.md) |
| Roadmap (feeds westerweel.work/nu) | [`reference/roadmap.yml`](reference/roadmap.yml) |
| Working rules for agents | [`../AGENTS.md`](../AGENTS.md) |
| How-to guides (running the bridge, phone setup) | [`how-to/`](how-to/) |
| Running the bridge in a Kubernetes cluster (image, tailnet-only Service, NetworkPolicy) | [`how-to/run-in-cluster.md`](how-to/run-in-cluster.md) |
| Change in progress: run the bridge in a cluster | [`openspec/changes/run-bridge-in-cluster/`](../openspec/changes/run-bridge-in-cluster/) |
| Change in progress: every project (`GET /projects`, `JARVIS_PROJECTS=*`, sync of all of one owner's repositories) | [`openspec/changes/add-project-list/`](../openspec/changes/add-project-list/) |
| The Android app: install, first run, errors, how it differs from HTTP Shortcuts | [`how-to/android-app.md`](how-to/android-app.md) |
| Change in progress: the first Android app (code in [`android/`](../android/)) | [`openspec/changes/add-android-client/`](../openspec/changes/add-android-client/) |
| Proposed (Mark decided 2026-11-15): Jarvis as a full agent. The turn runs on the coding session's host with the same rules, a spoken yes for irreversible actions, an audit record. Supersedes the read-only handoffs of `add-drive-mode` (#9) | [`openspec/changes/jarvis-full-agent/proposal.md`](../openspec/changes/jarvis-full-agent/proposal.md), with [`design.md`](../openspec/changes/jarvis-full-agent/design.md) (D30 to D40) and [`tasks.md`](../openspec/changes/jarvis-full-agent/tasks.md) (four phases) |
| Proposed (waits on Mark, #30): notifications on the phone. `POST /notify`, a self-hosted ntfy broker on the tailnet, the app as listener, fixed words on the lock screen | [`openspec/changes/jarvis-notifications/proposal.md`](../openspec/changes/jarvis-notifications/proposal.md), with [`design.md`](../openspec/changes/jarvis-notifications/design.md) (N1 to N11) |
| Proposed (waits on Mark, #30): confirming an irreversible action with the fingerprint. The phone signs the pending action, the agent host verifies | [`openspec/changes/jarvis-biometric-confirm/proposal.md`](../openspec/changes/jarvis-biometric-confirm/proposal.md), with [`design.md`](../openspec/changes/jarvis-biometric-confirm/design.md) (B1 to B10) |
| Proposed, a later phase (waits on Mark, #30): a speaker check, so Jarvis reacts to Mark's voice only. Starts with a measurement; it is no authentication | [`openspec/changes/jarvis-speaker-verification/proposal.md`](../openspec/changes/jarvis-speaker-verification/proposal.md), with [`design.md`](../openspec/changes/jarvis-speaker-verification/design.md) (V1 to V11) |

## Rule

Every change checks whether these docs need updating, in the same commit. No push before that
check. CI's `docs-gates` job enforces it for code paths.
