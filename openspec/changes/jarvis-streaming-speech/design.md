# Design: jarvis-streaming-speech

## Context

- `POST /turn` answers `audio/wav` with a `Content-Length`, after Kokoro has rendered the whole
  spoken line (`bridge/server.py`, `bridge/tts.py`). The bridge speaks HTTP/1.0 and closes
  every connection.
- The app reads the whole body into memory and plays it with `MediaPlayer`
  (`android/core/.../BridgeClient.kt`, `android/app/.../Audio.kt`).
- Kokoro's `KPipeline` is a generator. It splits the text with `split_pattern` (default: line
  breaks) and then only at 510 phonemes, so a 160-character summary is one piece today.
- A read-back (`X-Jarvis-Needs-Confirmation: 1`) becomes pending only once it was rendered and
  written (`FullMode.delivered`); a failure before that cancels it (`undelivered`, D36).
- Decision numbers: `add-voice-bridge` D1 to D9, `jarvis-full-agent` D30 to D40,
  `jarvis-general-conversation` D50 to D56, the app's A1 to A7. This change uses S1 to S8.

## Goals / Non-Goals

Goals: the first word about 3 s sooner on every ordinary turn; nothing changes for a client
that does not ask; a broken stream never looks like a complete one.

Non-Goals: faster rendering, a shorter summary, a codec, streaming the agent's work.

## Decisions

**S1. Opt-in by `Accept: audio/L16;rate=24000;channels=1`.** The bridge streams only when the
request's `Accept` names `audio/L16` with `rate=24000`, `channels` absent or `1`, and a
quality above 0, and the request is HTTP/1.1. `Accept` is what HTTP has for "this is the
format I can play", and it names the rate, so the bridge never streams something the client
did not say it can play; `*/*` or `audio/wav` (HTTP Shortcuts, the app until now) does not
match. The URL stays the same (its query names the project and is in the log). An old bridge
ignores the value and answers with a WAV, and the client tells the two apart by the reply's
`Content-Type`. `JARVIS_STREAM_REPLIES=0` makes the bridge ignore it too.
Alternative: `?stream=1`. Rejected: a second parameter in the logged URL, it does not say
which format, and it is easy to send to a bridge that then ignores it silently.

**S2. Raw 16-bit linear PCM, mono, 24 kHz, big-endian, in a chunked HTTP/1.1 body.**
`Content-Type: audio/L16;rate=24000;channels=1`, `Transfer-Encoding: chunked`,
`Connection: close`, no `Content-Length`. Big-endian because that is what `audio/L16` means
(RFC 2586, RFC 3551); the phone swaps each sample. Chunked because its terminating zero-length
chunk is the only way an HTTP/1.x client can tell a complete body from a cut one; a body ended
by closing the connection cannot (S6). The status line says HTTP/1.1 for this reply only; the
connection is still closed after it.
Alternatives: a WAV header with an unknown length (players disagree about it, and the client
needs a parser either way); Opus or Ogg (an encoder on the bridge, a decoder on the phone, for
a saving that does not matter: 48 KB/s on the tailnet, the same bytes as today's WAV).

**S3. Headers first.** When the agent has answered, the bridge writes the status and every
header it knows: `X-Jarvis-Summary`, `X-Jarvis-Session-Id`, `X-Jarvis-Needs-Confirmation`,
`X-Jarvis-Projects-Changed`, `X-Jarvis-Timing` (the stages so far, so without `tts`), and
`Cache-Control: no-store`. The phone can show the summary before the first sound.

**S4. Sentence by sentence.** For an ordinary reply the bridge calls Kokoro with a split
pattern at a sentence end (`.`, `!`, `?` or `…` followed by white space) and at line breaks.
Kokoro yields one piece per sentence; each is converted and written as one chunk before the
next is rendered. The text itself is not changed: only the white space between sentences is
dropped, which Kokoro drops anyway. Each sentence is rendered on its own, so the melody across
a sentence boundary can differ slightly from the WAV reply.

**S5. Read-backs whole, before the headers.** A reply with `needs_confirmation` is rendered
with Kokoro's own default split (the same call as the WAV reply, so the same audio), before
the status line goes out, and then written in chunks. A rendering error is then still a 500
with nothing pending, as before. The read-back's words and order are exactly what the agent
host built. Its first word comes only a download sooner; read-backs are one or two short
sentences.

**S6. Errors.**
- Rendering fails, or the phone stops reading, after the headers: the bridge logs it, writes
  no terminating chunk and closes the connection. In `full` mode `undelivered` is called (it
  cancels a read-back; an ordinary reply has nothing pending).
- A write error on a read-back, for instance because Mark stopped it with a tap and the
  connection closed before the bridge had written it all: cancelled, as today for a WAV that
  could not be written. A later yes is an ordinary utterance.
- The phone sees a cut body as an `IOException` on read. It stops after what it played, keeps
  the summary on screen and says "The spoken reply broke off. Its text is above."

**S7. The app.** `TurnRequests` sends `Accept: audio/L16;rate=24000;channels=1, audio/wav;q=0.5`.
`BridgeClient` returns a streamed reply when the `Content-Type` is that format, and the old
in-memory reply for `audio/wav`. A reader in `:core` turns the body into 16-bit samples
(a sample may be split across two reads), caps the reply at 32 MB and reports a cut body.
The player writes to an `AudioTrack` in `MODE_STREAM` with a one-second buffer and starts
after 300 ms of audio, or at the end of a shorter reply: the jitter buffer. A tap pauses and
flushes the track and closes the connection. The waiting tick stops when the headers arrive.
No permission, component or dependency is added (D9; the CI checks stay as they are).

**S8. Timing.** The log line gets `first_audio_ms`, from the request's head to the first audio
byte written, in both paths (for the WAV the first byte of the whole file), and
`reply=stream` or `reply=wav`. `tts_ms` stays the time spent in Kokoro and `write_ms` the time
spent writing; while streaming the two interleave.

## Risks

- **Rendering slower than playback** (a loaded node): the track runs dry between sentences and
  there is a gap; nothing breaks. Kokoro renders at about half real time on 4 vCPUs.
- **Melody at sentence boundaries** (S4). Listen on the first turns; the switch
  `JARVIS_STREAM_REPLIES=0` goes back to the WAV.
- **A proxy that buffers** would delay the stream. The cluster's Tailscale proxy forwards TCP.
