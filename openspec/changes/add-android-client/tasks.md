# Tasks: add-android-client

Tasks marked **(Mark)** need Mark's phone, ears or GitHub account. A build leaves them open.

## 1. Core (plain Kotlin, no SDK needed)
- [x] 1.1 Request building: the turn URL, the bearer header, the address rules of A6, the
  token rules of A5; the request prints without its headers
- [x] 1.2 Error mapping: statuses 400, 401, 404, 409, 413, 502 and others; unresolved name,
  refused connection, timeout, blocked cleartext; the summary header decoded (A7)
- [x] 1.3 The HTTP exchange with the platform client: no redirects, bounded reads, the
  Tailscale address check before plain HTTP (A2, A6)
- [x] 1.4 WAV header for 16 kHz mono PCM (A3); the hold-or-tap logic (A4)
- [x] 1.6 Review finding B1: the connection and the name lookup are injected, a route check
  per resolved address, no proxy; tests for each and for the "Tailscale is off" refusal (A6)
- [x] 1.5 Unit tests for all of the above, the exchange against a loopback HTTP server

## 2. App
- [x] 2.1 Gradle project in `android/`: version catalog with exact versions, wrapper pinned
  by checksum, minSdk 29, targetSdk 37, Compose
- [x] 2.2 Recorder (`AudioRecord`, in memory, 120 s cap), reply playback, the waiting and
  error tones (A3, A8)
- [x] 2.3 Token store on the Android Keystore; settings store; backup and transfer off (A5)
- [x] 2.4 Talk screen: project picker, the one button, summary and errors in large text
- [x] 2.5 Settings screen: address, token, project names, with the checks of 1.1
- [x] 2.6 Network security config: cleartext off, on for `ts.net` only (A6)
- [x] 2.7 `checkSovereignDependencies` as part of `check` (A9)
- [x] 2.8 Review finding B1: plain HTTP bound to the Tailscale VPN network, refused without
  one (`TailnetRoute.kt`, `ACCESS_NETWORK_STATE`) (A6)
- [x] 2.9 Review: reply played from memory with `prepareAsync`; Keystore and settings off
  the main thread; "token stored" only when it can still be decrypted

## 3. CI and docs
- [x] 3.1 `.github/workflows/android.yml`: core tests without the SDK, lint, dependency
  check, debug APK as an artifact; read-only token, actions pinned by commit
- [x] 3.2 `android/` in the docs gate's code paths
- [x] 3.4 CI prints the APK's permissions and components and fails on an Android permission
  other than the three of A9
- [x] 3.3 `docs/how-to/android-app.md`, the how-to index, `docs/index.md`, the roadmap

## 4. Signing
- [ ] 4.1 **(Mark)** Create the release key and the four GitHub secrets (open question 1)
- [ ] 4.2 **(Mark)** Say so on the `waiting-on-mark` issue
- [ ] 4.3 Release build signed from the secrets in CI; the APK attached to a release of the
  private repository; the how-to's "Updating" section rewritten

## 5. On the phone
- [ ] 5.1 **(Mark)** Install the APK, do the first-run setup, one spoken round trip and a
  follow-up in the same project; write down what happened in the how-to
- [ ] 5.2 **(Mark)** Try the errors: a wrong token, Tailscale off (must say "Tailscale is
  off" and send nothing), a second turn while one runs
- [ ] 5.3 **(Mark)** Parked in the car: is the reply audible over the car's speakers, is
  the button usable in the mount
- [ ] 5.4 Fix what 5.1 to 5.3 show (through the factory)
- [ ] 5.5 Roadmap; archive the change
