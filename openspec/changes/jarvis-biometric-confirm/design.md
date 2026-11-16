# Design: jarvis-biometric-confirm

## Context

- `jarvis-full-agent`, D36: a gated tool call is refused by a hook on the agent host and
  stored as *pending*. The wrapper derives a read-back sentence and a hash of the call
  (SHA-256 over the tool name, a zero byte and the command or canonical input). The bridge
  speaks the sentence, keeps `id` and `sha256` for 60 seconds, and on an exact spoken "yes"
  sends `confirm: {id, sha256, answer: "yes"}`. The wrapper starts the confirming turn with
  that hash as the grant.
- D36 considered a signed grant and rejected it "for now": the bridge would sign, the
  verifier would sit on the agent's own host, and it would defend against the same thing as
  the grant in the environment (an agent writing its own yes).
- D40 says what the spoken yes trusts: the bridge. A holder of the bridge's key, or of the
  phone's token, can answer a read-back with a yes.
- What is new here is *who signs*. Not the bridge: the phone, with a key that needs Mark's
  finger. That defends against something D36's rejected option did not: a bridge, a cluster
  or a token in the wrong hands.
- The agent host's code is Python standard library only. The standard library cannot verify
  an elliptic-curve signature. OpenSSH is on the host already (D32).
- The app: Android 10 (API 29) and newer, only AndroidX, Kotlin and kotlinx (A9), three
  permissions, the token under a Keystore key (A5). Until #13 it is debug-signed with a new
  key per build.
- Decision numbers: B1 to B10.

## Goals / Non-Goals

Goals:
- A gated action can be released by a signature that only Mark's finger can produce.
- The agent host checks it without trusting the bridge.
- What Mark saw on the phone and what the signature releases are the same thing.
- The spoken yes keeps working wherever Mark wants it to.

Non-Goals:
- Signing commits, tags or releases (B10, later).
- Making the gate a wall against an agent that attacks its own host (D40, unchanged).
- Hardware attestation of the key; a trusted display path on the phone.

## Decisions

**B1. The key.** An EC P-256 key pair made in the Android Keystore, for signing with
SHA-256 only. User authentication is required for **every** signature (no validity window),
by a strong (class 3) biometric only. In StrongBox when the phone has one, otherwise in the
TEE. Not exportable; the app's backup is already off. The key is invalidated when a new
biometric is enrolled on the phone, so a fingerprint added later by someone who knows the
PIN does not inherit it.
P-256 because Ed25519 in the Keystore needs Android 13, and OpenSSH verifies both.
The prompt is the platform's `BiometricPrompt` (API 28) with the signature object bound to
it: the signature can only be made inside a successful prompt. The prompt is built with
`setConfirmationRequired(true)`: a biometric that needs no deliberate act (a face that is
simply seen) releases nothing until Mark taps to confirm. A glance at the phone must never
sign. No library is added. One permission is added: `USE_BIOMETRIC`.
Alternatives considered:
- *A validity window* ("authenticated in the last 30 seconds"). Rejected: one touch would
  then cover whatever is signed next. One touch, one signature.
- *Android Protected Confirmation*, where the secure hardware itself draws the sentence.
  The right idea, but it exists on very few phones. Not relied on.
- *A PIN or pattern as a fallback.* Open question 3; default no.

**B2. What is signed.** Exactly these bytes, built by the app from the reply's headers:

    jarvis-confirm v1
    id=<the pending id>
    call=<sha256 of the call, hex, as in D36>
    readback=<sha256, hex, of the UTF-8 bytes of the sentence the app shows>
    answer=yes

- `id` is made by the wrapper, random, single use and 60 seconds old at most (D36), so a
  signature cannot be used twice or later.
- `call` is D36's hash. The signature releases that call and no other.
- `readback` binds the sentence. The wrapper builds the same bytes from **its own** pending
  id, call hash and the read-back it issued. It takes none of the three from the request.
  A bridge that shows Mark another sentence gets a signature that does not verify.
- Only a yes is signed. A no needs no proof: anyone may cancel.

