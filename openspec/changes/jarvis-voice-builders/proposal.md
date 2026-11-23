# Change: jarvis-voice-builders

## Why

Mark, by voice to Jarvis on 2026-11-23, after Jarvis refused to start a helper that would
fix a bug: *"Now why was it refused? You need to be able to start your own sessions."*
Jarvis answered that by design a voice turn may only start read-only helpers (D58). His
standing rule is that a voice request means "build it", and the refusal turned a bug fix
he asked for into a trip to the desk.

D58 kept builders out for two reasons: the type rule was a seatbelt on top of the gate
(D40), and a build asked for by voice was meant to go to the coding session. Neither holds
up as a reason to refuse a builder that runs in the turn. Host check 13 showed that Claude
Code runs the turn's PreToolUse hooks for every call a subagent makes, from a
`general-purpose` subagent too, and a security review on 2026-11-23 confirmed live that
hooks fire in subagents. The gate keys its state on the turn, found through the one
`claude` process, so a builder's merge, push to a default branch, mail or delete meets the
same gate, the same read-back and the same one-pending-call rule as the turn's own. What a
builder adds is edits, commits, branch pushes and pull requests on the user's own
repositories, which the turn could already do itself.

Mark asked for this directly, so it is built in the same PR as this proposal. The PR is
the approval point.

## What Changes

- **A voice turn may start a `general-purpose` subagent** (D59), without a spoken yes to
  start it: a new list `builder_types = ["general-purpose"]` in `[subagents]` of
  `agent_host/gate_rules.toml`, next to `read_only_types`. Everything else of D58 stays:
  the type is named explicitly, it runs in the turn (`run_in_background` absent or false),
  with no `isolation` (above all `remote`, where the gate does not run), `name`,
  `team_name` or `mode`, and not when an agent file defines that name or cannot be read
  with certainty.
- **`claude` and `fork` stay refused**, on the merits: `claude` gives a builder nothing
  `general-purpose` does not (the same tools under a second name, the default of a fleet
  view, not what host check 13 ran); `fork` is always asynchronous in Claude Code 2.1.322
  (`forceAsync`) and carries the turn's whole conversation. No type at all stays refused:
  which agent runs is then the configuration's, not the call's.
- **Every gated call inside a builder gates as in the turn.** Nothing in the gate changes
  for that; it is what host check 13 shows, now covered by tests that send the hook input
  of a call made inside a subagent through the real gate and wrapper: a nameable call is
  the turn's pending call and is read back to Mark from config-derived text when the turn
  ends, its yes releases exactly that call in the builder's directory, once; a call that
  needs the desk is refused with `needs_desk`, so the bridge says it was passed to the
  coding session. The denial texts gain one sentence telling a subagent to hand back at
  once.
- **No yes to start a builder.** Mark's words ask for Jarvis to just do it, and the gate
  guards every risky call inside it. So there is no read-back for the `Agent` call, and no
  question of the agent's words in one.
- **Work that will not fit in a turn still goes to the desk**, now by starting the helper
  with `run_in_background: true`: a helper that would outlive the turn is refused with
  `needs_desk`, as before.
- **The prompt** (`agent_host/prompt.md`) says Jarvis may start its own builder, that the
  gate guards the builder's calls as its own, how to hand on long work, and to end the turn
  when a builder reports a refusal or a call waiting for a yes.

## Impact

- Added requirement in `voice-bridge`: Subagents in a voice turn (it states D58 and D59 as
  a requirement; until now D58 lived in the design of `jarvis-full-agent` only).
- Affected code: `agent_host/gate_rules.toml`, `agent_host/rules.py`,
  `agent_host/classify.py`, `agent_host/decision.py`, `agent_host/gate` (the denial texts),
  `agent_host/prompt.md`; tests in `tests/test_agent_host_gate_builders.py`,
  `tests/test_agent_host_gate_subagents.py`, `tests/test_agent_host_gate_entrypoint.py`.
- Design: D59 in `openspec/changes/jarvis-full-agent/design.md`. Archive after
  `jarvis-full-agent`.
- Limits, said plainly: a builder runs inside the turn and ends with it (the agent host's
  `timeout_s`, 600 s by default). The bridge stops waiting after 170 s; a call a builder
  makes pending after that is read back into a reply nobody hears. It does not run, and the
  next request clears it; the prompt tells Jarvis to ask for it again. Making that read-back
  reach Mark late is drive mode 2 ("an unheard read-back cancelled", arrivals).
- How it works: [`docs/how-to/agent-host.md`](../../../docs/how-to/agent-host.md#subagents-a-voice-turn-may-start).
