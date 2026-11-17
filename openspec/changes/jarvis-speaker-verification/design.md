# Design: jarvis-speaker-verification

## Context

- A turn today: the phone posts 16 kHz mono audio (A3) with the token; the bridge
  transcribes it (faster-whisper), runs the agent and speaks the summary.
- The bridge's image already holds PyTorch on the CPU (for Kokoro) and `onnxruntime` (for
  faster-whisper's voice-activity filter). It has no encryption package.
- Rules: sovereign and local (D9: no hosted speech service); nothing of Mark's setup in the
  repository; the pod's root filesystem is read-only, with one state volume.
- `jarvis-full-agent`, D36: a confirmation is an exact "yes" with at least 0.3 seconds of
  speech, within 60 seconds of the read-back.
- The talk control is push-to-talk. Other voices get in while Mark holds it, or when
  someone else presses it. A wake word, if it ever comes, makes this matter more.
- A voiceprint is biometric data. It is Mark's own, on his own machines; this design still
  treats it as the most sensitive thing the bridge stores, because unlike the token it
  cannot be replaced.
- Decision numbers: V1 to V11.

## Goals / Non-Goals

Goals:
- In `enforce` mode, an utterance that is clearly another person does not start a turn and
  is not transcribed.
- Mark is rarely refused, and knows when he is.
- The voiceprint never leaves the bridge and can be deleted with one command.
- Nothing is decided on numbers from a paper: the threshold comes from Mark's own voice in
  his own car.

Non-Goals:
- Authentication. Stopping a recording, a clone or anyone who wants to get in (V9).
- Identifying other speakers, or keeping anything about them.
- Running the model on the phone. It would need a runtime library that A9 forbids, and the
  audio goes to the bridge anyway.

## Decisions

**V1. On the bridge, before transcription.** The check is the first thing done with the
audio. On a refusal in `enforce` mode the audio is dropped: no transcript, no agent turn,
no line in the conversation. That is what "not react" means, and it is also the least that
is kept of a person who never agreed to be recorded.

**V2. The model: a small speaker-embedding model in ONNX, on the runtime already in the
image; SpeechBrain's ECAPA is the reference it is measured against.** Both are local.
- ECAPA-TDNN through SpeechBrain is the well-known baseline. It would add SpeechBrain,
  `torchaudio` and their dependencies to the image.
- An ONNX export of a model of the same family (for example from the WeSpeaker or
  3D-Speaker projects) runs on `onnxruntime`, which is there, and needs only the feature
  step written out (filter-bank features, a few lines with what is installed).
Phase 0 measures both on the same recordings. The ONNX model is built unless it is clearly
worse; "clearly" is more than two points of false refusals at the same false-accept rate.
The file is fetched at image build like the other models, pinned by SHA-256, and its
licence is checked and recorded before it is used (task 0.2). The bridge never downloads a
model at run time.

**V3. Phase 0 is a measurement and changes nothing.** A script in the repository, run on
the build host, over recordings that stay on that host and are never committed:
- Mark: at the desk, parked, and driving (as a passenger holding the phone, or dictated at
  a standstill with the engine and fan on, then on a motorway as a passenger); through the
  phone's own microphone and through the car's; in Dutch and in English; sentences of the
  length he really uses, and the bare "yes" and "ja".
- Two or three other people, with their consent (open question 3), same sentences.
- The radio and a podcast, as the voices that are in a car anyway.
- A recording of Mark played from a second phone. Expected result: it passes. It is in the
  report so that nobody has to take V9 on faith.
The report gives, per model: the score distributions, the false-refusal rate for Mark per
condition at several thresholds, the false-accept rate for the others, the same for
utterances under two seconds, and the time per utterance on the bridge's CPU. It ends with
go or no-go against open question 1. The other people's recordings are deleted when the
report is written; the report holds numbers only.

**V4. Three modes: `off`, `observe`, `enforce`.** `JARVIS_SPEAKER_CHECK`, default `off`.
- `observe`: the score and the outcome go into the bridge's log line for the turn; nothing
  is refused. This is how the threshold is confirmed on real drives before it can lock
  Mark out.
- `enforce`: see V6.
With no voiceprint enrolled, `observe` and `enforce` behave as `off` and the bridge says so
once at start-up. A missing voiceprint must never mean "refuse everyone" (a deleted file
would lock Mark out of his own car) and never "accept everyone" silently.

**V5. Outcomes.** Per utterance, from the detected speech in it:

| Outcome | When |
|---|---|
| `match` | the score is at or above the threshold |
| `no_match` | it is below |
| `too_short` | less speech than the minimum (default 1.5 s; set from phase 0) |
| `error` | the model or the voiceprint could not be used |

The score is the cosine similarity between the utterance's embedding and the enrolled one;
with several enrolled profiles (V7) it is the highest.

**V6. What `enforce` does.**
- `match`: the turn runs. Nothing else changes: no extra right, no shorter confirmation.
- `no_match`: no transcript, no turn. The reply is one fixed sentence (open question 2).
  The log line holds the time, the project, the score and the outcome, not the audio and
  not a transcript.
- `too_short`: accepted when the last utterance for that project was a `match` and no more
  than 60 seconds ago, otherwise handled as `no_match`. So the "yes" after a read-back
  still works, and so does "go on". **The limit that comes with it:** a passenger who says
  "yes" right after Mark's request is accepted, and confirms the action. This sentence is
  kept in the how-to, next to the setting, for as long as the rule exists. Configurable to refuse instead
  (`JARVIS_SPEAKER_SHORT=refuse`), which makes voice confirmation impossible and only makes
  sense with the fingerprint.
