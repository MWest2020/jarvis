# Tasks: add-voice-bridge

## Weekend slice (build this first)

Mark's weekend prompt (2026-11-14): "Scope is a minor test only, because usage limits are tight
this weekend. Build the smallest end-to-end slice that proves the idea works; leave the rest of
tasks.md for later." The slice below is the agent's reading of that (not Mark's own list):

- 1.1 endpoint on the private interface with the token check;
- 1.2 `claude -p` with JSON output and session resume, for one project;
- 1.4 the spoken summary extracted from the reply, the full reply logged;
- 2.1 and 2.2 local STT and TTS, CPU-friendly models are fine for the test;
- 3.1 tap-to-talk via HTTP Shortcuts (upload audio, play the returned audio): document the
  shortcut setup in `docs/how-to/`;
- 4.1 an end-to-end test on the tailnet.

1.3 is limited in the slice to a fixed read-only allow-list (no approval flow yet). Everything
else (1.3 approvals, 2.3, 3.2, 3.3, 4.2, the plugin packaging and the adapter) comes later.

## 1. Bridge service
- [ ] 1.1 HTTP endpoint bound to the Tailscale interface only, with token check
- [ ] 1.2 Call `claude -p` with JSON output; store and resume session ID per project
- [ ] 1.3 Tool allow-list and approval handling (D6)
- [ ] 1.4 Extract spoken summary from reply (D5); log full reply

## 2. Speech
- [ ] 2.1 Run local Whisper; measure accuracy on recordings made in the car
- [ ] 2.2 Run Kokoro; pick a voice
- [ ] 2.3 Single interface for STT and TTS so hosted options can be swapped in

## 3. Android client
- [ ] 3.1 Tap-to-talk capture, upload audio, play returned audio
- [ ] 3.2 Acknowledgement tone while waiting
- [ ] 3.3 Project selection by voice

## 4. Validation
- [ ] 4.1 End-to-end test on the tailnet
- [ ] 4.2 Test while parked in the car before using it while driving
