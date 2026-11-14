# Tasks: add-voice-bridge

> Sections 1.1–1.4 are Mark's text. Everything marked **(draft)** was added by Claude on
> 2026-11-14 because the pasted text stopped at 1.4; Mark checks and corrects it.

## 1. Bridge service
- [ ] 1.1 HTTP endpoint bound to the Tailscale interface only, with token check
- [ ] 1.2 Call `claude -p` with JSON output; store and resume session ID per project
- [ ] 1.3 Tool allow-list and approval handling (D6)
- [ ] 1.4 Extract spoken summary from reply (D5)
- [ ] 1.5 **(draft)** Log the full reply text and the transcript per turn on the machine (no audio kept)
- [ ] 1.6 **(draft)** Agent adapter interface (send prompt, get text reply, resume session) with the Claude Code adapter only (D8)

## 2. Speech
- [ ] 2.1 **(draft)** STT behind one interface; local faster-whisper first (D3)
- [ ] 2.2 **(draft)** TTS behind one interface; local Kokoro first (D4)
- [ ] 2.3 **(draft)** Echo the transcript back briefly before any turn that may change files (risk: transcription errors)
- [ ] 2.4 **(draft)** Short acknowledgement tone while a turn runs (risk: latency)

## 3. Phone client
- [ ] 3.1 **(draft)** HTTP Shortcuts setup for the weekend test: record, POST audio, play the returned audio
- [ ] 3.2 **(draft)** Notes from the weekend test feed the native Android app (separate change, after the test)

## 4. Packaging
- [ ] 4.1 **(draft)** Claude Code plugin layout for the machine side, with settings for STT/TTS provider and the network binding (D7)
- [ ] 4.2 **(draft)** Nothing of Mark's own setup in the plugin (habitat, factory, machine names)
- [ ] 4.3 **(draft)** README: install, Tailscale recommendation, the token, the allow-list

## 5. Verify
- [ ] 5.1 **(draft)** A spoken round trip from the phone over the tailnet: question → spoken summary, session resumed on the next turn
- [ ] 5.2 **(draft)** A request from outside the tailnet or without the token is refused
- [ ] 5.3 **(draft)** An action outside the allow-list is announced and waits for a spoken yes, or is deferred
