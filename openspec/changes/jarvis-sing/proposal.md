# Change: jarvis-sing

## Why

Mark, 2026-11-17: "easter egg wish... make jarvis able to sing." He asked for it directly,
so it is built in the same PR as this proposal; the PR is the approval point.

## What Changes

- **The agent picks a tune, the bridge sings it.** When the driver explicitly asks for a
  song, the agent starts its spoken line with a tag, `[sing: <tune>]`, and its own short
  lyrics, one syllable per note. The tunes are a small table on the bridge
  (`bridge/singing.py`): `happy-birthday`, `twinkle`, `frere-jacques`, public-domain
  melodies only. The words are always the agent's own.
- **Rendered with what is there.** Kokoro renders each word once; numpy moves every
  syllable onto its note (TD-PSOLA: the note's pitch held steady with a light vibrato, the
  vowel stretched to the note's length), in a key that follows the voice, with a soft
  organ chord under it. At most 20 s. No new dependency and no other model.
- **Off the normal path.** A reply is sung only when the tag is the first thing on the
  agent's line and names a known tune, the driver's own words in that turn ask for a song,
  the reply is not a read-back, and `JARVIS_SING` is not `0`. Otherwise every tag is
  removed and the lyrics are spoken as words. A read-back is never changed.
- **After a notice.** In `full` mode the bridge's fixed sentences (a notice of the agent
  host, the read-only fallback, news of a new project) can stand around the agent's line;
  the tag counts as first when only such sentences come before it, and they are spoken
  before (or, for news, after) the song. Found live after #78: a song after "I started a
  new session for this project." was spoken instead of sung.
- A sung line may be 240 characters instead of 160.

## Impact

- Added requirement in `voice-bridge`: Singing on request.
- Affected code: `bridge/singing.py` (new), `bridge/tts.py`, `bridge/server.py`,
  `bridge/summary.py`, `bridge/config.py`, `agent_host/prompt.md`.
- The wire contract does not change: the tag travels inside the existing spoken line. An
  agent host with the old prompt never writes it; a bridge without this change would speak
  the tag, so the bridge goes first.
- How it works and its limits: [`docs/how-to/bridge-service.md`](../../../docs/how-to/bridge-service.md#singing-easter-egg).
