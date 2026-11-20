# Tasks: add-drive-mode

## Current phase: 2 (Nothing lost)

Approved by Mark 2026-11-17 (#10); the five choices inside it took their defaults (Mark,
18 Nov, #70: "defaults en proposals ok"). Phase 1 built in PR_URL. One build does one phase: the phase named in this
heading, and nothing from a later one. The last task of every phase moves this heading to the
next phase and updates `docs/reference/roadmap.yml` (an item is done only with evidence).
Every phase updates `docs/` in the same pull request (AGENTS.md) and keeps `POST /turn`
working for clients that send none of the new headers.

Tasks marked **(Mark)** need Mark's own phone, car or ears; tasks marked **(host)** need the
agent host and are done by the coding session. A build leaves them open and says so.

## 1. Journal, recap and notes (bridge)

- [x] 1.1 The journal on the state volume: drives (a new one after 30 minutes without an
  event, configurable), turns with time, project, transcript, spoken reply, turn id and
  session id; owner-only files; retention 30 days, configurable (D10)
- [x] 1.2 Control words matched as whole, normalised utterances against closed lists from
  configuration, after transcription and before the agent; the agent's text never matched
  (D12, D18)
- [x] 1.3 "Where was I": the recap from the journal in at most three sentences, without an
  agent turn; in a new drive it recaps the last one (D19)
- [x] 1.4 "Note …": the text into the journal, answered with "Noted"; never sent to the agent
- [x] 1.5 Tests: each word alone and inside a question; the recap with the agent adapter
  failing; a drive boundary; a restart of the bridge keeps the journal
- [x] 1.6 Docs: `bridge-service.md` (the journal, the words, the settings); the app's how-to
  says which words exist
- [x] 1.7 Roadmap and phase heading

## 2. Nothing lost (bridge)

- [ ] 2.1 **(host)** Confirm what the wrapper does when its ssh connection closes mid-turn,
  and record it in `docs/how-to/agent-host.md`; if it does not stop `claude`, "cancel" in
  `full` mode says it cannot stop that turn
- [ ] 2.2 `X-Jarvis-Turn-Id`: a turn id runs once; a retry gets the result or the background
  answer; without the header the bridge makes an id (D15, D20)
- [ ] 2.3 The patience window (45 s, configurable, below the 170 s limit): answer "This is
  taking a while. I'll chime when it's done.", keep reading the turn, store its result as an
  arrival (D20)
- [ ] 2.4 Arrivals also from a reply whose delivery failed, and from a turn of a project the
  driver switched away from; `X-Jarvis-Arrivals` on every reply (D27)
- [ ] 2.5 A read-back in a turn that went to the background: answer `no` to the agent host at
  once, store "I did not … Ask me again to do it." as the arrival (D28)
- [ ] 2.6 "What's new": arrivals of this drive, oldest first, then marked as heard; arrival
  text capped and filtered, never passed to the agent
- [ ] 2.7 "Cancel": stop the current project's running turn (readonly: process group; full:
  close the connection, per 2.1)
- [ ] 2.8 Tests: a dropped connection mid-turn, a broken stream, a retried turn id, a slow
  turn, a read-back in the background, "cancel" on both adapters with fakes
- [ ] 2.9 Docs: `bridge-service.md` (patience window, arrivals, headers); the wire and header
  reference updated
- [ ] 2.10 Roadmap and phase heading

## 3. The app in the drive

- [ ] 3.1 A turn id per recording in `X-Jarvis-Turn-Id`; one retry within 10 s under the same
  id, otherwise the recording is dropped and the problem tone plays
- [ ] 3.2 The `arrival` tone when `X-Jarvis-Arrivals` rose since the last reply, and the
  `problem` tone next to the existing error sentence; tones made on the phone (D14)
- [ ] 3.3 Unit tests in `:core` for the retry and the arrivals count; CI green
- [ ] 3.4 Docs: `android-app.md` (tones, what a retry does); a parked-test checklist page
  (what to try, what to write down: timings, misheard words, unclear tones)
- [ ] 3.5 **(Mark)** Parked test of phases 1 to 3: "where was I", a note, a slow question,
  the phone's network switched off mid-turn and back, "what's new"
- [ ] 3.6 Roadmap and phase heading

## 4. The drive log at the desk

- [ ] 4.1 The drive log as Markdown on the bridge, appended per event: turns with turn ids
  and session ids, notes, arrivals, cancellations (D29)
- [ ] 4.2 The wire contract: the request kind `drive_log` (data only, at most 256 KB),
  sent when a drive ends and retried until the agent host has it; the contract tests
- [ ] 4.3 The wrapper stores it as `drives/<drive id>.md` (0600, the records' retention) and
  never passes it to `claude`; tests in CI with a fake `claude`
- [ ] 4.4 Docs: `agent-host.md` (drive logs, how the coding session reads them, notes as
  requests); `readonly` mode keeps the log on the bridge
- [ ] 4.5 **(host)** The coding session's job that takes over refused turns (#55) also reads
  new drive logs, takes the notes up and pings Mark
- [ ] 4.6 Roadmap and phase heading

## 5. Project by voice

- [ ] 5.1 "Switch to …": fuzzy match against the bridge's project list and spoken aliases;
  the name spoken back; two close candidates read out and picked; no match leaves the project
  (D18)
- [ ] 5.2 Project names passed to faster-whisper as hotwords
- [ ] 5.3 `X-Jarvis-Project` on the reply; the app selects that project
- [ ] 5.4 Tests (bridge and `:core`); docs
- [ ] 5.5 **(Mark)** Parked test: switch between three projects by voice, names that fail
  recorded as aliases
- [ ] 5.6 Roadmap and phase heading

## 6. Replies in parts

- [ ] 6.1 The prompt on the agent host lets the agent add further spoken parts for a long
  answer; the wrapper returns them in the optional reply field `more`; the readonly adapter
  the same (D17)
- [ ] 6.2 The bridge enforces at most 20 parts of about 60 words, each filtered on its own;
  a read-back never has parts
- [ ] 6.3 "Go on", "repeat", "back"; the position in the journal; `X-Jarvis-More`; the app's
  `more` tone
- [ ] 6.4 Tests: a long reply, a part that fails the filter, a question in between and "go
  on" after it, a reconnect
- [ ] 6.5 **(Mark)** Parked test: have a proposal read out, stop it, ask, go on
- [ ] 6.6 Roadmap and phase heading

## 7. Screen off and the wheel button

- [ ] 7.1 Write down the lessons of the parked tests (3.5, 5.5, 6.5) in `docs/`, and adjust
  the tasks below in a pull request before building anything (D24)
- [ ] 7.2 A foreground service holding microphone and playback with the screen off (shared
  with the notification listener of `jarvis-notifications` if that exists)
- [ ] 7.3 A media session: the play/pause button of the wheel or a headset starts and stops
  a turn and stops playback
- [ ] 7.4 Audio routing with car Bluetooth, as decided in 7.1
- [ ] 7.5 CI: the permission and dependency checks widened to exactly what this needs
- [ ] 7.6 How-to: the wheel button, the screen off; what is fit for the road
- [ ] 7.7 **(Mark)** Parked test with the screen off and the wheel button, then the first use
  while driving
- [ ] 7.8 Roadmap; archive the change
