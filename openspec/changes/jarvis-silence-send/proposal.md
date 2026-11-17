# Change: jarvis-silence-send

## Why

Mark, 2026-11-17 (jarvis#67): "would be nice if I don't say anything for 3 secs, he would
see it as input." Today a tapped recording goes out only on the second tap, so every turn
needs two touches. Sending by itself once Mark stops talking makes a turn one tap, and is a
step toward wake-on-voice (#23).

Mark asked for this directly, so it is built in the same pull request as the proposal; the
pull request is the approval point.

## What Changes

- **The app finds the end of a spoken question by itself.** After a tap starts a recording,
  the app measures the loudness of the recorded audio on the phone and stops and sends once
  `silence_seconds` of silence (default 3) follow at least 0.6 s of speech. Nothing leaves the
  phone for this, there is no new library and no new permission (design H1, H2).
- **Never a turn without speech.** A short sound (a cough, a click, the tap itself) is set
  aside, and the screen says so while the app keeps listening. When nothing that counts as
  speech is heard for 10 s, the recording is dropped with a short note, not sent (H3, H4).
- **A spoken yes is short.** Right after a read-back has played to its end the minimum is
  0.3 s, the bridge's own minimum for a yes, so "yes" goes by itself too (H5).
- **A countdown on the button** while the silence is counted ("Sending in 2 s"), and a bar
  that runs down. A tap still sends at once (H6).
- **Hold-to-talk is unchanged**: while the button is held nothing is sent by itself; letting
  go sends (H7).
- **A setting**: "Send when I stop talking" on or off, and the silence from 1.5 to 6 s in
  half seconds. On by default with 3 s (H8).
- The silence at the end is cut to half a second before upload; the read-back cancel queue
  and its refusal rule run before every send, as before (H9).

Not in this change: starting to listen without a tap (wake word, #23), listening while a
reply plays (barge-in), any change to the bridge.

## Decided by default

1. **Loudness against a measured noise floor**, not a trained voice detector (H1).
2. **3 s of silence, at least 0.6 s of speech, 10 s with nothing heard**; only the silence is
   a setting (H3, H8).
3. **Keep listening after a short sound** instead of stopping (H4).
4. **On by default** (H8).

## Impact

- Capability `android-client`: one requirement added (Send at the end of speech).
- Code: the app's `:core` (`Endpointer.kt`, `TalkGesture.kt`) and `:app` (recorder, view
  model, settings, screens). The bridge, the wire and HTTP Shortcuts are unchanged.
- Docs: `android-app.md`, the roadmap.
