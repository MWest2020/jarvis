# Change: add-voice-bridge

## Why

Claude Code is currently used from the phone via Termius over SSH on a Tailscale network. That needs a keyboard and a screen, so it is unusable while driving. Claude Code's built-in /voice dictation needs a local microphone and does not work over SSH. Remote Control in the Claude Android app supports keyboard dictation for input only, with text replies. No existing option gives a spoken back-and-forth with a Claude Code session on Android.

## What Changes

- Add an Android-side client that captures speech, sends it for transcription, and plays spoken replies.
- Add a small bridge service on the target machine, reachable only over Tailscale, that runs Claude Code headless and returns plain text.
- Add text-to-speech with a natural voice (not Piper).
- Keep the existing Termius/SSH workflow unchanged as a fallback.

## Impact

- New capability: `voice-bridge`.
- Affected systems: Android phone, home machine(s) running Claude Code, Tailscale network.
- No changes to Claude Code itself.