**B3. The signature format is OpenSSH's (`SSHSIG`), namespace `jarvis-confirm`.** The app
wraps the Keystore's ECDSA signature in that format (about a hundred lines in `core`,
tested in CI against `ssh-keygen` itself). The wrapper verifies with

    ssh-keygen -Y verify -f /etc/jarvis-agent/confirm_signers -I <name> -n jarvis-confirm -s <signature file>

with the bytes of B2 on standard input and no shell in between. Why this format:
- the verifier is a program that is already on the agent host, audited, and not ours;
- the agent host stays standard library only;
- the namespace is part of what is signed: a signature made for another purpose with the
  same key (B10) is not a confirmation, and the signers file can pin a key to one namespace;
- `git` verifies the same format, which is what makes B10 cheap later.
Alternatives considered: a raw ECDSA signature checked with a Python cryptography package
(a first third-party dependency on the agent host) or with the `openssl` command (works,
but has no namespace and no signers file, so we would write both).

**B4. The agent host verifies; the bridge carries.** The obvious place for the check is the
bridge, which already decides what counts as a yes. The design puts it one step further
in, on purpose: if the bridge verified and then told the agent host "confirmed", the agent
host would still be trusting the bridge, and D40's hole would stay open. The wrapper is where the pending call
and the grant live, so the wrapper checks. The bridge may refuse a malformed request early;
that is a courtesy, not the check.
The wrapper's request becomes version 2: `confirm` may carry `"method":"fingerprint"` and
`"signature":"<the armored signature>"`. Version 1 requests stay valid and mean a spoken
yes. On a fingerprint confirmation the wrapper: finds the pending call by id, checks the
expiry, builds the bytes of B2 from its own state, runs the verification, and only then
starts the confirming turn exactly as D36 step 5 does, with the pending call's hash as the
grant. Anything that fails (no pending call, expired, no signers file, a bad signature,
`ssh-keygen` missing or failing) refuses and clears the pending call. Nothing falls back to
"treat it as a spoken yes".
Honest limit, the same as the gate's: the signers file and the wrapper are on a host where
the agent has `sudo`. This is not a wall against an agent that attacks its own host. It is
a wall against everything that reaches the agent host only through the bridge.

**B5. Method per class.** In `/etc/jarvis-agent/config.toml`:

    confirm_method = "either"                 # "voice" | "fingerprint" | "either"
    [confirm_method_by_class]
    delete = "fingerprint"                    # an example, not the default

A call in several classes takes the strictest of them. The read-back's last words follow
the method: "Say yes to go ahead." for voice and either, "Confirm with your fingerprint."
for fingerprint. When a class demands a fingerprint:
- a spoken yes does not release it. Like any other utterance it clears the pending call
  (D36, step 6), and Jarvis says "Not confirmed. That one needs your fingerprint.";
- with no signer enrolled, the call is not confirmable and "needs the desk".
`confirm = "off"` (D36) still switches the gate off for a class, fingerprint or not. That
setting is changed at the desk, or by the one fixed, read-back command of D36; that command
itself takes the strictest method configured for any class, so a spoken yes cannot switch
off a gate that demands a fingerprint.
Default: `either` everywhere (open question 1). Said plainly in the proposal: `either`
protects against nothing; it is the setting that keeps voice confirmation as it was.

**B6. Enrolment is a desk job.** In the app: Settings, "Fingerprint confirmation", "Create
key". The app refuses when the phone has no strong biometric enrolled. It then shows the
public key as one OpenSSH line and its SHA-256 fingerprint, with a copy button. The public
key is not a secret; the fingerprint is what Mark compares.
On the agent host: `sudo /usr/local/lib/jarvis-agent/signer add <name>` reads the line from
standard input, prints the fingerprint, asks for a typed yes, and writes it to
`/etc/jarvis-agent/confirm_signers` (root-owned) with the option
`namespaces="jarvis-confirm"`. `signer list` and `signer remove <name>` exist. More than one
signer is allowed (a second phone).
These paths are in D36's class `self` already (the gate's configuration): a voice turn
cannot write them, and a signer cannot be added through the bridge. Otherwise the holder of
the bridge's key would enrol their own.

