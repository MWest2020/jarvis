# Change: jarvis-session-lifetime

## Why

Measured in #60 and #61: the live `jarvis-home` session had grown to about 90,000 tokens.
Every voice turn resumed it, which costs about 3 s more than a fresh session for the same
question, and after an hour without a turn the prompt cache has expired, so the first turn
sends the whole conversation to the cache again (59,000 and 83,000 cache tokens written on
two such turns of 2026-11-17).

Mark decided on 2026-11-17 ([#61](https://github.com/MWest2020/jarvis/issues/61)): "yes,
fresh session after an hour idle. other improvement is jarvis asking to close the convo".
He asked for it directly, so it is built in the same PR as this proposal; the PR is the
approval point.

## What Changes

- **A new session after an hour idle.** The agent host keeps, per project, when its last
  turn ended. When that is more than `session_idle_minutes` ago (a new key, default 60,
  the prompt cache's lifetime; 0 switches it off), the next turn starts a new Claude Code
  session instead of resuming. The driver hears the existing notice `new_session` ("I
  started a new session for this project."), in its existing place in the notice order.
- **Jarvis offers to close a finished conversation, and closes it on request.** The turn
  prompt lets the agent offer once, at the end of a request that is clearly finished, to
  close the conversation. When the driver says to close it, the agent says goodbye and ends
  its reply with the line `SESSION: close`. The wrapper cuts that line off before the
  spoken line is taken and forgets the session id of the turn's own project; the next turn
  starts fresh, without a notice. The marker has no argument and acts on the request's
  project only; it is ignored while a call waits for a yes, and on a failed turn.
- **The record says why.** Each turn record that ran `claude` gets `session`: how the
  session started (`resumed`, `new`, `closed`, `idle`, `discarded`, `rotated`), the minutes
  since the project's last turn, and `end` (`closed`, `close_ignored`).

## Impact

- Modified capability: `voice-bridge`, the requirement Session continuity (as modified by
  `jarvis-full-agent`).
- Affected code: `agent_host/lifetime.py` (new), `agent_host/wrapper.py`,
  `agent_host/sessions.py`, `agent_host/config.py`, `agent_host/record.py`,
  `agent_host/prompt.md`, `agent_host/config.example.toml`.
- The wire contract between the bridge and the agent host does not change: no new field,
  no new notice. Either side can be updated first. The app learns of a close only from the
  goodbye it speaks; telling the app that the conversation ended (a reply field) is left
  for the app work, bridge first.
- What continuity promises changes: a question that refers to something said more than an
  hour ago, or before a close, starts from nothing in the conversation; memory and the
  coding session's notes still apply.
