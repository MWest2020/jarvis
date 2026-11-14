# Design: add-android-client

## Context

- The bridge: `POST /turn?project=<name>` with `Authorization: Bearer <token>`, the body is
  the recorded audio, the answer is `200 audio/wav` with `X-Jarvis-Summary` (percent-encoded)
  and `X-Jarvis-Session-Id`. Errors: 400, 401, 409 (the project is busy), 413 (over 10 MB),
  502. A turn takes about 17 seconds (`docs/how-to/run-in-cluster.md`, Test on 2026-11-14).
- It speaks plain HTTP and is reachable only over the tailnet, where WireGuard encrypts.
- The phone is an Android on the tailnet. The build host has no Android SDK; CI builds.
- Rules that apply (AGENTS.md, D9 of `add-voice-bridge`): private network plus token,
  sovereign by default, the token never in git.
- Decisions here are numbered A1 to A10, to stay clear of D1 to D25 in the other changes.

## Goals / Non-Goals

Goals:
- Replace the HTTP Shortcuts setup with something that needs no hand-building, keeps the
  token properly, is never silent while waiting, and says what went wrong.
- Usable in a parked car: one screen, one large control, large text.
- Small enough to review in one sitting, and buildable today.

Non-Goals:
- Use while driving. That needs the screen-off service, the wheel button and the tones of
  `add-drive-mode`, and its parked tests first.
- The drive protocol, QR pairing, Bluetooth routing, a release key, a Play Store listing.
- Any change to the bridge.

## Decisions

**A1. Two Gradle modules: `core` (plain Kotlin) and `app` (Android).** Everything that can
be wrong without a phone is in `core`: building the request, mapping statuses and
exceptions to messages, the WAV header, the Tailscale address check, the hold-or-tap logic,
and the HTTP exchange itself. Its tests run on any JVM, with a real HTTP server on loopback
standing in for the bridge. `./gradlew :core:test` needs no Android SDK (CI runs it with the
SDK variables unset to prove it), so the factory can test this part on a host without one.
`app` holds what needs the platform: the microphone, playback, the Keystore, the screens.
Alternative considered: one Android module with Robolectric. Rejected: another dependency,
slower, and the SDK is then needed for every test.

**A2. The platform's HTTP client, not OkHttp.** `HttpURLConnection` does one POST with a
body and reads one answer; that is all `/turn` needs. OkHttp would be the first third-party
library in the app, which D9 and Mark's instruction ("no third-party SDKs beyond
AndroidX/Kotlin") rule out. Redirects are not followed: the token goes to the configured
host and nowhere else. Timeouts: 10 s to connect, 180 s to read (the bridge gives a turn
120 s). Revisit when the drive protocol needs server-sent events.

**A3. Record 16 kHz mono 16-bit PCM with `AudioRecord`, send it as WAV.** `bridge/stt.py`
hands the bytes to faster-whisper, which decodes through PyAV, so AAC in an MP4 container
would also work; but the encoder then differs per phone, and Whisper resamples to 16 kHz
mono anyway. Raw PCM with a 44-byte header has nothing to guess, and it is the format the
bridge was tested with. Size: 32 kB per second, so the cap of 120 seconds is 3.8 MB, far
under the bridge's 10 MB. The audio source is `VOICE_RECOGNITION`. The recording is held in
memory and never written to storage. A recording under 0.3 s is not sent.
Alternative considered: `MediaRecorder` with AAC, smaller over a mobile link. Kept for
later if upload time over mobile data turns out to matter; measure first.

**A4. One button, two ways to use it.** Press and hold, let go to send; or a short tap
(under half a second) starts recording and the next tap sends. One control covers both
without a setting. While the bridge works the button does nothing; while the reply plays a
tap stops it. When the app leaves the screen a recording in progress is dropped, never sent
later.

**A5. The token: an AES-256-GCM key in the Android Keystore; only ciphertext on disk.**
The key cannot be exported. The ciphertext is in the app's private preferences. The token
is decrypted for one request and not kept in the app's state; the settings screen never
shows it or puts it back in the field; a typed token is not written into saved instance
state. Backup and device transfer are switched off for the whole app. The app has no log
statements, the request object prints without its headers, and error messages are fixed
sentences that never include exception text. The token must be visible ASCII without
spaces, so it cannot inject a header.
Alternative considered: `EncryptedSharedPreferences` (androidx.security-crypto), named in
the assignment. Rejected: the library is deprecated, and it brings Google's Tink, a
third-party crypto library at runtime. Sixty lines against the platform Keystore do the
same without it.

