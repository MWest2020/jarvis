# Design: add-voice-bridge

## Context

- Phone: Android, already on the tailnet.
- Machines: reachable over Tailscale; Claude Code already installed and authenticated.
- Constraint: must work hands-free in the car; short, spoken replies.
- Preference: boring, auditable components; avoid Piper's voice.

## Goals / Non-Goals

- Goals: push-to-talk or tap-to-talk voice input; spoken summaries of Claude Code replies; one ongoing session per project that survives across turns; packaged as a Claude Code plugin so others can use it, with nothing tied to Mark's own setup.
- Non-Goals: reading code or diffs aloud verbatim; exposing anything outside the tailnet; replacing the SSH workflow.

## Decisions

**D1. Headless Claude Code instead of piping into an interactive SSH session.** The bridge calls Claude Code in print mode (`claude -p`) with JSON output, and resumes the same session on each turn using the session ID from the previous reply. This gives clean, parseable text instead of terminal escape codes. Alternative considered: driving an interactive session in tmux over SSH. Rejected: fragile parsing of terminal output.

**D2. Transport: HTTPS over Tailscale.** The bridge listens only on the Tailscale interface. The phone calls it directly. Tailscale handles identity and encryption; the bridge additionally checks a shared token. Alternative considered: SSH command per turn. Viable fallback, but more moving parts on Android.

**D3. Speech-to-text.** Option A: Whisper large-v3 (or faster-whisper) on the home machine. Private, no per-use cost, needs a decent GPU or Apple Silicon. Option B: hosted transcription (e.g. OpenAI). Higher accuracy on noisy car audio, costs per minute, audio leaves the network. Start with A; switch to B only if accuracy in the car is not good enough.

**D4. Text-to-speech.** Option A: Kokoro, open source, runs locally, far more natural than Piper. Option B: hosted TTS (ElevenLabs or OpenAI) for the most natural voice. Start with A; keep the TTS behind one interface so B can be swapped in.

**D5. Spoken output is a summary, not the raw reply.** Claude Code replies often contain code and paths. The bridge instructs Claude Code (via an appended system prompt) to end each reply with a short spoken summary, and only that summary is sent to TTS. Full text is logged on the machine.

**D6. Permissions.** Headless runs use an explicit allow-list of tools. No unattended permission bypass. Actions that need approval are announced by voice and require a spoken yes, or are deferred until Mark is at a screen.

**D7. Distribute as a Claude Code plugin.** The machine side (bridge service, start command, session handling) ships as a Claude Code plugin. Speech-to-text and text-to-speech providers are settings: local (Whisper, Kokoro) or hosted. Network access is the user's choice; Tailscale is recommended, not required. The phone client cannot be installed by a plugin, so it is a separate small Android app or web page. Mark's own setup (habitat, factory, machine names) stays out of the plugin.

**D8. Agent behind an adapter interface.** The bridge talks to the coding agent through one small adapter interface: send prompt, get text reply, resume session. Only the Claude Code adapter is built now. Later adapters: Codex (headless exec with session resume), and a local model via an open-source coding agent that supports Ollama. Speech and phone parts stay unchanged. Not in scope for this weekend.

**D9. Sovereignty by default.** No dependency on third-party messaging apps or agent platforms (e.g. Telegram, Hermes). Phone talks directly to the user's own machine. Local STT/TTS is the default; cloud speech services are optional settings only.

## Risks / Trade-offs

- Driver distraction: keep turns short, no screen interaction needed beyond one tap.
- Transcription errors on technical terms: echo the transcript back briefly before executing anything that changes files.
- Latency: STT + Claude Code + TTS may take several seconds; play a short acknowledgement tone.

## Open Questions

- Android client: decided. HTTP Shortcuts (already on Mark's phone) for the weekend test; a native Android app as the real client, built after the test so its lessons go into the app.
- Which machine hosts STT/TTS if the Claude Code machine has no GPU?
- Cloud STT/TTS acceptable, or local only?
