# Tasks: jarvis-streaming-speech

Built in the same pull request as the proposal (Mark asked for it). Every step updates
`docs/` in the same commit (AGENTS.md).

- **(Mark)**: needs Mark's phone.

## 1. Bridge

- [x] 1.1 `bridge/tts.py`: `stream_pcm(text, voice, whole)`, one big-endian L16 piece per
  sentence (S4), or Kokoro's default split for a read-back (S5).
- [x] 1.2 `bridge/server.py`: the `Accept` match (S1), headers first and the chunked body
  (S2, S3), read-backs rendered before the headers (S5), a cut body on an error (S6),
  `delivered`/`undelivered` from the stream's result.
- [x] 1.3 `bridge/config.py`: `JARVIS_STREAM_REPLIES` (default on, `0` off).
- [x] 1.4 `bridge/timing.py`: `first_audio_ms`, `reply=stream|wav` (S8).
- [x] 1.5 Tests: headers before the body, one chunk per sentence and the first before the
  second is rendered, the error mid-stream, a read-back rendered before the headers and
  cancelled when it fails, the old client and HTTP/1.0 unchanged, the switch.

## 2. App

- [x] 2.1 `:core`: the `Accept` header, the reply format, the stream reader (byte order, a
  sample split across reads, the size cap, a cut body), `TurnOutcome.Streamed` (S7).
- [x] 2.2 `:app`: `AudioTrack` player with pre-roll and tap-to-stop; the view model plays a
  stream or a WAV, says "The spoken reply broke off. Its text is above." on a cut body.
- [x] 2.3 Tests in `:core` against a loopback server that sends chunked bodies, cut bodies
  and odd chunk boundaries.

## 3. Docs and evidence

- [x] 3.1 `bridge-service.md` (the reply formats, the switch, the log fields),
  `android-app.md` (playback, the new error), the roadmap item.
- [x] 3.2 Kokoro measured once on `agent-lxc`: time to the first sentence of a typical
  summary against the whole line.
- [ ] 3.3 (Mark) A turn on the phone with the new APK and bridge: `first_audio_ms` from the
  log next to the old `tts_ms`, and how it sounds at sentence boundaries.
