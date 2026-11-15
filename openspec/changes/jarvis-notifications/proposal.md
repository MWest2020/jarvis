# Change: jarvis-notifications

## Why

Mark, 2026-11-15: "in the future i would want a jarvis notification as well" (#21).

Today Jarvis only speaks when spoken to. A build that finished, a question that waits for
Mark, a reply to something he asked an hour ago: none of these reach the phone unless he
asks. This change lets Jarvis tell him, on the phone, that something needs him.

## What Changes

- **A way in: `POST /notify` on the bridge.** A sender (the build system, the job that
  collects open questions, Jarvis itself) posts one small JSON object: a kind from a closed
  list (`build`, `question`, `reply`, `confirmation`), a project, one short text and
  optionally a link. Each sender has its own token, which can do nothing but notify. The
  bridge applies the filter it already applies to speech (no code, no paths, nothing shaped
  like a secret), caps the length, and refuses everything else.
- **A broker: ntfy, self-hosted in the cluster, tailnet-only.** The bridge publishes to one
  ntfy topic; the phone reads it. ntfy keeps messages for a day, so a phone that was off the
  tailnet gets what it missed. No Firebase, no Google service, no ntfy.sh: the server runs
  with its Firebase and upstream settings empty, and nothing leaves the tailnet.
- **The app listens.** A foreground service in the Jarvis app holds one connection to the
  broker over the Tailscale tunnel, under the same network rules as a voice turn, with a
  token that can only read that one topic. No second app on the phone, no new library: the
  platform's HTTP client reads ntfy's line-per-message stream.
- **The lock screen shows fixed words only.** "A build finished", "A question is waiting",
  "A confirmation is waiting", "Jarvis has a reply". The text a sender wrote is shown only
  after the phone is unlocked, and it has passed the bridge's filter before that. No
  notification has a button that does something: a tap opens the app.
- **Speaking** a notification is the last phase and waits for `add-drive-mode` (#9): in
  drive mode a notification is an *arrival*, announced by a tone and spoken when Mark asks
  "what's new". Jarvis does not start talking by itself (default; open question 2).
- **The confirmation ping of `jarvis-full-agent` stays what it is.** That ping (design D36,
  step 7) exists to show Mark, outside Jarvis, an action he did not confirm. It must not
  depend on the bridge it is watching, so it keeps its own channel. A Jarvis notification
  may come next to it, never instead of it.

Not in this change: confirming anything from a notification (that is `jarvis-biometric-confirm`,
inside the app, after unlocking); notifications on a watch or in the car's display (they
are kept on the phone); iOS; a web push.

## What this costs, said plainly

- **Battery.** Without Google's push service the phone must hold a connection itself. That
  is one idle socket inside a tunnel that Tailscale already keeps up, and a permanent small
  notification. The ntfy project reports the cost of this pattern as small; we do not copy
  their number. Phase 4 measures it on Mark's phone over 24 hours and writes it down.
- **Only on the tailnet.** With Tailscale off on the phone nothing arrives; the permanent
  notification says "not connected", and the missed messages come in on reconnect (up to
  the broker's cache, one day by default).
- **Four more Android permissions** (three were all the app had): to show notifications,
  to run the service, and to start it again after a reboot. CI's permission check is
  widened to exactly these, and still fails on any other.
- **One more component in the cluster** (ntfy: one container, one small volume) and one
  more tailnet Service.

## Phases

| # | Phase | You can then |
|---|---|---|
| 1 | Bridge side | post a notification to the bridge and see it, filtered, on a fake broker in CI |
| 2 | Broker in the cluster | post to the real bridge and read the topic with `curl` from a tailnet machine |
| 3 | The app | get it on the phone, with fixed words on the lock screen |
| 4 | Senders and the phone test | hear about a finished build and a waiting question; the battery number is known |
| 5 | Spoken in drive mode (after #9) | hear the arrival tone and ask "what's new" |

Order: after `jarvis-full-agent` (#21's own default). Phases 1 to 4 do not need it; the
kinds `reply` and `confirmation` have no sender until background turns exist (#9).

## Impact

- Capability `voice-bridge`: one requirement added (Notifications in).
- Capability `android-client`: two requirements added (Notification listener, Lock screen
  content), one modified (Client sovereignty: the app may also contact the configured
  notification server, and nothing else). This change is archived after `add-android-client`.
- Code, when built: `bridge/` (the route, the filter reuse, the publisher), `android/`
  (service, channels, settings, the stream reader in `core`), `.github/workflows/android.yml`
  (the permission list). Docs: a new how-to, `android-app.md`, `bridge-service.md`,
  `run-in-cluster.md`.
- Outside this repository: the ntfy deployment and its tailnet Service in the cluster's own
  repository; one line in each sender (the build system, the question collector).
- `add-drive-mode` (#9): its `GET /drive/events` stream stays for what happens inside a
  drive. What arrives from outside a turn comes through this change; phase 5 turns a
  notification into #9's "arrival".

## Open questions

Each has a default. If the pull request is merged without an answer, the default is what
is built.

1. **May the project name be on the lock screen?** ("A build finished for wordsworth"
   instead of "A build finished".) It is not a secret, but it tells whoever picks up the
   phone what you work on.
   *Default: no. Kind and outcome only; the project is shown after unlocking.*
2. **Should Jarvis speak a notification by itself in drive mode,** or only sound a tone and
   wait for "what's new"? #9's design says Jarvis never talks at a moment the driver did
   not choose; #21 says "optionally speaks it".
   *Default: tone only, spoken when you ask. A setting "speak arrivals" exists, off.*
3. **Do you want notifications before the app part is built,** through the stock ntfy app
   from F-Droid reading the same topic? It works after phase 2. The price: a third-party
   app holds the read token, and what shows on the lock screen is then that app's and the
   phone's setting, not our rule (the text is still filtered by the bridge).
   *Default: no. Phase 2 is proven with `curl`; the phone waits for phase 3.*
4. **Which senders first?** *Default: the build system (finished, failed) and the collector
   of `waiting-on-mark` questions (one notification per new question, none for reminders).
   The daily digest mail stays as it is.*
5. **What battery cost is too much?** *Default: the number is measured and reported; at
   more than five percent of the battery per day the listener is off by default and you
   switch it on when you want it.*
