---
status: current
last_reviewed: 2026-11-14
---
# Running the bridge service (weekend slice)

This covers the machine side only: the HTTP endpoint, local speech, and headless
Claude Code. For the phone side, see
[`android-http-shortcuts.md`](android-http-shortcuts.md).

## Prerequisites

- Tailscale running on this machine, and its IP known (`tailscale ip -4`).
- Claude Code installed and already authenticated (`claude --version` works).
- `espeak-ng` installed (a system package; Kokoro's text-to-speech front end
  depends on it) — e.g. `apt install espeak-ng` on Debian/Ubuntu.
- `uv` installed. Never `pip` directly in this repo (AGENTS.md).

## Install

```sh
uv sync
```

This installs `faster-whisper`, `kokoro` and `soundfile` into a local `.venv`
(gitignored). The first run of the service downloads the Whisper and Kokoro
model weights to the usual Hugging Face / Kokoro cache directories; that
needs a working internet connection once.

## Configure

The bridge reads everything from the environment. Nothing here goes in git —
put it in a local, gitignored `.env` and `export`/`source` it, or set it in
your process manager.

| Variable | Required | Meaning |
|---|---|---|
| `JARVIS_BIND_HOST` | yes | The Tailscale IP to listen on, e.g. `100.x.y.z`. Refuses to start on `0.0.0.0` or `::` — never public (AGENTS.md). |
| `JARVIS_TOKEN` | yes | The shared secret the phone sends as `Authorization: Bearer <token>`. Generate one with `openssl rand -hex 32`. Never commit it. |
| `JARVIS_PROJECTS` | yes | Comma-separated `name=/absolute/path` pairs, e.g. `demo=/home/mark/code/demo`. The phone selects a project by name in the request. |
| `JARVIS_PORT` | no (default `8787`) | TCP port on the Tailscale interface. |
| `JARVIS_STATE_DIR` | no (default `~/.jarvis`) | Where the per-project session id and the log file are stored. Outside the repo, never in git. |
| `JARVIS_WHISPER_MODEL` | no (default `base`) | faster-whisper model size; CPU-friendly sizes are `tiny`, `base`, `small`. |
| `JARVIS_KOKORO_VOICE` | no (default `af_heart`) | A Kokoro voice name. Pick one by ear once the service runs (see below). |

The tool allow-list for headless Claude Code is **fixed** for this weekend
slice: `Read`, `Glob`, `Grep` only (read-only). There is no approval flow yet
for anything beyond that list (D6) — that is tracked as a later task.

## Run

```sh
uv run jarvis-bridge
```

The service logs to `$JARVIS_STATE_DIR/bridge.log` (full Claude Code replies
and transcripts are logged there, even though only a short summary is ever
spoken — D5) and also to stdout.

## Picking a Kokoro voice

Kokoro ships several voices. Try a few by setting `JARVIS_KOKORO_VOICE` and
listening to a response, or render one directly:

```sh
uv run python -c "
from bridge.tts import synthesize
open('/tmp/voice-test.wav', 'wb').write(synthesize('This is a test.', voice='af_heart'))
"
```

Play `/tmp/voice-test.wav` and try other voice names (see the Kokoro project's
voice list) until one sounds right for spoken replies in a car.

## Manual end-to-end check on the tailnet (task 4.1)

This is the real test the weekend slice is for, and it needs real hardware
this change was not written from (a phone on the tailnet, a microphone, a
running Claude Code session) — it is not something to automate in CI. From
another device on the same tailnet:

```sh
curl -X POST \
  "http://<tailscale-ip>:8787/turn?project=<name>" \
  -H "Authorization: Bearer <token>" \
  --data-binary @test-question.wav \
  -o reply.wav
```

Plain HTTP is intentional: Tailscale's WireGuard tunnel already encrypts
traffic between tailnet peers (D2), so there is no separate TLS layer here.
The shared token is the application-level check on top of that.

Play `reply.wav`. It should be a short spoken summary of Claude Code's answer
about the given project. Check `$JARVIS_STATE_DIR/bridge.log` on the machine
for the full reply and the recognized transcript. Ask a follow-up the same
way and confirm (from the log) that the same `session_id` is resumed.