**A6. Plain HTTP only for `*.ts.net`, and only to a Tailscale address.** Three layers:
1. The settings refuse an `http://` address whose host is not under `.ts.net`. `https://`
   is accepted for any host (for a bridge behind `tailscale serve`).
2. The network security config switches cleartext off for the whole app and on for
   `ts.net` and its subdomains, so the platform enforces the same rule under the code.
3. Before a plain-HTTP request the client resolves the name and sends nothing unless every
   address is in 100.64.0.0/10 or fd7a:115c:a1e0::/48. A `*.ts.net` name answered by a
   hostile Wi-Fi's DNS while Tailscale is off would otherwise receive the token and the
   recording in the clear.
The assignment asked for 100.64.0.0/10 in the network security config. That file takes
domain names only, no address ranges, so a bare `http://100.x.y.z` address is not accepted;
the MagicDNS name is. Layer 3 is where the range is enforced.

**A7. Errors are sentences.** 401: the bridge refused the token, check it in Settings. 409:
this project is busy, try again in a moment. 413: too large. 400: the bridge's own first
line (for example an unknown project), cut to 160 characters. 502: the agent turn failed.
A name that does not resolve, a refused connection, no route: is Tailscale on? A timeout
says so. Each failure also plays a short error tone, since the driver's eyes may be
elsewhere.

**A8. Waiting is audible.** One acknowledgement beep when the recording has gone out, then
a soft tick every three seconds until the reply or the error. Produced on the phone with
the platform's tone generator: no sound files, no connection needed. These are not the six
tones of `add-drive-mode` (D14); that set replaces them when it is built.

**A9. Dependencies are checked by the build.** `:app:checkSovereignDependencies` resolves
the debug and release runtime classpaths and fails on any module outside `androidx.*`,
`org.jetbrains.kotlin` and `org.jetbrains.kotlinx`, with three named exceptions that
AndroidX itself pulls in and that contain only annotations or one interface
(`org.jetbrains:annotations`, `org.jspecify:jspecify`, `com.google.guava:listenablefuture`).
It is part of `check`, so CI runs it. The app asks for two permissions: internet and the
microphone. All versions are exact, in `gradle/libs.versions.toml`; the Gradle distribution
and the wrapper are pinned by checksum.

**A10. Distribution: an APK from CI, sideloaded.** No Play Store (open question 5 of
`add-drive-mode`). Until Mark has created a signing key as GitHub secrets, the APK is
debug-signed with a key made on the runner. The project names are typed in the settings;
nothing of Mark's setup (host name, project names) is in the app or the docs.

## Risks / Trade-offs

- **Not run on a phone yet.** The build host has no SDK and no device. CI proves it
  compiles, passes lint and the `core` tests; the microphone, playback, Keystore and
  screens are untested until Mark installs it (task 5.1). The first install may show
  problems no test here could.
- **The address check of A6 is a check, not a pin.** `HttpURLConnection` resolves the name
  again when it connects, so an attacker who controls DNS and answers differently within
  the same second could pass. Closing that needs a client that connects to the checked
  address itself. Also, 100.64.0.0/10 is carrier-grade NAT space: a mobile operator's own
  network can use it. Both need Tailscale to be off and a hostile resolver at the same
  time. For the security review.
- **`targetSdk 37` and local-network rules.** Recent Android versions restrict apps from
  reaching local networks without a permission. A VPN such as Tailscale should be exempt;
  not verified on the phone. If the app cannot connect while a browser on the same phone
  can, this is the first thing to check.
- **Debug signing.** Each CI build has another signature; see open question 1.
- **Distraction.** The app has a screen and a summary to read. It is for a parked car, and
  the how-to says so.
- **Audio focus is not requested**, so a reply plays over music instead of pausing it.

## Migration

None. The bridge is unchanged and the HTTP Shortcuts setup keeps working beside the app.

## Open Questions

For Mark: see `proposal.md`. For the next build rounds:
- Does the reply route to the car's speakers over Bluetooth as it is, and is the phone's
  microphone the right one in the car? (Shared with phase 6 of `add-drive-mode`.)
- Is a WAV upload fast enough over mobile data, or is AAC needed (A3)?
