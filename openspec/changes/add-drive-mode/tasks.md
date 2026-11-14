# Tasks: add-drive-mode

## Current phase: 1 (Live turn)

One build does one phase: the phase named in this heading, and nothing from a later one.
The last task of every phase moves this heading to the next phase and updates
`docs/reference/roadmap.yml` (an item is done only with evidence). Every phase updates
`docs/` in the same pull request (AGENTS.md) and keeps `POST /turn` working.

Tasks marked **(Mark)** need Mark's own phone, car or ears. A build leaves them open and
says so; it does not tick them.

## 1. Live turn

- [ ] 1.1 Confirm the events of `claude -p --output-format stream-json` with partial
  messages against the installed CLI; record the ones the adapter relies on in
  `docs/reference/` with a captured sample used as a test fixture
- [ ] 1.2 Agent adapter interface (start turn, event stream, cancel) and the Claude Code
  adapter on it; a fake adapter for tests; the bridge core no longer imports Claude Code
  specifics (D22)
- [ ] 1.3 Speech provider interfaces (transcribe with language and vocabulary hints;
  synthesize) with the local faster-whisper and Kokoro providers on them; fake providers for
  tests; active providers logged at start (D22)
- [ ] 1.4 Reply script: the appended system prompt asks for the spoken parts first; parse
  them, enforce the part and word limits, filter each part on its own (D17)
- [ ] 1.5 `POST /drive/turn`: streamed events (`heard`, `working`, `speech`, `more`, `done`,
  `problem`), speech rendered per part and sent as soon as it is ready, a client turn id
  that is run once (D15, D16)
- [ ] 1.6 Stopping: a closed connection does not kill the turn; `POST /drive/cancel` does,
  by process group
- [ ] 1.7 Timings per turn in the log and in the `done` event (D16)
- [ ] 1.8 `POST /turn` returns the first spoken part and is otherwise unchanged; existing
  tests still pass
- [ ] 1.9 Docs: the protocol as a reference page; `bridge-service.md` updated; a `curl -N`
  walkthrough
- [ ] 1.10 Proof on the tailnet: a streamed turn with `curl -N` where the first `speech`
  event arrives before `done`, with the logged timings, written up in `docs/how-to/`
- [ ] 1.11 Roadmap: mark "approved by Mark" and the phase 1 item done with their evidence;
  move the phase heading

## 2. Reference client and parked test

- [ ] 2.1 The web client page, served by the bridge: one full-screen button, the six tones,
  end of speech by silence or a second press, playback queue, a press stops speech within
  200 ms (D13, D14, D23)
- [ ] 2.2 No external requests from the page; a content security policy that enforces it;
  the token taken from the pairing link's fragment and kept in browser storage
- [ ] 2.3 Offline and failure behaviour: the `problem` tone, one retry of an upload within
  10 s under the same turn id, never a silent failure
- [ ] 2.4 End-to-end test in CI with a fake microphone and the fake adapter and providers
- [ ] 2.5 How-to: reaching the page over Tailscale's HTTPS name; pairing a phone
- [ ] 2.6 The parked-test checklist as a how-to page: what to try, what to write down
  (timings, misheard words, tones that were unclear, anything that needed the screen)
- [ ] 2.7 **(Mark)** The open phone round trip of the weekend test, with HTTP Shortcuts or
  this client, whichever is first
- [ ] 2.8 **(Mark)** Parked test in the car (task 4.2 of `add-voice-bridge`); results
  recorded on the checklist page; pick the Kokoro voice by ear (old task 2.2)
- [ ] 2.9 Roadmap and phase heading

## 3. The drive

- [ ] 3.1 The journal: drives, turns, listening position, notes, arrivals; owner-only
  files; a retention setting (D10)
- [ ] 3.2 Control words matched by the bridge as whole utterances; "ja" and "nee" accepted;
  aliases in configuration (D18)
- [ ] 3.3 Project by voice: fuzzy match on names and aliases, the name spoken back, two
  close candidates read out; vocabulary hints passed to speech-to-text (old task 3.3)