- `error`: the turn is refused with "the voice check is not working", and the bridge's
  health line says so. It fails closed in `enforce`, because a silent fall back to "accept
  everyone" would not be noticed. Mark's way out is the mode setting.
- No lock-out and no counting of refusals. The token is what stands against an attacker; a
  lock-out would only add a way to shut Mark out.

**V7. Enrolment.** Started from the bridge's own machine: `jarvis-bridge enrol` (in a
cluster: run inside the pod) opens a window of ten minutes. In that window, and only then,
`POST /enrol` accepts recordings with the phone's token; the app is not changed, the how-to
gives a `curl` line and the HTTP Shortcuts variant. Requirements: at least 10 utterances
of 3 seconds or more, at least 40 seconds of speech together, and they must agree with each
other (a recording far from the rest is refused and asked again). The voiceprint is the
normalised mean of the embeddings. Up to three named profiles ("desk", "car") may exist;
each is enrolled this way.
When the window closes the recordings are deleted; only the voiceprint remains.
Enrolling again replaces a profile; it is the answer to a new phone, a new car, or a voice
that changed.
Why not by voice or by token alone: whoever can enrol decides whose voice is "Mark". A
voice turn in `full` mode that tries to run the enrol command meets the gate of
`jarvis-full-agent` (a cluster command that is not on the read list, with no sentence to
read back): it needs the desk.

**V8. The voiceprint.** One file on the bridge's state volume, mode 0600:
- encrypted with AES-256-GCM. The key is read from a file outside the state volume (in a
  cluster: a Secret mounted read-only). This adds the `cryptography` package, the first
  encryption dependency of the bridge, pinned like the rest;
- holds the profiles' vectors, the model's checksum (a voiceprint from another model is
  refused, not compared) and the enrolment date. No audio;
- no route returns it. There is `enrol` and `jarvis-bridge forget-voice`. Nothing reads
  it out;
- **forgetting retires the key too.** Deleting the file does not reach the copies of it in
  older backups of the state volume: they stay readable for whoever has the key. So
  `forget-voice` deletes the file, records the key's identifier (a hash of the key) as
  retired, and says that the key must now be replaced. The bridge refuses to enrol a voice
  under a retired key, so a new voiceprint always gets a new key. The bridge cannot destroy
  the old key itself: it is mounted read-only and lives in the cluster's secret store.
  Destroying it there, with its older versions, is the second step of forgetting, in the
  how-to; only after that step are the old backups unreadable. Until then they are not,
  and the how-to says so in those words;
- never in a log, a turn record, an error message, the agent's prompt, the agent host or
  git; the state volume's backup holds only the encrypted file.
What the encryption is worth, said plainly: it protects a copy of the volume or a backup
that is read without the key. It does not protect against someone who controls the running
bridge, who has both. And the stake: a vector cannot be turned back into Mark's voice, but
it can be used to recognise his voice in other recordings, and he cannot get a new one.
Alternative considered: the vectors themselves in the cluster's secret store. Rejected for
now: enrolment would then need a road from the pod into the secret store, a wider right
than a file on its own volume.

**V9. What this does not stop.** Written here so that no later change builds on it by
mistake:
- a recording of Mark, played back;
- a synthetic copy of his voice;
- anyone at all, for an utterance under the minimum length that follows one of Mark's;
- two people speaking at once (the result is then unpredictable in both directions).
Therefore: the check is never a condition for *more*. No code path may read "the speaker
matched" as a reason to skip the token, the gate, the read-back, the yes or the
fingerprint, or to accept a confirmation. It can only make the bridge do less. A test
holds this: with the check stubbed to always `match`, every refusal of the other changes
still happens.

**V10. The threshold.** One number in the configuration, with no default in the code: the
bridge refuses to start in `enforce` without one. It is chosen in phase 0 at the point
where Mark's false refusals in the car meet open question 1, and checked again on the
scores that `observe` collected. A model update means a new voiceprint and a new threshold.

**V11. Cost per turn.** One model run on audio the bridge already holds. In `enforce` it
runs before transcription (V1), so its time is added to every turn; in `observe` it may run
next to the transcription. Measured in phase 0; if it adds more than 300 ms on the bridge's
CPU the smaller model is taken.

## Risks / Trade-offs

- **It is mistaken for security.** The largest risk. Answer: the proposal's own section,
  V9, the test of V9, and the fixed sentence in the how-to.
- **Mark is refused in his own car.** `observe` first, a threshold that leans his way, a
  spoken reason, profiles per microphone, and the mode setting as the way out. If phase 0
  shows more than the agreed rate, it is not built.
- **The passenger's "yes"** still confirms (V6). Stated in the proposal. The fingerprint
  closes it.
- **Biometric data of other people.** Their audio is turned into a vector to compare it.
  That vector is never stored, and in `enforce` their words are not transcribed. In
  `observe` everything is transcribed as today. The measurement uses consenting people and
  deletes their recordings.
- **Short commands.** Much of what is said to Jarvis is short. The inheritance rule keeps
  it usable and is exactly as weak as it sounds.
- **Voices change** (a cold, age) and microphones differ. Profiles, and enrolling again.
- **The model's licence or training data** turns out to be unsuitable. Checked before use
  (task 0.2); the other candidate is the fallback.
- **A leaked voiceprint cannot be rotated.** Encrypted at rest, no read route, one command
  to delete; and the honest limit in V8.

## Migration

`off` is the default. Nothing changes for anyone who does not enrol a voice and set a
mode.

## Open Questions

For Mark: the five in `proposal.md`, each with a default. For the measurement: which model
(V2), the minimum length (V5), the threshold (V10) and the cost (V11).
