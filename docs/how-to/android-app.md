---
status: current
last_reviewed: 2026-11-15
---
# The Android app: install, set up, use

A small native client for the bridge's `POST /turn`: one large button, a project picker,
the summary of the last reply as text. It replaces the hand-built shortcut of
[`android-http-shortcuts.md`](android-http-shortcuts.md). Why and the decisions: the change
[`add-android-client`](../../openspec/changes/add-android-client/design.md). The code is in
[`android/`](../../android/).

**For a parked car, not for driving.** The app has a screen to look at and a button to hit.
Hands-free use while driving is what the change `add-drive-mode` is for.

**Status on 2026-11-15:** it builds, passes lint and its unit tests in CI. It has not run on
a phone yet; the first install is the test (tasks 5.1 to 5.3 of the change). Write down
what happens under "Test record" below.

## What you need

- An Android phone with Android 10 or newer, on the same tailnet as the bridge, with
  Tailscale switched on.
- The bridge's address as a Tailscale name, `http://<name>.<tailnet>.ts.net:8787`
  ([`run-in-cluster.md`](run-in-cluster.md) or [`bridge-service.md`](bridge-service.md)),
  its token (`JARVIS_TOKEN`), and the project names from `JARVIS_PROJECTS`.

## Install (sideload)

The app is not in any store. CI builds the APK.

1. On GitHub: the repository → Actions → workflow **android** → the newest green run on
   `main` → Artifacts → `jarvis-android-debug-apk`. With the CLI:

   ```sh
   gh run download -R <owner>/jarvis <run-id> -n jarvis-android-debug-apk
   ```

   The artifact is a zip with one file, `jarvis-debug-<run>-<commit>.apk`. The run's summary
   shows its SHA-256; compare it if the file travels over anything you do not trust.
2. Get the APK onto the phone: download it there in a browser that is logged in to GitHub,
   or copy it over USB, or over the tailnet (`tailscale file cp`).
3. Open the APK on the phone. Android asks once to allow installing from that source (the
   browser or the file manager); allow it, install, and switch the permission off again if
   you like.
4. Play Protect may warn about an unknown developer. That is expected for a sideloaded,
   debug-signed app.

### Updating

Until there is a release signing key, every CI build is signed with a debug key made on the
runner, and each has a different one. Android refuses to install an APK over an app signed
with another key, so for now an update is: uninstall Jarvis, install the new APK, enter the
settings again. A release key fixes this; see "The signing key" below.

## First run

1. Open Jarvis. With nothing configured it opens the settings.
2. **Bridge address:** `http://<name>.<tailnet>.ts.net:8787`, no `/turn` at the end.
3. **Token:** the bridge's `JARVIS_TOKEN`. It is stored encrypted with a key in the phone's
   Keystore and is not shown again; to change it, type a new one. "Forget the stored token"
   removes it.
4. **Projects:** the names the bridge knows, separated by commas. The bridge has no route
   that lists them, so they are typed once.
5. Save. On the main screen pick a project.
6. Press the button. The first time, Android asks for the microphone; allow it and press
   again.

## Using it

- **Hold** the button, talk, let go. Or **tap** it, talk, tap again. Both send.
- A beep confirms the recording has gone out; a soft tick every three seconds means the
  bridge is working. A turn takes about 20 seconds.
- The reply plays by itself and its summary appears as text. A tap while it plays stops it.
- A recording stops by itself after two minutes. Leaving the app while recording discards
  the recording.
- The screen stays on while the app is in front.

## What the errors mean

| The app says | Cause | Do |
|---|---|---|
| The bridge refused the token | 401: the token differs from `JARVIS_TOKEN` | Enter the token again in Settings |
| This project is busy with another turn | 409: a turn for this project is still running | Wait for it, try again |
| Tailscale is off. Nothing was sent | The address is plain `http://` and the phone has no Tailscale VPN network up | Switch Tailscale on |
| The bridge's name does not resolve / cannot be reached. Is Tailscale on? | The name is unknown on the tailnet, or the bridge is down | Check the address and the bridge |
| The bridge's name does not point at an address that Tailscale routes. Nothing was sent | The name resolved to something outside the tunnel | Check the address; check the tailnet's DNS |
| The connection to the bridge broke. Try again; if it keeps happening, check the token | The connection was closed mid-turn. A bridge that refuses the token can do this before the app has read its answer | Try again; then enter the token again |
| No answer from the bridge in time | No reply within three minutes | Check the bridge's log |
| The bridge did not accept the request: … | 400, with the bridge's own reason, e.g. an unknown project | Fix the project names in Settings |
| The recording is too large | 413 | Ask a shorter question |
| The agent turn failed on the bridge | 502 | Try again; details are in the bridge's log |
| Plain http is only allowed for a Tailscale name | The address is `http://` to something that is not `*.ts.net` | Use the Tailscale name, or https |

