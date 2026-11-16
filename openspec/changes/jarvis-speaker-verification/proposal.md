# Change: jarvis-speaker-verification

## Why

Mark, 2026-11-15: "long term it should recognize my voice. and no react to others" (#23).

Jarvis acts on whatever the microphone picks up while the talk control is held: a passenger
who answers for Mark, a child in the back seat, the radio. With `jarvis-full-agent` an
utterance can start real work. Jarvis should react to Mark's voice and not to another.

**This is a later phase.** It comes after `jarvis-full-agent` and `jarvis-notifications`,
and it starts with a measurement, not with a build: nobody knows yet how often a model
would refuse Mark himself in a moving car.

## What Changes

- **A speaker check on the bridge, before anything else happens with the audio.** A small
  local model turns the utterance into a vector (an embedding) and compares it with Mark's
  enrolled voice. No cloud service, no new process: it runs in the bridge, next to the
  speech recognition, on the runtime the image already has.
- **Three modes.** `off` (the default, and today's behaviour), `observe` (every turn runs;
  the score is recorded, so the threshold can be set on real drives), `enforce` (an
  utterance that does not match is refused).
- **On a refusal nothing happens with what was said.** It is not transcribed, not sent to
  the agent, not kept. Jarvis says one fixed sentence, "I did not recognise your voice", so
  that Mark knows why when it was him (open question 2).
- **Enrolment at the desk.** About a minute of Mark speaking, through the phone and the
  microphones he really uses. It can only be started from the bridge's own machine, never
  with the phone's token alone: otherwise whoever holds the token enrols their own voice.
- **The voiceprint is treated as a secret that cannot be changed.** One encrypted file on
  the bridge, the key somewhere else, no route that returns it, no copy in a log or a
  record, and one command that deletes it. The recordings used for enrolment are deleted
  when it is done.
- **A threshold that comes from measurement** and is tuned to keep Mark in, not to keep an
  attacker out.

Not in this change: recognising who else is speaking (other voices are never enrolled and
their vectors are never kept); a wake word; telling a live voice from a recording.

## What it is, and what it is not

It is a **filter against other people by accident**. It is **not authentication**, and it
never becomes one:

- **A recording of Mark passes.** So does a cloned voice, which today takes a few seconds
  of audio and a free tool. Mark's voice is not a secret: it is in every call and every
  voice message he ever sent. This change contains nothing that tells a live voice from a
  played one, and the models that claim to are not reliable outside their test sets.
- **So it replaces nothing.** The tailnet and the token decide who can reach the bridge.
  The read-back and the yes, or the fingerprint of `jarvis-biometric-confirm`, decide
  whether an irreversible action runs. A voice match adds no permission, shortens no
  confirmation and counts as no consent. With the check switched off, Jarvis is exactly as
  safe as it was.
- **A short "yes" cannot be checked.** A model needs a second or two of speech; "yes" is
  a third of a second. In `enforce` mode a short utterance is accepted when it follows a
  matching one within the confirmation window. A passenger who says "yes" right after Mark
  asked for a merge therefore still confirms it. The fingerprint is the answer to that, not
  the voice.
- **It will refuse Mark sometimes**: road noise, a cold, a new microphone, someone talking
  over him. That is why it starts in `observe` and why the threshold leans his way.

## Phases

| # | Phase | You can then |
|---|---|---|
| 0 | The measurement (no change to the bridge) | read how often Mark is refused and others pass, per model, at the desk and in the car, and decide whether to go on |
| 1 | Check and enrolment, mode `observe` | see a score on every turn; nothing is refused |
| 2 | Mode `enforce` | have another voice refused, with the threshold from phase 1's drives |

Phase 0 ends in a go or no-go for Mark (open question 1). If no model meets the bar, this
change stops there and says so.

## Impact

- Capability `voice-bridge`: two requirements added (Speaker verification, Voiceprint).
- Code, when built: `bridge/` (the check before transcription, the modes, enrolment, the
  encrypted store), `deploy/` (the model in the image, pinned by checksum), one dependency
  for the encryption. Docs: a new how-to, `bridge-service.md`, `run-in-cluster.md`.
- Outside this repository: a Secret with the voiceprint's key in the cluster.
- The app: nothing in phases 0 to 2. A refusal is an ordinary spoken reply.
- Turn time: one more model run per utterance; phase 0 measures it. Target: under 300 ms on
  the bridge's CPU.

## Open questions

Each has a default. If the pull request is merged without an answer, the default is what
is built. They are put to Mark in https://github.com/MWest2020/jarvis/issues/30.

1. **What is good enough to switch `enforce` on?** *Default: Mark is refused in at most 2
   of 100 utterances in the car, and at that threshold the other voices of the household
   are refused at least 9 times in 10. If no model reaches that in phase 0, nothing more is
   built and you get the numbers.*
2. **What should Jarvis do with a voice it does not recognise:** say so, play a tone, or
   stay silent? Silence is the purest "do not react", but when it refuses you by mistake
   you would not know why.
   *Default: one fixed sentence, "I did not recognise your voice", and nothing else.*
3. **Who records the other voices for the measurement?** The false-accept number needs a
   few people other than you, with their consent, and their recordings are deleted after
   the measurement.
   *Default: you ask two or three people at home; without them phase 0 reports only how
   often you are refused, and `enforce` is not recommended on that basis.*
4. **May a fingerprint override a refusal** ("it is me") for the next few minutes, once
   `jarvis-biometric-confirm` exists?
   *Default: not in this change. Retry, or switch the check off at the desk.*
5. **When?** *Default: phase 0 starts after `jarvis-notifications` phase 4; nothing is
   scheduled before that.*
