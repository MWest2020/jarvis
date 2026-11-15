# Tasks: jarvis-biometric-confirm

## Current phase: 0 (not started; waits for approval and for `jarvis-full-agent` phases 1 to 3)

One build does one phase. The last task of every phase moves the heading and updates
`docs/reference/roadmap.yml` (an item is done only with evidence). Every phase updates
`docs/` in the same pull request (AGENTS.md).

- **(host)**: needs the agent host itself; done by the coding session there.
- **(Mark)**: needs Mark's hands or phone. See the last section.

## 1. Agent host side

`agent_host/`, Python standard library only; tests use keys made with `ssh-keygen` in CI
in place of the phone.

- [ ] 1.1 Request version 2: `confirm` with `method` and `signature`; version 1 unchanged;
  an unknown field is still an error; the 64 KB cap holds (B4)
- [ ] 1.2 The bytes of B2, built from the wrapper's own pending id, call hash and issued
  read-back; nothing from the request goes into them
- [ ] 1.3 Verification by `ssh-keygen -Y verify` with the signers file, the namespace
  `jarvis-confirm`, the message on standard input, an argument vector and no shell; a
  timeout; every failure refuses and clears the pending call (B3, B4)
- [ ] 1.4 `confirm_method` and `confirm_method_by_class`; the strictest class wins; the
  read-back's last words per method; a spoken yes for a fingerprint class clears and says
  so; no signer means "needs the desk"; the fixed `confirm on|off` command takes the
  strictest method configured (B5)
- [ ] 1.5 `signer add|list|remove`: root only, reads one public key line, prints its
  fingerprint, asks for a typed yes, writes the signers file with the namespace option; the
  file is in the class `self` (B6)
- [ ] 1.6 The record and the ping carry the method, the signer's name and the key
  fingerprint; a refused signature is recorded with its reason (B9)
- [ ] 1.7 Tests: a valid signature releases the call once; refused: a signature over
  another id, another call hash, another read-back, the answer "no", another namespace
  (`git`), a key that is not enrolled, a key enrolled without the namespace option used in
  another namespace, a truncated signature, an expired pending call, a second use, a
  missing signers file, a failing `ssh-keygen`; a version 1 yes for a fingerprint class; a
  call in two classes; `confirm off` by spoken yes while one class demands a fingerprint
- [ ] 1.8 **(host)** `ssh-keygen -Y verify` checked on the agent host's OpenSSH with an
  ECDSA P-256 key; added to the update runbook
- [ ] 1.9 Docs: the agent host how-to (the setting, enrolment, removal, what "either" does
  and does not protect); roadmap

## 2. Bridge side

- [ ] 2.1 The four reply headers of B7; the read-back passed byte for byte
- [ ] 2.2 `POST /confirm`: the phone's token, the 8 KB cap, the id must be the pending one
  for the project and not expired, the version 2 request, the answer as for a turn; it uses
  up the pending call like any request
- [ ] 2.3 Tests against a fake agent host: a signed yes, a no, an unknown id, an expired
  one, a body that is not JSON, a spoken yes still working when the method is `either`
- [ ] 2.4 Docs: `bridge-service.md`; roadmap

## 3. The app

- [ ] 3.1 `core`: the bytes of B2; the OpenSSH signature format around an ECDSA signature
  (public key blob, namespace, hash name, the two integers); the public key line and its
  fingerprint. Tests on the JVM with a software key, and a CI step that verifies the result
  with the runner's `ssh-keygen`
- [ ] 3.2 The key (B1): P-256, sign only, authentication per use, strong biometric only,
  invalidated by a new enrolment, StrongBox when present; the two API paths (29, and 30 and
  newer)
- [ ] 3.3 Settings: "Fingerprint confirmation": create, show the public line and the
  fingerprint with a copy button, delete; refusal without a strong biometric (B6)
- [ ] 3.4 The confirmation screen (B8): the sentence as received, the countdown, the two
  buttons, the prompt bound to the signature object, the post to `/confirm`, cancel, the
  time-out; the invalidated-key sentence
- [ ] 3.5 `.github/workflows/android.yml`: `USE_BIOMETRIC` added to the allowed
  permissions; the dependency check unchanged and green
- [ ] 3.6 Tests: the hash is taken from the displayed string; nothing is sent after a
  failed or dismissed prompt; nothing after the time limit; no confirmation UI when the
  headers are absent
- [ ] 3.7 Docs: `android-app.md` (creating the key, what is signed, why a reinstall loses
  it, #13 first); roadmap

## 4. Enrolment and the parked test

- [ ] 4.1 **(Mark)** Create the key on the phone; read the fingerprint
- [ ] 4.2 **(host)** `signer add` with the line Mark gives; the fingerprints compared
- [ ] 4.3 **(host)** The classes Mark chose set to `fingerprint` (open question 1)
- [ ] 4.4 **(Mark)** Parked test: merge with the finger; a spoken yes refused for a
  fingerprint class; cancel on the screen; let one time out; note whether 60 seconds is
  enough
- [ ] 4.5 Roadmap; archive the change

## Later, not in this change

Signing tags and releases with a key on the phone (design B10). Needs
`jarvis-notifications`. Gets its own proposal.

## What needs Mark's hands

The four open questions in `proposal.md`, each with a default. Then: the release signing
key of #13 first (else every app update loses the confirmation key), creating the key
(4.1), and the parked test (4.4).
