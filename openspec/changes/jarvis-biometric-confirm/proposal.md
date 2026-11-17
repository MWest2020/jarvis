# Change: jarvis-biometric-confirm

## Why

Mark, 2026-11-15: "code signing can be done via biometrics" (#22).

`jarvis-full-agent` lets Jarvis merge, deploy, delete and send after a read-back and a
spoken "yes". That yes is a word matched in a transcript. It proves that somebody near the
microphone said yes, or that the speech model heard one. It also comes from the bridge: the
agent host believes the bridge when it says "he said yes", so whoever controls the bridge
can say it too (that change's design D40 states this).

A fingerprint on Mark's phone can do better. The phone holds a key that only signs after
his finger was on the sensor, and the agent host can check the signature itself.

## Two readings, one recommendation

"Code signing via biometrics" can mean two things:

1. **Confirming an irreversible action with the fingerprint.** The phone signs the hash of
   the pending action (the hash of `jarvis-full-agent`, D36); the gate releases the action
   only for a valid signature. **This is the change.** It is small, it sits exactly where
   the spoken yes sits, and it closes a hole that is written down in D40.
2. **Signing commits and tags with a key on the phone**, so a release tag carries proof
   that Mark approved it. **Later.** It needs a road from the agent host to the phone (that
   is `jarvis-notifications`), a `git` process that waits for a finger, and it only makes
   sense for tags and releases: a fingerprint per commit is not workable. This change picks
   a signature format (the one `git` already verifies) so that the later step needs no new
   cryptography. See design B10.

A third reading, signing the Android app itself: no. The release key stays a secret in CI
(#13). A fingerprint can approve; it cannot replace that key.

## What Changes

- **A key in the phone's secure hardware.** Created by the app, never leaves the phone, and
  usable for one signature per fingerprint (or another strong biometric the phone offers).
  Enrolling a new fingerprint on the phone makes the key useless: then it is enrolled again.
- **Enrolment at the desk.** The app shows the public half and its fingerprint. Mark gives
  it to the agent host, where a root-owned command adds it to the list of signers. It can
  not be enrolled by voice or through the bridge.
- **The app shows what it signs.** When a reply asks for a confirmation, the app shows the
  read-back sentence and asks for the finger. It signs the pending id, the hash of the
  action, and the hash of the sentence it showed.
- **The agent host verifies, not the bridge.** The wrapper on the agent host checks the
  signature against its own pending action and its own read-back, with `ssh-keygen`, which
  is already there. The bridge only carries the signature. So a bridge that lies about the
  sentence, or invents a confirmation, gets nowhere.
- **Per class: voice, fingerprint, or either.** Set in the agent host's configuration.
  Default: either (open question 1). Where a class is set to fingerprint, a spoken yes does
  not release it, and the read-back ends with "Confirm with your fingerprint."
- **The record says how.** Each confirmed action is recorded with "voice" or "fingerprint"
  and, for a fingerprint, which key.

Not in this change: signing commits or tags (later, B10); confirming from a notification or
from the lock screen; confirming actions that today "need the desk" (the phone could show
the exact command, so one day it might; not now); key attestation; a PIN as a substitute
for the finger (open question 3).

## What it adds, and what it does not

Adds:
- A yes that was not Mark's (a passenger, the radio, a misheard word, a recording) cannot
  release an action whose class is set to fingerprint.
- For those classes, the phone's token alone is not enough, and neither is the bridge's key
  or control of the cluster: D40's sentence "a holder of the key can also answer its own
  read-back with a yes" stops being true for them.
- The sentence on the phone's screen is bound to the action: a different sentence means a
  signature that does not verify.

Does not add:
- **Anything, while a class is set to "either".** Whoever could say yes for Mark still can.
  "Either" is a convenience (a noisy car), not a protection. The protection starts when a
  class is set to fingerprint.
- Protection for actions that are not gated at all, or against an agent that attacks its
  own host: the list of signers and the verifier live on that host, as the gate does (D40).
- Protection against a tampered app on the phone. The phone is trusted to show the sentence
  it signs.
- Proof of intent. A finger on a sensor says Mark was there and touched it.

## What it costs

- **Eyes and a hand.** A fingerprint needs the phone unlocked, the app on screen and a
  touch. That is for a parked car or the couch, not for driving. Classes set to fingerprint
  cannot be confirmed while driving. That is the point of the setting, and the reason the
  default is "either".
- **A lost key means the desk.** A new fingerprint on the phone, a new phone or a
  reinstalled app: the key is gone and is enrolled again at the desk. Until the app has a
  release signing key (#13), every update is a reinstall. **Do #13 first**, or enrol after
  every update.
- One more Android permission (use the biometric sensor). No new library.

## Phases

| # | Phase | You can then |
|---|---|---|
| 1 | Agent host side | confirm a pending action by hand with a signature from a test key; a bad one is refused |
| 2 | Bridge side | send a signed confirmation through the bridge against a fake agent host |
| 3 | The app | see the sentence, touch the sensor, and hear that it is done |
| 4 | Enrolment and the parked test | merge with your finger, and see a spoken yes refused for a class set to fingerprint |

Order: after `jarvis-full-agent` is built through its phase 3 (#22's own default).

## Impact

- Capability `voice-bridge`: one requirement added (Signed confirmation). The requirement
  "Voice confirmation" of `jarvis-full-agent` is not rewritten; this one adds a second way
  to give the confirmation and a setting that can demand it.
- Capability `android-client`: one requirement added (Biometric confirmation).
- Code, when built: `agent_host/` (request version 2, the method setting, verification,
  the signer command), `bridge/` (a `/confirm` route, three reply headers), `android/` (the
  key, the enrolment screen, the prompt; the signature encoding in `core`),
  `.github/workflows/android.yml` (one more permission). Docs: `android-app.md`, the agent
  host how-to.
- Outside this repository: one root-owned file on the agent host.

## Open questions

Each has a default. If the pull request is merged without an answer, the default is what
is built. They are put to Mark in https://github.com/MWest2020/jarvis/issues/30.

1. **Next to the spoken yes, or instead of it?** Per class.
   *Default: either, for every class. Nothing that works by voice stops working. My advice,
   which you can take with one line of configuration: set `delete`, `account` and `root` to
   fingerprint. You do not need them while driving, and they are the ones a wrong yes
   cannot undo.*
2. **Which key signs commits and tags, later?** The same key can, safely: the signature
   format keeps the two uses apart.
   *Default: nothing is built now. When it is, a second key in the same hardware under the
   same fingerprint rule, because a commit-signing key is published on GitHub and outlives
   a confirmation key.*
3. **May the phone's PIN or pattern stand in for the fingerprint** (wet hands, a sensor
   that fails)?
   *Default: no. A PIN can be watched; and the spoken yes is already the fallback wherever
   a class is "either".*
4. **Face unlock?** Android only lets a face release a key on phones whose face sensor it
   rates as strong (few do).
   *Default: whatever the phone rates as a strong biometric is accepted, with an explicit
   tap after the face is recognised.*