- [ ] 3.4 Listening: "go on", "repeat", "back"; position kept across interruptions and
  reconnects (D17)
- [ ] 3.5 "Where was I": the recap from the journal, without an agent turn (D19)
- [ ] 3.6 Background turns: patience window, the result stored as an arrival, `GET
  /drive/events` with `Last-Event-ID`, "what's new", "cancel"; agent timeout default 600 s
  (D20)
- [ ] 3.7 "Note": written to the journal, answered with "Noted"
- [ ] 3.8 The drive log as Markdown per drive, with full replies and session ids; a how-to
  on continuing a drive's session at the desk
- [ ] 3.9 Web client: the `more` and `arrival` tones wired to the new events
- [ ] 3.10 Tests for every control word, for a dropped connection mid-turn, and for a
  reconnect that finds an arrival
- [ ] 3.11 **(Mark)** Parked test of this phase; misses of the grammar recorded
- [ ] 3.12 Roadmap and phase heading

## 4. Handoffs

- [ ] 4.1 The handoff file: format, slot kinds (`choice`, `lookup`, `text`), read-back
  sentence, command as an argument list; refused when not owner-only (D21)
- [ ] 4.2 Resolution and read-back; the pending confirmation: single-use, bound to the
  resolved action, 30 s, exactly a yes or it is cancelled and said so
- [ ] 4.3 Running the command: no shell, validated arguments only, minimal environment
  without the token, timeout, a cap per drive; the outcome in the journal and the drive log
- [ ] 4.4 `POST /arrivals` with its own token; text capped, filtered, never passed to the
  agent
- [ ] 4.5 Modify the tests of "Approval for changes": a tool outside the read-only list
  never runs in a voice session, with or without a yes (D11)
- [ ] 4.6 Security tests: an agent reply containing a handoff phrase starts nothing; a yes
  without a pending confirmation does nothing; a stale or reused confirmation is refused;
  slot values with shell characters reach the command as one argument or are refused
- [ ] 4.7 Docs: writing a handoff, with a generic example and a plain warning that a handoff
  is as dangerous as its command
- [ ] 4.8 **(Mark)** His own `build` handoff, in his configuration and not in this
  repository; the ratatoskr side per Open question 6
- [ ] 4.9 **(Mark)** Parked test: start a real build by voice and hear it come back
- [ ] 4.10 Roadmap and phase heading

## 5. Plugin

- [ ] 5.1 `plugin/` with the manifest, the bridge package and the commands start, status,
  pair and last-drive (D25)
- [ ] 5.2 Pairing: a fresh token and a QR code in the terminal; the token only in the state
  directory, owner-only
- [ ] 5.3 The clean-room gate in CI: fails when anything under `plugin/` matches the
  deny-list of names from Mark's setup
- [ ] 5.4 Install test on a machine that is not Mark's host session: install, start, pair,
  one turn
- [ ] 5.5 Docs: install and first run for someone who is not Mark; README updated
- [ ] 5.6 Roadmap and phase heading

## 6. Android app

- [ ] 6.1 Write down the lessons of the parked tests (2.8, 3.11, 4.9) in `docs/`, and adjust
  the tasks below in a pull request before building anything
- [ ] 6.2 `client/android/`: a Kotlin app with one dark screen, pairing by QR scan, the
  protocol of D15, the six tones (D24)
- [ ] 6.3 Foreground service holding microphone and playback with the screen off; a media
  session so the wheel or headset play/pause button is the control
- [ ] 6.4 Audio routing with car Bluetooth, as decided in 6.1
- [ ] 6.5 No Google Play services, no analytics, network access limited to the paired host;
  a CI check on the dependency list
- [ ] 6.6 CI: build, unit tests, a signed APK as a release artefact of the private
  repository
- [ ] 6.7 How-to: installing the APK, pairing, the wheel button
- [ ] 6.8 **(Mark)** Parked test with the screen off and the wheel button, then the first
  use while driving
- [ ] 6.9 Roadmap; archive the change
