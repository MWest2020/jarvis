# Change: jarvis-streaming-speech

## Why

Mark, 2026-11-17: "build the streaming speech next".

The per-stage timing of #60 shows where the first word waits: after the agent answers, Kokoro
renders the whole spoken line (about 0.038 s per character, 5.4 s for a 143-character
summary) before the bridge sends a single byte, and the phone then downloads the whole WAV
before it starts to play. The first sentence of a summary is ready after about 2 s; the rest
renders faster than it plays. Sending each sentence as soon as it is rendered, and playing it
as it arrives, brings the first word about 3 s forward on every turn.

Mark asked for this directly, so it is built in the same pull request as the proposal; the
pull request is the approval point.

## What Changes

- **The bridge can stream a reply.** A client that asks for it with
  `Accept: audio/L16;rate=24000;channels=1` gets the status and every `X-Jarvis-*` header as
  soon as the agent has answered, then the audio as raw 16-bit PCM in a chunked HTTP/1.1 body,
  one sentence at a time while Kokoro renders the next. Every other client gets the WAV exactly
  as before.
- **Read-backs are not split.** A reply that asks for a spoken yes is rendered whole, with the
  same Kokoro call as the WAV reply, and before any byte goes out; only then is it streamed.
- **A broken stream is visible as broken.** A rendering or write error after the headers ends
  the body without its terminating chunk and closes the connection; the phone keeps the text
  on screen and says the spoken reply broke off.
- **A read-back that was not heard to its end is cancelled.** A read-back reply carries its
  id; when the phone did not play it to its end it sends `POST /turn/cancel` and the bridge
  answers `no` to the agent host. Before, a read-back stayed pending as soon as the bridge had
  written it, heard or not (design S6).
- **The app plays the stream** with `AudioTrack` in streaming mode and a short pre-roll, stops
  on a tap, and falls back to the WAV player when the bridge answers with a WAV.
- **Timing:** the log line gets `first_audio_ms` (from the request's head to the first audio
  byte written) and `reply=stream|wav`.
- **A switch on the bridge:** `JARVIS_STREAM_REPLIES=0` answers every client with a WAV.

Not in this change: a shorter summary, other Kokoro settings, a different audio codec, playing
while the agent still works (the agent host answers once, at the end).

## Decided by default

1. **Opt-in by `Accept`**, not by a query parameter (design S1).
2. **Raw L16 PCM in a chunked body**, not a WAV without a length and not Opus (design S2).
3. **Sentence by sentence**, split at `.`, `!`, `?` or `…` followed by white space, and at
   line breaks (design S4).
4. **Read-backs rendered whole and before the headers**, so they save only the download
   (design S5).
5. **The waiting tick stops when the headers arrive**, with the summary on screen; the first
   sentence follows about 2 s later (design S7).
6. **Streaming on by default** on the bridge for a client that asks; `JARVIS_STREAM_REPLIES=0`
   switches it off without a new image of the app.

## Impact

- Capability `voice-bridge`: one requirement added (Streamed reply).
- Capability `android-client`: one requirement added (Streamed playback).
- Code: `bridge/tts.py`, `bridge/server.py`, `bridge/timing.py`, `bridge/config.py`; the app's
  `:core` (request header, stream reader) and `:app` (player, view model).
- Wire: one more `Accept` value from the phone and one more reply format from the bridge.
  `X-Jarvis-*` headers unchanged. HTTP Shortcuts and older apps are not affected.
- Deployment order: either. An old bridge ignores the `Accept` value and the app plays its WAV;
  an old app never asks and gets the WAV.
- Docs: `bridge-service.md`, `android-app.md`, the roadmap.
