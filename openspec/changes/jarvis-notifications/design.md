# Design: jarvis-notifications

## Context

- The bridge answers requests; it never starts a conversation. The phone app
  (`add-android-client`) is one screen that talks to one host, the bridge, with three
  permissions and no third-party library (its decisions A2, A6, A9).
- The rules that bind this change: reachable only over the tailnet plus a token; sovereign
  by default, so no Google Play services and no Firebase (D9, A9); nothing of Mark's own
  setup in the repository; spoken and shown text is a short summary, never code, paths or
  secrets.
- Android only wakes an app for a push message through Firebase Cloud Messaging. Every road
  without Google means that the app itself holds a connection, in a foreground service.
  That is the real cost of this change, whichever server is on the other end.
- The app targets API 37 and runs on Android 10 (API 29) and newer. The foreground-service
  rules of Android 12 to 15 all apply.
- `add-drive-mode` (#9, not merged) has the idea of an *arrival* (a tone; "what's new"
  plays it) and a rule: Jarvis speaks only as the answer to an utterance. Its `POST
  /arrivals` is dropped by `jarvis-full-agent`. So today nothing carries a message from
  outside a turn to the phone.
- Decision numbers: N1 to N11, to stay clear of D1 to D40 and A1 to A10.

## Goals / Non-Goals

Goals:
- Mark hears within about a minute that a build finished, a question waits, a reply is
  ready or a confirmation is pending, while the phone is on the tailnet.
- No Google dependency, nothing reachable from outside the tailnet.
- A locked phone shows nothing a sender wrote, whatever its lock-screen setting is.
- A cost in battery that is measured, not assumed.

Non-Goals:
- Delivery when the phone is off the tailnet. It catches up on reconnect.
- Acting from a notification. A tap opens the app; nothing else.
- Guaranteed delivery. A notification is a hint; the build system, the issue and the daily
  mail stay the record.

## Decisions

**N1. Transport: a self-hosted ntfy server as the broker; not Firebase.**

| | Firebase Cloud Messaging | ntfy in the cluster | The bridge's own stream |
|---|---|---|---|
| Google dependency | Play services on the phone, a Firebase project, the message passes Google | none | none |
| Reachable from | the internet | the tailnet only | the tailnet only |
| Phone battery | best: the system's one shared connection | one more idle connection | one more idle connection |
| New parts | a Google account binding, a library A9 forbids | one container, one tailnet Service | queue, resume and stream code in the bridge |
| What the phone's credential can do | n/a | read one topic | run agent turns |

Firebase is rejected: it breaks D9 and A9, and the content or at least the timing of every
notification would pass through Google. It is the only option that costs no battery, and
we say so.

Between the other two, ntfy wins on least privilege. The listener runs all day; with ntfy
it holds a token that can read one topic and nothing else, and it talks to a server that
can do nothing but pass messages. With the bridge's own stream the all-day service would
hold the token that starts agent turns, and the long-lived connections would land on the
one process that is the road to a full agent (see #15 on its connection handling). ntfy
also brings a message cache with resume (`since=<id>`), per-topic access rules and a
protocol that has been running on phones for years.
The price: one more deployment and a second address and token in the app's settings.

**N2. Senders talk to the bridge, not to the broker.** `POST /notify`, JSON, at most 4 KB:

    {"v":1,"kind":"build","outcome":"ok","project":"<name>","text":"<one or two sentences>","link":"https://…"}

- `kind` is one of `build`, `question`, `reply`, `confirmation`. `outcome` (`ok`, `failed`)
  is allowed for `build` only. An unknown field, kind or version is a 400.
- **Kinds per sender.** A sender token may post `build` and `question` only. `reply` and
  `confirmation` say "Jarvis did something" and "Jarvis waits for your yes"; they are
  created inside the bridge, by the code that ran the turn, and are refused (403) from
  every sender token. Otherwise a leaked build-system token could put "A confirmation is
  waiting" on Mark's phone at a moment of its choosing.
- `project` must be a name the bridge knows (the same check as `/turn`), or absent.
- `text` passes the spoken-output filter (no code, no paths, nothing shaped like a secret)
  and is cut to 300 characters. A text that is empty after the filter is replaced by the
  kind's fixed sentence; the notification is still sent.
- `link` is optional, must be `https://` and its host must be on a configured list (default:
  `github.com`). Otherwise it is dropped, not the notification.
- Authentication: a sender token from a file of `name = token` lines, mounted as a Secret.
  A sender token is accepted on `/notify` only; the phone's token is not accepted there
  (the phone has no reason to notify itself), and a sender token is refused on `/turn` and
  `/projects`. One token per sender, so one can be withdrawn. The log names the sender.
- A limit per sender (default 20 per hour, a burst of 5): over it the answer is 429. A
  sender in a loop must not empty the phone's battery or teach Mark to ignore the sound.
- The bridge then publishes to the broker and answers 202; when the broker cannot be
  reached it answers 503 and keeps nothing. The sender may try again. The bridge has no
  queue of its own: the broker is the queue.

Why not let senders publish to ntfy directly: the filter and the closed list of kinds would
then have to be trusted to every sender's script. Through the bridge there is one place
where text is cleaned, and only one writer on the topic.
Cost: when the bridge is down, nothing can be sent. Accepted; see Non-Goals.

**N3. The broker's configuration is part of the design.** ntfy runs in the cluster with:
- `auth-default-access: deny-all`; two users: the bridge may write the one topic, the phone
  may read it. Nothing else exists;
- no `firebase-key-file`, no `upstream-base-url`, no web push keys, no SMTP, no attachments:
  with these empty the server contacts nobody;
- the message cache at 24 hours, on a small volume;
- a tailnet-only Service like the bridge's (no Ingress, no LoadBalancer with a public
  address), and a NetworkPolicy: in from the tailnet proxy and from the bridge, out to
  nothing;
