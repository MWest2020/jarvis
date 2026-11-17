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

**S4. Sentence by sentence.** For an ordinary reply the bridge cuts the text at a sentence end
(`.`, `!`, `?` or `…` followed by white space and a word that does not start in lower case)
and at line breaks, joins a sentence shorter than 30 characters to the next one, and gives
Kokoro the pieces as a list. Kokoro yields one result per piece; each is converted and written
as one chunk before the next is rendered. The words and their order are the text's; only the
white space between sentences can differ. The join is there because Kokoro renders at about
0.045 s per character and speaks at about 0.078: a short lead sentence ("Done.", 0.6 s of
speech) followed by a long one (3 s to render) was a gap of more than two seconds in the
measurement. Each piece is rendered on its own, so the melody across a piece boundary can
differ slightly from the WAV reply.

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
- A write error on a read-back on the bridge: cancelled, as for a WAV that could not be
  written. But a read-back is one or two sentences and fits in the socket and proxy buffers:
  the bridge has usually written all of it, and kept it pending (`delivered`), before the
  phone has played its first word. A write error therefore says almost nothing about what
  was heard. This was already so for the WAV reply.
- **So the phone cancels a read-back it did not play to its end.** A read-back reply carries
  `X-Jarvis-Confirmation-Id` (the id the agent host issued; WAV and stream alike). When it
  ends any other way than played to its end (a tap, a cut stream, a playback error, the
  screen closed while it played), the app sends
  `POST /turn/cancel?project=<name>&id=<id>` with the token and shows "Cancelled. Nothing
  was confirmed." The bridge answers `no` to the agent host for that pending call, exactly
  as for a read-back that could not be written (`ConfirmAnswer`, `ANSWER_NO`), and replies
  `{"cancelled": true}`. It is idempotent: an id that is not pending (answered, expired,
  cancelled before, another read-back, `readonly` mode) changes nothing and gets
  `{"cancelled": false}`. A cancel that arrives while the bridge is still writing the
  read-back is remembered for 60 seconds, and `delivered` then cancels instead of keeping.
  The route takes no project lock.
- **What still holds:** a read-back is pending only from the moment the bridge has written
  it to the moment the phone reports it was not played to its end. A yes in that window
  (the phone stops, the cancel is in flight, Mark says yes at once) cannot come from the
  app: the button takes no new question until the bridge has answered the cancel. **The residual:**
  when the app is killed, or the phone loses the network, before the cancel goes out, nothing
  tells the bridge; the pending call then expires on the agent host after its `expires_s`
  (at most 60 seconds) and on the bridge after the same deadline. A yes within that minute
  still confirms a read-back that was only partly heard. When the cancel fails the app says
  "The read-back was stopped, but the bridge could not be told. Do not say yes; it expires
  within a minute."
- The phone sees a cut body as an `IOException` on read. It stops after what it played, keeps
  the summary on screen and says "The spoken reply broke off. Its text is above." (for a
  read-back: "Cancelled. Nothing was confirmed.", above).

**S7. The app.** `TurnRequests` sends `Accept: audio/L16;rate=24000;channels=1, audio/wav;q=0.5`.
`BridgeClient` returns a streamed reply when the `Content-Type` is that format, and the old
in-memory reply for `audio/wav`. A reader in `:core` turns the body into 16-bit samples
(a sample may be split across two reads), caps the reply at 32 MB and reports a cut body.
The player writes to an `AudioTrack` in `MODE_STREAM` that starts once 300 ms of audio is
buffered: the jitter buffer. From Android 12 that is the track's start threshold and the
buffer holds one second; before, a streaming track starts only when its buffer is full, so
the buffer is the 300 ms. A reply shorter than that is followed by a threshold of silence,
which is never heard: the player waits until the reply's own frames have played, then
empties the track. One worker thread owns the track; a tap sets a flag and closes the
connection, and the worker pauses, empties and releases the track within one read or write.
The waiting tick stops when the headers arrive.
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
