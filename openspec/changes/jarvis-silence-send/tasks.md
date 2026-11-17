# Tasks: jarvis-silence-send

Built in the same pull request as the proposal (Mark asked for it, jarvis#67). Every step
updates `docs/` in the same commit (AGENTS.md).

- **(Mark)**: needs Mark's phone.

## 1. Core

- [x] 1.1 `Endpointer.kt`: 20 ms frames, RMS, the noise floor from the first 300 ms and its
  tracking, speech and silence, the send, the short sound, nothing heard, the countdown, the
  length to send (H1, H3, H4, H6, H9).
- [x] 1.2 `Endpointing`: the defaults, the silence setting clamped to 1.5-6 s in half
  seconds, the shorter minimum for a yes (H5, H8); `SilenceSend.action` (H7);
  `TalkGesture.holding`.
- [x] 1.3 Tests with synthetic PCM: speech-silence-speech, the noise floor, a short noise,
  long silence first, clipping, speech from the first frame, reads of odd sizes, the yes
  minimum, the decision while held.

## 2. App

- [x] 2.1 `Recorder` hands each read to a listener on its worker thread.
- [x] 2.2 View model: an endpointer per tapped recording, verdicts on the main thread for the
  current recording only, the send through `stopAndSend` (cancel queue first), the drop with
  a note, the 2-minute limit without speech dropped, the yes minimum after a read-back.
- [x] 2.4 Review of #69: a microphone that stops delivering ends the recording; the yes
  minimum only for the first recording after a read-back and never while a cancel is due;
  at the limit a recording whose speech never paused is dropped as too noisy.
- [x] 2.3 Screens: the countdown and its bar on the button, the short-sound note, the hint
  text; the setting (switch and slider) in Settings, stored in `SettingsStore`.

## 3. Docs

- [x] 3.1 `android-app.md` (using it, the setting, how the end is found), the roadmap item.

## 4. On the phone

- [ ] 4.1 **(Mark)** A tapped question that sends by itself in a room and parked in the car;
  a cough that does not send; a yes after a read-back; hold-to-talk unchanged. Note anything
  that sent too early or not at all, and move the constants if needed.