- the image pinned by digest.
The manifests live in the cluster's own repository; the how-to here gives these settings
and no host names.

**N4. The app is the subscriber.** A foreground service reads
`GET /<topic>/json?since=<last id>` with the platform's `HttpURLConnection`: one JSON
object per line, a keepalive line from the server about every 45 seconds. The parser is in
`core` and tested against recorded lines. The read timeout is two keepalive intervals; on
a timeout or an error the service reconnects with a backoff from 5 seconds to 5 minutes
and asks for everything since the last id it showed. An id is shown once.
The connection follows A6 unchanged: plain HTTP only for a `ts.net` name, bound to the
Tailscale network, every resolved address checked, no proxy; refused when Tailscale is off.
The read token is stored like the bridge token (A5).
Alternatives considered:
- *The stock ntfy app.* No code to write, and the F-Droid build has no Firebase. Rejected
  as the design: a third-party app holds the token (the objection that led away from HTTP
  Shortcuts), the lock-screen rule of N6 cannot be enforced in it, and Jarvis could never
  speak what it received. Offered to Mark as a stopgap (open question 3).
- *UnifiedPush* (the Jarvis app registers with a distributor app). Rejected: it still needs
  the ntfy app as distributor, and adds a library.

**N5. The foreground service, by Android's rules.**
- *Type.* From Android 14 a foreground service must declare a type. `specialUse`, with the
  manifest property that states the purpose ("keeps a connection to the user's own server
  to receive notifications"). Not `dataSync`: from Android 15 it is stopped after six hours
  in a day and may not be started at boot. `remoteMessaging` was considered: it has no time
  limit either, but Android defines it for carrying text messages between a user's devices,
  which this is not. The app is sideloaded, so no store reviews the choice; we still pick
  the type that says what the service does.
- *Start.* Only by Mark, with a switch in the settings, while the app is on screen (Android
  12 and later refuse most starts from the background). After a reboot and after an app
  update a receiver starts it again if the switch was on; `specialUse` may be started from
  the boot broadcast. The service is sticky.
- *Permissions.* `POST_NOTIFICATIONS` (asked when the switch is turned on; refused means the
  switch goes back off and the app says why), `FOREGROUND_SERVICE`,
  `FOREGROUND_SERVICE_SPECIAL_USE`, `RECEIVE_BOOT_COMPLETED`. No wake lock, no exact alarm,
  no request to be exempt from battery optimisation: if phase 4 shows late delivery in
  Doze, the how-to tells Mark where to exempt the app by hand.
- *The permanent notification* is on its own channel with the lowest importance: "Jarvis is
  listening for notifications", or "not connected: is Tailscale on?". Since Android 13 the
  user can swipe it away; the service keeps running.
- Default: the switch is off. Someone who installs the app and never wants notifications
  pays nothing.

**N6. The notification itself holds one fixed sentence, and nothing else.** Chosen by kind
and outcome:

| Kind | The whole notification |
|---|---|
| build, ok | A build finished |
| build, failed | A build failed |
| question | A question is waiting |
| reply | Jarvis has a reply |
| confirmation | A confirmation is waiting |

The project, the sender's text and the link are **never put into the notification**: not
as its title, its text, an expanded view or the ticker. They are shown inside the app, in
a list that opens on a tap. The app's screen is never shown over the lock screen, so
reading them takes an unlocked phone.
Why not "private visibility with a public version", which Android offers for exactly this
and which the first draft of this design used: Android shows the public version only when
the phone is set to hide sensitive content on the lock screen. With the phone set to "show
all notification content", which is a common setting, the full notification is on the lock
screen. The rule would then depend on a system setting, and it must not. A notification
that does not contain the text cannot show it, on any setting, on the lock screen or in the
shade of an unlocked phone that someone else is looking at.
The notification is still posted with private visibility (and a public version that is the
same sentence), so nothing changes if a later Android reads those fields differently. A
setting in the app, "hide notifications on the lock screen", off by default, posts them
with secret visibility instead: then a locked phone shows nothing at all.
The list in the app is filled from the broker (`since=`, the last day); the app writes no
notification text to the phone's storage. The read-back of a pending confirmation is not in
the list either: it is shown on the confirmation screen, where the confirmation is given.
Notifications are local only (not bridged to a watch or a car display) and carry no action
buttons. A tap opens the app; a link is opened from there.

**N7. One Android channel per kind,** plus the service's own. Mark sets sound, vibration
and "do not disturb" per kind in the system settings; the app has no settings of its own
for that. Defaults: `question` and `confirmation` make a sound, `build` and `reply` are
silent in the shade.

**N8. A notification is data.** Its text is never passed to the agent as a prompt and never
changes what a turn may do. It comes from senders that are less trusted than Mark's voice.

**N9. Speaking (phase 5, after #9).** In drive mode a received notification becomes an
arrival in #9's sense: the arrival tone sounds, and "what's new" speaks the waiting ones,
oldest first. The words are rendered by the bridge's own TTS, not by the phone's speech
engine (which on most phones is Google's). Outside drive mode nothing is spoken. Whether
Jarvis may speak an arrival unasked is open question 2; the default follows #9's rule.
The details of this phase are written when #9's protocol is decided; this change only
fixes the two rules above.

**N10. Senders are configuration, not code.** The repository has the route and one example
(`curl` with a token file). Which systems send, and with what words, is set up outside it:
for Mark the build system and the collector of his open questions. Jarvis's own two kinds
(`reply`, `confirmation`) are sent by the bridge itself when background turns exist, and
by nothing else (N2).

**N11. The confirmation ping of D36 is not moved here.** Its job is to be a second,
independent witness of a confirmed action. If it travelled through the bridge, whoever
controls the bridge could confirm an action and drop the ping. It stays on the notify
command the agent host configures.

## Risks / Trade-offs

- **Battery is unknown until measured.** Mitigation: off by default, measured in phase 4
  (24 hours on, 24 hours off, same phone, same week), the number in the how-to, and open
  question 5 says what happens when it is high.
- **Doze can delay delivery.** A foreground service normally keeps its network in Doze, but the
  system may still batch wake-ups. A minute late is fine; phase 4 records what was seen.
- **A phone vendor's battery manager kills the service.** Known from other apps. The how-to
  names the setting; the permanent notification going away is the visible sign.
- **Tailscale off means silence.** The permanent notification says so. The daily mail still
  carries what waits for Mark.
- **A noisy sender.** The per-sender limit, and the channels let Mark silence one kind.
- **A sender token leaks.** Its holder can make "A build finished" or "A question is
  waiting" appear, with filtered text behind it in the app, up to the limit. It cannot
  claim a reply or a confirmation, start a turn or read anything. Withdraw that one token.
- **The read token leaks.** Its holder can read the filtered notifications from inside the
  tailnet. Another reason for the filter and for short texts.
- **ntfy is one more thing to keep patched.** Pinned by digest, updated by the cluster's
  own update routine; it has no road out and one road in.

## Migration

Nothing changes until the broker address is configured on the bridge and the switch is on
in the app. `POST /notify` answers 404 while no broker is configured.

## Open Questions

For Mark: the five in `proposal.md`, each with a default. For the build: whether the
keepalive interval of the pinned ntfy version is the 45 seconds assumed in N4 (the read
timeout follows what the server really sends).