**B7. The bridge.** When the wrapper returns a pending confirmation, the reply to the phone
gets, next to `X-Jarvis-Needs-Confirmation: 1`:
`X-Jarvis-Confirmation-Id`, `X-Jarvis-Confirmation-Sha256`, `X-Jarvis-Confirmation-Method`
(`voice`, `fingerprint` or `either`) and `X-Jarvis-Confirmation-Text` (the read-back,
percent-encoded, byte for byte as the wrapper issued it; the bridge does not reword it).
A new route, `POST /confirm?project=<name>`, with the phone's token and a JSON body of at
most 8 KB: `{"id":"…","answer":"yes","signature":"…"}` or `{"id":"…","answer":"no"}`. The
bridge checks that this id is the pending one for the project and not expired, sends the
version 2 request, and answers like a turn (the spoken result, or "Cancelled"). A request
to `/confirm` is a request for the project like any other: it uses up the pending call.
HTTP Shortcuts and an older app ignore the new headers and keep saying yes.

**B8. The app.** On a reply that needs a confirmation, the app shows the sentence from
`X-Jarvis-Confirmation-Text` in large type, two buttons ("Confirm with fingerprint",
"Cancel") and the seconds left; the spoken read-back plays as before. When the method is
`fingerprint`, or when it is `either` and a key exists, the prompt opens by itself, with
the same sentence as its text. The app hashes **the string it displays**, builds the bytes
of B2, signs inside the prompt, and posts to `/confirm`. Cancel posts a no. After 60
seconds the screen closes and nothing is sent.
A key that was invalidated (a new fingerprint on the phone) is reported in words: "The key
is no longer valid. Create a new one and enrol it at the desk."
The prompt needs the app on screen and the phone unlocked. From a notification
(`jarvis-notifications`) the road is: tap, unlock, the app, the prompt. Never a button in
the notification.

**B9. Record and ping.** The turn record (D38) and the confirmation ping (D36, step 7) say
how the action was confirmed: `voice`, or `fingerprint` with the signer's name and key
fingerprint. A refused signature is recorded with its reason.

**B10. Later: signing tags and commits.** Written down so that this change does not block
it, and not built now.
- `git` can sign with `gpg.format = ssh`; the signing program it calls can be ours. It
  would send "sign this tag" to the phone and wait. That needs a road from the agent host
  to the phone, which is `jarvis-notifications`, and a `git` process held open while Mark
  finds his phone.
- The phone would show "Sign tag v1.4.0 on jarvis at commit 3f2a9c1" and sign in the
  namespace `git`, which is the one `git` verifies. A `git` signature is therefore never a
  confirmation and the other way round (B3).
- For tags and releases only. An agent makes many commits, at times when nobody holds the
  phone.
- Which key: open question 2. Default: a second key, same hardware, same rule.
Why confirmation first: it is the gate, it needs no new road to the phone, and it removes a
weakness that is written down. A signed tag is a nicer badge on a release; it protects
nothing that the gate does not already hold.

## Risks / Trade-offs

- **"Either" is the default and adds no protection.** Stated in the proposal and in the
  how-to, with the one-line change that does.
- **The phone is trusted.** A replaced or tampered app could show one sentence and sign
  another. The Keystore still demands a finger per signature, so it cannot sign unseen in
  the background; it can deceive. Out of scope, said so.
- **Every reinstall loses the key,** and until #13 every update is a reinstall. The how-to
  says to enrol after #13, or to expect a desk trip per update.
- **A finger is not consent.** A sleeping or forced owner. No design here answers that.
- **Classes set to fingerprint cannot be confirmed while driving.** Intended.
- **`ssh-keygen` changes its behaviour or is missing.** The verification fails closed; a
  host check runs it once at install time and on every OpenSSH update.
- **Sixty seconds may be short** for unlock, read and touch. Kept: a longer window widens
  what a stolen moment can do. The parked test (4.4) says whether it is enough.
- **The spoken audio is not what is signed.** The sentence on the screen is. They come from
  the same string, but only the screen's is checked.

## Migration

Nothing changes until a signer is enrolled. With `either` and no signer, the gate behaves
exactly as `jarvis-full-agent` built it. The wrapper accepts version 1 and version 2.

## Open Questions

For Mark: the four in `proposal.md`, each with a default. For the build: whether the
Keystore on Mark's phone offers StrongBox for P-256 (else the TEE), recorded in the how-to.
