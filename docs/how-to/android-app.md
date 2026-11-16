---
status: current
last_reviewed: 2026-11-16
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
  and its token (`JARVIS_TOKEN`). The project names are fetched from the bridge, or typed.

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
4. **Projects:** leave it empty (or type `*`) and the app asks the bridge for its projects
   (`GET /projects`): when the app starts, after you save the settings, and when you tap
   **Refresh** on the main screen. The last list is kept, so the picker is filled at once on
   the next start. Or type the names the bridge knows, separated by commas: then the app shows
   exactly those and never asks. A bridge older than 2026-11-15 has no list route; type the
   names for it.
5. **Default project (optional):** the name of the project the app selects while you have
   not picked one yourself. Leave it empty and the app selects General (below), or nothing
   when General is off.
6. **Home project (for General):** the project the **General** entry talks to, `jarvis-home`
   unless you change it. General is shown when the project list has it. Leave it empty to
   hide General.
7. Save. With `jarvis-home` in the list the main screen opens with General selected; pick a
   project to talk about that one instead (see "Which project is selected" below).
8. Press the button. The first time, Android asks for the microphone; allow it and press
   again.

### General: just talk

The first entry on the main screen is **General** (change
[`jarvis-general-conversation`](../../openspec/changes/jarvis-general-conversation/design.md),
D50). It is not a mode: it sends your turns to the home project, `jarvis-home` by default,
whose own `CLAUDE.md` makes Jarvis a sparring partner there. It is shown only when the
project list has the home project (from the bridge, or typed); an app that was set up
before, with a list without it, looks exactly as before. Think out loud, discuss an
idea; when it is worth keeping Jarvis writes it down in that repository. Say you want to
build it and Jarvis proposes a repository: you hear the read-back ("Create the private
repository birdsong. Say yes to go ahead."), say yes, and with the bridge in `full` mode
the agent host makes it a project at once: Jarvis ends with "It's ready. You can switch to
it.", the app fetches its list again by itself once the reply has played, and the new
project is there to tap. What you had selected stays selected. When setting it up takes
longer, Jarvis says "I'm still setting it up at the desk." and the agent host tries again
with your next questions; tap Refresh later. With project names typed in the settings, the
app tells the bridge so with each question (header `X-Jarvis-Projects: typed`) and Jarvis
says "It's ready. Add it to your project list in the app." instead.

The home project is shown once, as General, and not again under its own name.

### Which project is selected

A list of exactly one project is selected: there is nothing to choose (General, when it is
there, is that same project). With more than one, the app never takes the first of the list
by itself. In this order:

1. **The entry you tapped last** (General included). It is remembered across restarts and
   refreshes. If it is missing from the bridge's list for a while, it is selected again once
   it is back.
2. Otherwise the **Default project** from the settings, when the list has it (upper and lower
   case do not matter).
3. Otherwise **General**, when the list has the home project.
4. Otherwise **none**: the main screen says "Pick a project." and the button answers "Pick a
   project first." until you tap one.

The list from the bridge is shown alphabetically, upper and lower case together.
`GET /projects` gives names only, so there is no "most recently pushed first" yet; that needs
a date in the bridge's answer.

A version of the app from before 2026-11-16 selected the first project by itself and stored
that as if it were your pick. That stored value is ignored: after the update, pick your
project once.

## Using it

- **Hold** the button, talk, let go. Or **tap** it, talk, tap again. Both send.
- A beep confirms the recording has gone out; a soft tick every three seconds means the
  bridge is working. A turn takes about 20 seconds.
- The reply plays by itself and its summary appears as text. A tap while it plays stops it.
- The summary (and an error) can be selected: long-press it to copy a link or a command.
- When a reply says a new project is ready (the bridge's header
  `X-Jarvis-Projects-Changed: 1`), the app fetches the project list again by itself once the
  reply has played or you stopped it, if the list comes from the bridge (if a refresh is
  already running, one more follows). The selection stays where it was, and a message on
  screen from after the reply stays too.
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
| The bridge did not accept the request: … | 400, with the bridge's own reason, e.g. an unknown project | Fix the project names in Settings, or tap Refresh |
| The bridge does not offer a project list | 404 on `GET /projects`: the bridge is older than the list route | Update the bridge, or type the project names in Settings |
| The bridge's project list could not be read | The answer to `GET /projects` was not the expected JSON | Check the address; type the names meanwhile |
| The bridge lists no projects | The bridge has no project (with `JARVIS_PROJECTS=*`: no checkout under its root) | Check the bridge's projects |
| The recording is too large | 413 | Ask a shorter question |
| The bridge is busy with other connections | 503: the bridge already serves its maximum of eight connections | Wait a few seconds, try again |
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
  on a hostile Wi-Fi the resolver and the gateway can both pretend. "Routes" means the most
  specific matching route sends packets into the tunnel: on Android 13 and newer a range
  that the VPN app excluded from its tunnel does not count;
- no proxy is ever used, so a proxy configured on the phone does not see the token.

The app cannot tell Tailscale from another VPN. It takes the VPN network whose own address
is in 100.64.0.0/10 or fd7a:115c:a1e0::/48, and 100.64.0.0/10 is a shared range: another VPN
that gives the phone such an address (for example Cloudflare WARP Zero Trust) also
qualifies, and its DNS and its tunnel are then trusted the way Tailscale's are. Run only a
VPN you trust with the token.

The token is sent to the address you enter, on every turn. Enter the address of your own
bridge and nothing else.

A bare `http://100.x.y.z` address is not accepted: Android's network security config takes
names, not address ranges. Use the Tailscale name. `https://` works for any host.

## How it differs from HTTP Shortcuts

| | HTTP Shortcuts | The app |
|---|---|---|
| Setup | Build a shortcut by hand: method, URL, header, record and play actions | Three fields; the projects can come from the bridge |
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
- Nothing another app can call. In the APK as it ships, libraries included, only two
  components are exported: the launcher activity, and AndroidX's `ProfileInstallReceiver`,
  which only a holder of `android.permission.DUMP` (the adb shell, not an app) can reach.
  CI fails the build on any other exported component (`android/ci/check_exported.py`; to
  expose one on purpose, add it to the list there with the reason).
- No log output at all.

## The app icon

The icon is a voice waveform whose last bar bends into a J: mint bars, a white J, on deep navy. It is an adaptive icon made of three vector drawables in `android/app/src/main/res/drawable/`: `ic_launcher_background`, `ic_launcher_foreground` and `ic_launcher_monochrome` (for Android 13+ themed icons). The source drawing is `docs/assets/jarvis-logo.svg`. Change the SVG and the three drawables together, and keep the mark inside the 66 dp safe zone (21 to 87 in the 108 viewport).

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
- The workflow's step "Permissions and components in the APK" reads the merged manifest out
  of the built APK and fails on an extra permission or an extra exported component. The
  component check has its own tests, run with the bridge's: `uv run pytest
  tests/test_check_exported.py`.

## Test record

Nothing yet. After the first install, note here: the phone and Android version, whether a
turn and a follow-up worked, the timings, and anything that was unclear or failed.