## Why plain HTTP, and why only there

The bridge does not do TLS. It listens on the tailnet only, where WireGuard encrypts
everything between the phone and the machine, and it wants a token on top
([`run-in-cluster.md`](run-in-cluster.md), "Listening behind the Tailscale proxy"). So the
app allows plain HTTP in exactly that case:

- the settings accept `http://` only for a `*.ts.net` name;
- Android itself blocks cleartext for the app everywhere else
  (`android/app/src/main/res/xml/network_security_config.xml`);
- a plain-HTTP turn is sent on a connection bound to the Tailscale VPN network, with the
  name looked up through that network, and only to Tailscale addresses
  (100.64.0.0/10 or fd7a:115c:a1e0::/48) that the network routes. With Tailscale off there
  is no such network and nothing is sent. An address in the right range is not enough:
  on a hostile Wi-Fi the resolver and the gateway can both pretend;
- no proxy is ever used, so a proxy configured on the phone does not see the token.

The token is sent to the address you enter, on every turn. Enter the address of your own
bridge and nothing else.

A bare `http://100.x.y.z` address is not accepted: Android's network security config takes
names, not address ranges. Use the Tailscale name. `https://` works for any host.

## How it differs from HTTP Shortcuts

| | HTTP Shortcuts | The app |
|---|---|---|
| Setup | Build a shortcut by hand: method, URL, header, record and play actions | Three fields |
| The token | In the shortcut, inside a third-party app; exported with the shortcut | Encrypted with a Keystore key, never shown again, not in any backup |
| Switching project | One shortcut per project, or edit the URL | Tap the project |
| While waiting | Silence | A beep, then a tick every three seconds |
| When it fails | A status code, or nothing | A sentence saying what to do |
| The summary | Not shown | On the screen |
| Recording format | Whatever the recorder app produces | 16 kHz mono WAV, the format the bridge was tested with |
| Other hosts | The app can call anything | Only the configured bridge; cleartext only on the tailnet |

The HTTP Shortcuts guide stays valid: both use the same `POST /turn`.

## What the app does not do

- Nothing with the screen off, no steering-wheel or headset button, no Bluetooth routing
  choices, no long replies in parts: all of that is `add-drive-mode`.
- No Google Play services, Firebase, analytics, or any library besides AndroidX and Kotlin;
  the build fails if one is added. Three permissions: internet, microphone, and network
  state (to find the Tailscale network); CI fails the build on any other.
- No log output at all.

## The signing key (Mark, once)

So that updates install over the old version, CI needs a key that stays the same. Create it
on a machine you trust, not on a runner and not in the repository:

```sh
keytool -genkeypair -v -keystore jarvis-release.jks -alias jarvis \
  -keyalg RSA -keysize 4096 -validity 10000
base64 -w0 jarvis-release.jks      # the value for ANDROID_KEYSTORE_BASE64
```

Then, in the repository's Settings → Secrets and variables → Actions, add
`ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS` (`jarvis`) and
`ANDROID_KEY_PASSWORD`. Keep the `.jks` file and the passwords somewhere safe: losing them
means a fresh install on every phone. CI does not use these secrets yet; wiring the release
build to them is task 4.3 of the change.

## Building and testing

The app is built in CI (`.github/workflows/android.yml`): on every pull request that
touches `android/`, and on `main`.

```sh
cd android
./gradlew :core:test                 # request, errors, WAV, address check; no Android SDK needed
./gradlew check :app:assembleDebug   # lint, the dependency check, the APK; needs the Android SDK
```

- `core/` is plain Kotlin and holds everything that can be tested without a phone. Its
  tests talk to a real HTTP server on loopback that behaves like the bridge.
- `app/` is the Android part: microphone, playback, Keystore, screens.
- Versions are exact, in `android/gradle/libs.versions.toml`. The Gradle wrapper is pinned
  by checksum.
- `:app:checkSovereignDependencies` fails the build on any runtime dependency outside
  AndroidX, Kotlin and kotlinx.

## Test record

Nothing yet. After the first install, note here: the phone and Android version, whether a
turn and a follow-up worked, the timings, and anything that was unclear or failed.
