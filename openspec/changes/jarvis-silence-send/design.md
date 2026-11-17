# Design: jarvis-silence-send

## Context

- A tap starts a recording and a second tap sends it; holding the button and letting go
  sends too (`TalkGesture.kt`, design A4 of `add-android-client`). The recorder reads
  16 kHz mono 16-bit PCM from `AudioRecord` into memory on a worker thread (`Audio.kt`).
- Before any turn is sent, a read-back cancel that is still due goes to the bridge first,
  and the turn is refused when that fails (`ReadBackCancels.clearBeforeTurn`,
  `jarvis-streaming-speech` S6).
- The bridge takes a spoken yes only with at least 0.3 s of detected speech
  (`JARVIS_CONFIRM_MIN_SPEECH_S`, `jarvis-full-agent` D36).
- The bridge transcribes without a voice filter (`bridge/stt.py`), and Whisper writes words
  into long silence.
- Rules: design A9 (D9): AndroidX, Kotlin and kotlinx only at runtime, three permissions.
- Decision numbers: A1 to A10 (the app), S1 to S8 (streaming speech). This change uses H1 to
  H9.

## Goals / Non-Goals

Goals: one tap per turn; never a turn without speech; the same safety before a send as a
tapped send; testable without a phone.

Non-Goals: starting without a tap (#23), listening while a reply plays, telling Mark's voice
from another (`jarvis-speaker-verification`), any change on the bridge.

## Decisions

**H1. Loudness against a noise floor, in `:core`.** The recording is cut into 20 ms frames
and each frame's loudness is its RMS. The noise floor is the lower quartile of the first
300 ms, at least an RMS of 50 (about -56 dBFS, so a muted microphone does not make every
breath speech). Frames of digital silence (zeros, which many phones give for the first moment
of a recording) do not count toward the 300 ms and never move the floor; the floor is taken
after one second at the latest. A frame is speech when its RMS is at least three times the
floor (about +9.5 dB). After the first 300 ms the floor follows the quiet frames: down
within about 60 ms when the room gets quieter, up over about a second when it gets louder.
The 300 ms are classified once the floor is known, so speech that starts at the tap counts;
and if Mark talks through the whole first 300 ms, the floor drops to the room at his first
pause. `Endpointer` takes the bytes as the recorder reads them (any length, a sample split
across two reads included) and is plain Kotlin, so its tests feed it synthetic PCM.
Alternatives: WebRTC's VAD or Silero on the phone (a native library or a model file: D9, and
a lot more to review for a decision that loudness makes well enough in a car or a room);
Android's `SpeechRecognizer` (Google's service, and it sends audio away).

**H2. No new permission, no new library, nothing sent.** The measurement runs on the PCM the
recorder already holds; it is not stored or logged.

**H3. When it sends.** At least `min_speech` of speech frames (0.6 s) in the current
utterance, then `silence_seconds` of silence frames in a row. Pauses between words are
shorter than the silence and add nothing; the speech frames on both sides of a pause count
together. A cough is about 0.2 to 0.5 s of loud frames: under the minimum.

**H4. A short sound is set aside, and nothing heard stops the recording.** Sound under the
minimum followed by `silence_seconds` of silence is forgotten: it never adds to later speech,
the screen says "Only a short sound so far. Still listening.", and the recording goes on, so
a cough before a question does not cost another tap. When no speech has counted for 10 s
since the start or since the last sound, the recording is dropped ("Nothing heard, so
nothing was sent." or "Only a short sound, so nothing was sent."), the button is free again,
and no error tone plays. The recorder's 2-minute limit drops a recording without speech too,
instead of sending it. 10 s is longer than the longest silence setting, so a pause to think
before speaking is not cut off.
Alternative: stop at once after a short sound. Rejected: another tap for the most common
noise, and the hands-free point is fewer taps.

**H5. A shorter minimum for a yes.** For the first recording after a read-back (a reply that
waits for a spoken yes) played to its end, and within the minute that the bridge keeps it,
the minimum is 0.3 s: a "yes" has about that much loud sound, and the bridge refuses a yes
with less detected speech anyway (D36), so a cough that does get through confirms nothing.

**H6. A countdown.** Once speech has counted and the silence has lasted half a second, the
button says "Sending in N s · tap to send now" and a bar under it runs down. Shorter pauses
show nothing, so the button does not flicker between words. The worker thread posts to the
main thread only when the event, the shown countdown (in tenths of a second) or the
short-sound note changes.

**H7. Hold-to-talk keeps its behaviour.** Sending by itself is only for a recording started
with a tap: while the button is held (`TalkGesture.holding`), the endpointer's verdicts are
ignored, and letting go sends as before. A tap while hands-free sends at once, as today.

**H8. The setting.** "Send when I stop talking" (on or off) and "Silence before sending"
from 1.5 to 6 s in steps of 0.5 s, stored as `silence_send` and `silence_seconds` in the
app's preferences, default on and 3 s. A stored value outside the range is clamped. Both
apply at once, without the Save button, since they are not checked against anything. Off
means taps work exactly as before this change.

**H9. What is sent.** The recording up to half a second after the last speech frame, so
Whisper does not get three seconds of silence to write words into and the upload is smaller.
The send goes through the same path as a tapped send: the read-back cancel queue first, a
refused cancel refuses the turn (`clearBeforeTurn`), then the request. Nothing listens while
a reply plays or while the bridge works: the endpointer only runs during a recording, and a
recording only starts with a tap on an idle button.

## Risks / Trade-offs

- **Not tried on a phone.** The thresholds are reasonable for `VOICE_RECOGNITION` audio at
  arm's length in a room or a parked car; they are constants in `Endpointing` and the first
  phone test may move them (task 4.1).
- **A noise that starts after the first 300 ms and stays loud** (a fan, the engine started)
  counts as speech until the floor has climbed past it, which it does only on frames that are
  not speech: the app then keeps listening until a tap or the 2-minute limit. The tap still
  sends.
- **Someone else talking** keeps the recording open, and their words go into the turn. The
  same as a tapped recording today.
- **A whisper** may stay under three times the floor in a loud car: it is then a short sound,
  and a tap sends it.

## Migration

None. The setting is new and on by default; turning it off gives the old behaviour.
