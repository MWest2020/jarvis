# Tasks

## 1. Decide
- [x] **Mark.** Build it. Mark, 2026-11-26: "jarvis did a beatbox, but when prompted for
      another it stayed silent" and "he says check the log for details, but cant show
      them".

## 2. Beatbox
- [x] `BeatMemory` (per project, ten minutes, `FOLLOW_UP_S`); `choose(follow_up=...)`; the
      server records each beat played.
- [x] The beats' names ask ("hip hop", "boots and cats"), not "hook" or "cats" alone.
- [x] A dropped tag says the bridge's own line; a beat that fails to render too.
- [x] The log line says `follow_up=`.

## 3. Spoken output
- [x] `;` out of the code filter; a long summary cut at a semicolon before a word cut.
- [x] `speakable()`; extract_summary: the summary as is, else made speakable, else the
      reply made speakable, else the honest fallback.
- [x] `spoken_line()` in the agent host's wrapper; the bridge speaks only a summary line.

## 4. Speech recognition
- [x] `JARVIS_WHISPER_LANGUAGES` (at least two codes, not with `JARVIS_WHISPER_LANGUAGE`).
- [ ] Set `JARVIS_WHISPER_LANGUAGES=nl,en` in the homelab deployment.

## 5. Ship
- [ ] Bridge deployed (homelab pin), agent host reinstalled.
- [ ] Archive this change.
