# Tasks: jarvis-speaker-verification

## Current phase: 0 (not started; a later phase, after `jarvis-full-agent` and `jarvis-notifications`)

One build does one phase. The last task of every phase moves the heading and updates
`docs/reference/roadmap.yml` (an item is done only with evidence). Every phase updates
`docs/` in the same pull request (AGENTS.md).

- **(host)**: done by the coding session on the build host or in the cluster.
- **(Mark)**: needs Mark's voice, phone or car. See the last section.

## 0. The measurement

Nothing in `bridge/` changes in this phase.

- [ ] 0.1 A script that computes embeddings and scores for a directory of recordings, for
  the two candidate models of V2, and writes the report of V3 (numbers only)
- [ ] 0.2 Per candidate: the source, the checksum, the licence of the weights and of the
  data they were trained on, recorded in the design; a candidate without a usable licence
  is dropped
- [ ] 0.3 **(Mark)** His recordings: desk, parked, driving as described in V3; the phone's
  microphone and the car's; Dutch and English; full sentences, and "yes" and "ja"
- [ ] 0.4 **(Mark)** Two or three other people, with their consent (open question 3); the
  radio and a podcast in the car
- [ ] 0.5 **(host)** A recording of Mark played from a second phone, in the report as its
  own line
- [ ] 0.6 **(host)** The report: per model and condition the false refusals and false
  accepts at several thresholds, the same under two seconds, the time per utterance on the
  bridge's CPU; go or no-go against open question 1; the recordings of other people deleted
- [ ] 0.7 The outcome written into `design.md` (model, minimum length, threshold, cost) and
  put to Mark as go or no-go; roadmap

## 1. Check and enrolment, mode `observe`

- [ ] 1.1 The model in the image: fetched at build, pinned by SHA-256, loaded once; the
  feature step with a test against reference embeddings (V2)
- [ ] 1.2 The check before transcription; the four outcomes; the score in the turn's log
  line; `JARVIS_SPEAKER_CHECK` with `off` and `observe` (V1, V4, V5)
- [ ] 1.3 The voiceprint store: AES-256-GCM, the key from a file outside the state volume,
  mode 0600, the model checksum inside, no read route; `forget-voice` deletes the file,
  records the key's identifier as retired and prints that the key must be replaced; enrol
  refuses a retired key (V8)
- [ ] 1.4 Enrolment: `jarvis-bridge enrol` opens the ten-minute window; `POST /enrol` only
  inside it; the count, length and agreement rules; profiles; recordings deleted when it
  closes (V7)
- [ ] 1.5 Tests: enrolment refused outside the window; a voiceprint from another model
  refused; the file unreadable without the key; nothing of the voiceprint or the audio in
  the log (a test that greps the log of a full run); no embedding of a non-matching voice
  on disk after a turn; nothing enrolled behaves as off; enrolment under a retired key is
  refused and under a new key works; a copy of the old file cannot be read with the new key
- [ ] 1.6 The smoke test of the image still passes offline with the model inside
- [ ] 1.7 **(host)** The key as a Secret mounted in the pod, in the cluster's own repository
- [ ] 1.8 **(Mark)** Enrol at the desk and in the car; drive with `observe` for two weeks
- [ ] 1.9 Docs: `docs/how-to/speaker-check.md` (what it is and is not, enrolling, the
  modes), `bridge-service.md`, `run-in-cluster.md`; roadmap. The how-to must contain, in
  plain words: that a recording or a cloned voice passes; that a short "yes" from a
  passenger right after Mark's request is accepted and confirms the action (V6); and
  forgetting as two steps: `forget-voice`, then destroying the old key and its older
  versions in the secret store, with the sentence that older backups stay readable until
  the second step is done (V8)

## 2. Mode `enforce`

- [ ] 2.1 The scores collected in `observe` against the threshold of phase 0; adjusted if
  they disagree, with the numbers in the how-to (V10)
- [ ] 2.2 `enforce`: no transcript and no turn on `no_match`; the fixed sentence; the
  short-utterance rule and `JARVIS_SPEAKER_SHORT`; `error` fails closed with its own
  sentence; no start without a threshold (V6, V10)
- [ ] 2.3 Tests: each scenario of "Speaker verification"; the test of V9 (the check stubbed
  to always match: a missing token, a pending gated action without an exact yes, and a
  class that demands a fingerprint are all still refused); no transcript exists after a
  `no_match`
- [ ] 2.4 **(Mark)** Parked test with a second person: their request is refused, his runs;
  their "yes" after his request (it confirms: seen and noted); a recording of him (it
  passes: seen and noted)
- [ ] 2.5 The how-to updated for `enforce`; the passenger-"yes" limitation of V6 stays in
  it, next to `JARVIS_SPEAKER_SHORT`; a docs check in CI fails when that paragraph is gone
- [ ] 2.6 Roadmap; archive the change

## What needs Mark's hands

The five open questions in `proposal.md`, each with a default. Then: the recordings for the
measurement (0.3, 0.4), the go or no-go after it (0.7), enrolment and two weeks of
`observe` (1.8), and the parked test (2.4).
