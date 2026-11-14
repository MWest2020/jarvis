---
status: current
last_reviewed: 2026-11-14
---
# Jarvis docs

Start here. Agents: read this page and follow the links; there is no need to scan the repo.

| What | Where |
|---|---|
| What we are building and why | [`openspec/changes/add-voice-bridge/proposal.md`](../openspec/changes/add-voice-bridge/proposal.md) |
| Design decisions (D1 to D9) | [`openspec/changes/add-voice-bridge/design.md`](../openspec/changes/add-voice-bridge/design.md) |
| Tasks, with the weekend slice on top | [`openspec/changes/add-voice-bridge/tasks.md`](../openspec/changes/add-voice-bridge/tasks.md) |
| Requirements and scenarios | [`openspec/changes/add-voice-bridge/specs/voice-bridge/spec.md`](../openspec/changes/add-voice-bridge/specs/voice-bridge/spec.md) |
| Roadmap (feeds westerweel.work/nu) | [`reference/roadmap.yml`](reference/roadmap.yml) |
| Working rules for agents | [`../AGENTS.md`](../AGENTS.md) |
| How-to guides (running the bridge, phone setup) | [`how-to/`](how-to/) |

## Rule

Every change checks whether these docs need updating, in the same commit. No push before that
check. CI's `docs-gates` job enforces it for code paths.
