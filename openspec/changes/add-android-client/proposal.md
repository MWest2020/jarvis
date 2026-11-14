# Change: add-android-client

## Why

The only phone client today is a hand-built HTTP Shortcuts shortcut
(`docs/how-to/android-http-shortcuts.md`): the token sits in a third-party app's shortcut,
the wait of about 17 seconds is silent, an error shows as a status code, and changing project
means editing a URL. The roadmap has had "a native phone client" as its third phase since
the start.

Mark, 2026-11-15: "whats the status on the jarvis android app? i will test http shortcuts,
but work also on the app."

The open proposal `add-drive-mode` (PR #9) plans a native app as its last phase, on a new
protocol that does not exist yet and behind parked tests only Mark can do. This change does
not wait for that: it is a small app for the bridge as it runs today (`POST /turn`), and it
is the base that phase can later build on.

## What Changes

- **A native Android app in `android/`** (Kotlin, Jetpack Compose, Android 10 and newer):
  one screen with a large talk button (hold, or tap to start and tap to send), a project
  picker, and the summary of the last reply as text. It records 16 kHz mono WAV, posts it to
  `/turn`, beeps while the bridge works, and plays the spoken reply.
- **Settings** for the bridge address, the token and the project names. The token is
  encrypted with a key in the Android Keystore, never shown again, never logged, and not
  part of any backup.
- **Errors in words**: a refused token says "check the token", a busy project says so, an
  unreachable bridge asks whether Tailscale is on.
- **Plain HTTP only on the tailnet**: allowed for `*.ts.net` names only, and only when the
  name resolves to a Tailscale address. Everything else needs HTTPS.
- **Sovereignty (D9) enforced by the build**: only AndroidX, Kotlin and kotlinx at runtime.
  No Google Play services, no Firebase, no analytics, no HTTP or crypto library. The build
  fails when anything else appears.
- **CI** (`.github/workflows/android.yml`): tests, lint, the dependency check, and a
  debug-signed APK as an artifact. Sideloaded; no Play Store.
- **Docs**: `docs/how-to/android-app.md`.

Not in this change: anything of the drive protocol of `add-drive-mode` (the six tones, the
event stream, long replies in parts, control words), the screen-off service and the wheel
button, car Bluetooth routing, pairing by QR code, a release signing key, and any change to
the bridge. The HTTP Shortcuts guide stays; it is still the client for the open phone test.

## The code is in the same pull request

AGENTS.md says no code before a change is approved. Mark asked for the app to be worked on
now, so the first version comes with the proposal: merging this pull request approves both.
It touches nothing the bridge promises; the living spec `voice-bridge` is unchanged and a
new capability `android-client` is added.

## Impact

- New: `android/` (Gradle project, modules `core` and `app`), `.github/workflows/android.yml`,
  `docs/how-to/android-app.md`, capability `android-client`.
- Changed: `android/` added to the docs gate's code paths; `docs/index.md`, the how-to
  index, the roadmap.
- The bridge, its image and its tests: untouched.
- `add-drive-mode` (PR #9): its phase 6 task 6.2 names `client/android/`. If both are
  approved, that phase extends the app in `android/` instead of starting a second one
  (open question 3).

## Open questions for Mark

Each has a default. If you merge this without answering, the default is what happens.

1. **The signing key.** A sideloaded app can only be updated in place by an APK signed with
   the same key. A release key must not be made by an agent or live in the repository: you
   create it once and store it as GitHub secrets (`ANDROID_KEYSTORE_BASE64`,
   `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`; the how-to has
   the two commands).
   *Default: until those secrets exist, CI builds a debug-signed APK. Its key is made on the
   runner and differs per build, so installing a newer build means uninstalling the old one
   and entering the settings again. Wiring the release build to the secrets is task 4.3 and
   stays open until you say the secrets are there.*
2. **The application id** is `io.github.mwest2020.jarvis`. Android treats another id as
   another app, so changing it later means a fresh install.
   *Default: keep it.*
3. **Where the app lives:** `android/` here, `client/android/` in `add-drive-mode`'s tasks.
   *Default: `android/`; the drive-mode task is adjusted when that phase starts.*
4. **Project names are typed into the settings**, because the bridge has no route that lists
   them. A list route would be a change to the bridge (`voice-bridge`).
   *Default: typed for now; a list route is proposed separately if the typing annoys.*
5. **Pull-request builds also upload their APK** (kept seven days), so a reviewer can try
   the build under review; the task asked for main only.
   *Default: keep both.*
