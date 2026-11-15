# Change: jarvis-full-agent

## Why

Mark, 2026-11-15, verbatim:

- "jarvis moet zelf ook repos kunnen aanmaken etc. dus alles wat ik bijvoorbeeld hier met jou kan"
- "jarvis may merge etc. everything you can do"

"You" is the coding session: Claude Code on the host where Mark works with it, with his
instructions file, the memory, the hooks, the auto-mode permission classifier and the tools
that are installed there (`gh`, `ssh`, the secrets and cluster clients). Jarvis today is the
opposite: `claude -p --restricted` in a pod with `Read`, `Glob` and `Grep`, on shallow copies
of the repositories. It can answer, and it can do nothing.

So Jarvis stops being read-only. It gets the same powers **and the same rules**: a second
mouth for the same agent, never a privileged way around it.

## What Changes

- **Two agent modes.** `readonly` is what exists and stays the default and the fallback.
  `full` runs the turn on the *agent host*: the machine where the coding session lives.
- **A remote agent adapter** on the D8 seam. The bridge (in the cluster) opens one `ssh`
  connection per turn over the tailnet, with a dedicated key whose `authorized_keys` line
  forces one fixed wrapper. The wrapper runs `claude -p --output-format json`, resumed per
  project, in the project's checkout on the agent host, in the `auto` permission mode. No
  `--dangerously-skip-permissions`, no bypass mode, no tool list of its own. Speech stays in
  the pod: only the `claude` process runs on the agent host.
- **The same rules.** The turn loads the user's own settings, instructions, memory and hooks
  exactly as the coding session does. An appended prompt adds only what is different: you are
  Jarvis, the driver cannot see a screen, the same agreements apply.
- **Voice confirmation** for actions that are irreversible or reach outside: merge, deploy,
  delete, anything forced, sending mail or messages, creating a repository, and changing
  Jarvis's own rules. A PreToolUse hook refuses such a tool call unless the bridge sent a
  grant for exactly that call in this turn. Jarvis reads the action back in one sentence; a
  spoken "yes" as the next utterance releases it, once. It is on by default and Mark can
  switch it off: everything is allowed, this only guards against misheard speech in a car.
- **Identity and audit.** Commits use the host's global git identity. Builds and pings go out
  under the identity the host configures for Jarvis. Every turn leaves an owner-only record on
  the agent host (what was said, what was replied, every tool call that changes something,
  every confirmation), and one line per day goes into the configured daily notes.
- **`AGENTS.md`**: the hard rule "headless agent runs use an explicit tool allow-list" is
  rewritten when this change is applied: the allow-list holds in `readonly` mode; in `full`
  mode the user's own permission mode and hooks decide, and a bypass flag is never passed.

Not in this change: streaming, tones, control words, the drive journal (`add-drive-mode`,
#9); the Android app (no change needed: a read-back is an ordinary spoken reply, and the yes
is an ordinary next turn); any change to the phone's token or to how the bridge is exposed.

## What Jarvis must still never do

The same hard rules as the coding session. They are not new and not relaxed by "everything":

- touch the employer's organisations or repositories (the global hook blocks it; Jarvis does
  not look for a way past the hook);
- change anything on production machines that are outside the experiment (read at most);
- print or speak a secret, a token or a key;
- route around a refusal of the permission classifier or of a hook: a refusal is a refusal,
  and Jarvis says so;
- work under another identity than the configured ones, or borrow another session's identity;
- run with a permission bypass.

Mark's gates stay his. Where an agreement says a step needs his OK (a production deploy of
wordsworth, something new and public), his spoken and confirmed yes in the car is that OK,
exactly as when he types it to the coding session, and nothing more. Where an agreement
says the step is his to take (the handbook's agent definitions), Jarvis prepares it and
leaves it. A change of promise with open questions still goes to Mark as a question.
Merging still means: an independent review and every check green first.

## Relation to add-drive-mode (#9)

This change answers #9's open question 1 ("Should a spoken yes ever unlock file edits or
commands inside the voice session?") with **yes**, and goes further: edits and commits need
no yes at all; only the gated classes do. It supersedes #9's read-only handoff design. #9's
branch is not edited here. What #9 should change before it is merged:

| In #9 | Do |
|---|---|
| Rule 1 and D11 ("the front seat directs; it does not edit"), the non-goal "editing files or running commands from the voice session" | Drop. In `full` mode the voice session is a full agent. |
| The `MODIFIED` delta of "Approval for changes" | Drop it; the one in this change replaces it. Two changes cannot both rewrite one requirement. |
| D21, the capability `handoffs`, phase 4, `POST /arrivals` and its token, tasks 4.1 to 4.9 | Drop. Jarvis starts a build itself, as its own identity, under the confirmation gate. Keep "note" (it writes only to the journal). |
| D12 ("actions come from the driver's words only") | Narrow it: project switching, control words and the yes are still recognised by the bridge from the driver's own utterance, and the agent can never confirm. The agent may now act, under the classifier, the hooks and the gate. |
| D18, the row "yes / no" | Point to this change's requirement "Voice confirmation". Use its closed list and its 60 seconds. |
| D20 (background turns) and D16 (streamed turn) | Keep. Build them on the adapter interface of this change; for `full` mode the stream comes through the same ssh pipe. A background turn that reaches a gated action stores the read-back as an arrival. |
| D22 / task 1.2 (agent adapter) | Whichever change is built first adds the interface; the other extends it. |
| Open question 6 (the factory side of the `build` handoff) | Settled outside this repository: the build system already accepts the identity `jarvis`. |
| Risk "a wrong yes", task 4.5, roadmap phase "Handoffs" | Rewrite against the gate; the "never runs" test applies to `readonly` mode only; the phase becomes "Full agent". |

## Phases

One factory build per phase. Tasks marked **(host)** need the agent host itself and are done
by the coding session; tasks marked **(Mark)** need Mark's hands, phone or ears.

| # | Phase | You can then |
|---|---|---|
| 1 | Agent host side | run a full turn on the agent host by hand through the wrapper, gate and record included |
| 2 | Bridge side | choose `full` mode; a read-back and a yes work end to end against a fake agent host |
| 3 | Wiring | talk to the full agent from the phone over the tailnet |
| 4 | Daily line and parked test | see what Jarvis did in the daily notes; merge by voice, parked |

## Impact

- Capability `voice-bridge`: one requirement modified (Approval for changes), eight added.
- Code: `bridge/` (adapter seam, remote adapter, confirmation state), new `agent_host/`
  (wrapper, gate hook, gate rules, prompt, installer; Python standard library only),
  `deploy/Dockerfile` (the ssh client). `agent_host/` is added to the `docs-gates` code paths.
- Docs: a new how-to for the agent host; `bridge-service.md` and `run-in-cluster.md` updated.
- Outside this repository: one `authorized_keys` line and a root-owned install on the agent
  host; a Secret with the bridge's key, a tailnet egress Service and one more egress rule in
  the cluster's own repository.
- The Claude credentials in the pod stay, for `readonly` mode. `full` mode uses the agent
  host's own login, so its turns count against the same usage limits as the coding session.

## Open questions

None. Mark decided. What needs his hands is listed at the end of `tasks.md`.
