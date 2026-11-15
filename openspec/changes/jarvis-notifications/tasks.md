# Tasks: jarvis-notifications

## Current phase: 0 (not started; waits for approval and for `jarvis-full-agent`)

One build does one phase. The last task of every phase moves the heading and updates
`docs/reference/roadmap.yml` (an item is done only with evidence). Every phase updates
`docs/` in the same pull request (AGENTS.md).

- **(cluster)**: a change in the cluster's own repository, done by the coding session.
- **(Mark)**: needs Mark's hands or phone. See the last section.

## 1. Bridge side

- [ ] 1.1 `POST /notify` in `bridge/`: version 1, the closed field and kind lists, the 4 KB
  cap, the project check, 400 for anything else (N2). 404 while no broker is configured
- [ ] 1.2 Sender tokens from a file of `name = token` lines; a sender token is refused on
  `/turn` and `/projects`, the phone's token on `/notify`; constant-time comparison; the
  log names the sender and never the token (N2)
- [ ] 1.3 The text through the spoken-output filter, the 300-character cut, the fixed
  sentence per kind and outcome when nothing is left; the link rule (HTTPS, allowed hosts)
- [ ] 1.4 The limit per sender (20 per hour, burst 5, configurable), 429 over it
- [ ] 1.5 The publisher: one HTTP request to the broker with the bridge's write token (from
  a file), a timeout, 202 on success, 503 when it fails, no retry and no queue
- [ ] 1.6 Tests against a fake broker on loopback: each scenario of "Notifications in",
  a request without a token, with a wrong one, an oversized body, a body that is not JSON,
  a text that is only a code block, a link to another host, the 21st notification in an hour
- [ ] 1.7 Docs: `docs/how-to/notifications.md` (the route, a `curl` example with a token
  file, the broker settings of N3 without host names), `bridge-service.md`; roadmap

## 2. Broker in the cluster

- [ ] 2.1 **(cluster)** ntfy with the settings of N3: deny by default, the two users, no
  Firebase, no upstream, no web push, no attachments, a 24-hour cache on a small volume,
  the image pinned by digest
- [ ] 2.2 **(cluster)** A tailnet-only Service and a NetworkPolicy (in: the tailnet proxy
  and the bridge; out: nothing); the bridge's own policy gets one egress rule to the broker
- [ ] 2.3 **(cluster)** The write token and the sender tokens as Secrets mounted as files
  in the bridge's pod
- [ ] 2.4 Verified and recorded in the how-to: a `curl` to `/notify` shows up on the topic
  read with `curl` from a tailnet machine; the broker cannot be reached from outside the
  tailnet; the broker's pod has no route out (a request to a public address fails)
- [ ] 2.5 `run-in-cluster.md`; roadmap

## 3. The app

- [ ] 3.1 `core`: the reader of the broker's stream (one JSON object per line, keepalive,
  message, anything else ignored), the last-id bookkeeping, the backoff; tests with recorded
  lines and a loopback server that drops the connection (N4)
- [ ] 3.2 Settings: the notification server's address (the same check as the bridge
  address), the read token (stored as in A5), the switch (off by default)
- [ ] 3.3 The foreground service: type `specialUse` with its manifest property, started
  from the switch, sticky, restarted by the boot and package-replaced receivers when the
  switch was on; the permanent notification on its own lowest-importance channel with the
  two states (N5)
- [ ] 3.4 The connection through `TailnetRoute` as a turn uses it (A6): refused when
  Tailscale is off, no proxy
- [ ] 3.5 One channel per kind (N7); private visibility with the public version of N6;
  local only; no actions; a tap opens the app; the project and text shown after unlock
- [ ] 3.6 `POST_NOTIFICATIONS` asked when the switch is turned on; refused means off, with
  a sentence
- [ ] 3.7 `.github/workflows/android.yml`: the permission check allows exactly the three of
  A9 plus `POST_NOTIFICATIONS`, `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_SPECIAL_USE` and
  `RECEIVE_BOOT_COMPLETED`; `checkSovereignDependencies` unchanged and green
- [ ] 3.8 Tests: the public version holds only the fixed sentence for every kind and
  outcome; a notification id shown once across a reconnect; an unknown kind is shown as
  nothing
- [ ] 3.9 Docs: `android-app.md` (switching it on, the permanent notification, where a
  vendor's battery manager is set, what the lock screen shows); roadmap

## 4. Senders and the phone test

- [ ] 4.1 **(cluster)** The build system sends `build` with `ok` or `failed` when a build
  ends (one line in that system's own repository)
- [ ] 4.2 **(cluster)** The collector of open questions sends `question` once per new
  question, not for reminders
- [ ] 4.3 **(Mark)** The phone test: a notification arrives with the phone locked; the lock
  screen shows the fixed sentence only; after an hour without Tailscale the missed ones
  come in; after a reboot it still works
- [ ] 4.4 **(Mark)** Battery: 24 hours with the listener on and 24 hours with it off, the
  system's own per-app figure for both, written into the how-to (open question 5)
- [ ] 4.5 Roadmap

## 5. Spoken in drive mode (after `add-drive-mode`)

- [ ] 5.1 Write this phase's design when #9's protocol is decided: a notification becomes
  an arrival; the tone; "what's new"; the words rendered by the bridge's TTS (N9)
- [ ] 5.2 The setting "speak arrivals", off (open question 2)
- [ ] 5.3 The bridge sends `reply` and `confirmation` itself when background turns exist
- [ ] 5.4 Roadmap; archive the change

## What needs Mark's hands

The five open questions in `proposal.md`, each with a default. Then: the read token and the
address typed into the app once (3.2), the phone test (4.3) and the battery day (4.4).
