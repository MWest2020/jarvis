# Change: jarvis-beatbox

## Why

Mark, 2026-11-18, by mail after hearing the songs of `jarvis-sing`: "super! Can it
beatbox?" He asked for it directly, so it is built in the same PR as this proposal; the PR
is the approval point, as for `jarvis-sing`.

## What Changes

- **The agent asks for a beat, the bridge makes it.** When the driver explicitly asks for
  a beat ("beatbox", "drop a beat", "doe eens een beat"), the agent starts its spoken line
  with `[beatbox]` or `[beatbox: <name>]` and one short line of its own, said before the
  beat. The beats are a small table on the bridge (`bridge/beatbox.py`): `boots-and-cats`,
  `hip-hop` and `jarvis-hook` (with a hummed bass line and a sung hook, "Jar-vis... yeah").
  No name, or a name not in the table, plays one at random.
- **Made with what is there.** Every sound is the attack of a word in Jarvis's own Kokoro
  voice (the "b" of "boots", the "k" of "cats", the "t" of "tss"), cut short and filtered,
  over a synthesised body (a falling sine, filtered noise); the hum and the hook use the
  singing's TD-PSOLA. numpy only: no samples, no recordings, no new dependency. 5 to 15 s.
- **Off the normal path**, as singing: only when the tag is first on the agent's line
  (after nothing but the bridge's fixed sentences), the driver's own words in that turn
  ask for a beat, the reply is not a read-back, and `JARVIS_BEATBOX` is not `0`.
  Otherwise every tag is removed and the line is spoken. A read-back is never changed.

## Impact

- Added requirement in `voice-bridge`: Beatboxing on request.
- Affected code: `bridge/beatbox.py` (new), `bridge/tts.py`, `bridge/server.py`,
  `bridge/summary.py`, `bridge/config.py`, `agent_host/prompt.md`.
- The wire contract does not change: the tag travels inside the existing spoken line. An
  agent host with the old prompt never writes it; a bridge without this change would speak
  the tag, so the bridge goes first, then the prompt.
- How it works and its limits: [`docs/how-to/bridge-service.md`](../../../docs/how-to/bridge-service.md#beatboxing-easter-egg).
