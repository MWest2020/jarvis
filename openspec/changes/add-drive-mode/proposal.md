# Change: add-drive-mode

## Status

**Approved by Mark on 2026-11-17** in [#10](https://github.com/MWest2020/jarvis/issues/10)
("Jarvis: rijmodus goedkeuren?"): "definitely approve".

The proposal he approved was written on 2026-11-14 ([#9](https://github.com/MWest2020/jarvis/pull/9)).
Between then and the approval Mark took decisions that replace parts of it, and some of it
was built in other changes. This version, rewritten on 2026-11-17, keeps what is still new
and re-points or drops the rest. It adds no new direction: every part that changed follows a
decision Mark already took, listed here.

| #9's default (14 Nov) | Superseded by | What this version does |
|---|---|---|
| Q1: the voice session stays read-only; changes only through *handoffs* (rule 1, D11, D21, the capability `handoffs`, phase 4, `POST /arrivals`) | `jarvis-full-agent` (#20, Mark 2026-11-15: "jarvis may merge etc. everything you can do"; live 2026-11-16) | Dropped. In `full` mode Jarvis acts itself; irreversible actions are read back and need a spoken yes (requirement "Voice confirmation"). What cannot be done by voice goes to the coding session ([#55](https://github.com/MWest2020/jarvis/pull/55)). "Note" stays. |
| Q4: a web client before the native app (D23, phase 2, the capability `phone-client`) | `add-android-client` (#12; the app exists, signed with one key since #54) | Dropped. Drive mode extends the app in `android/`. |
| Q7: press to talk only, no wake word (rule 3, first half; D13) | The roadmap phase "voice-and-speed" (Mark 2026-11-16, #49): waking on Mark's voice ([#23](https://github.com/MWest2020/jarvis/issues/23)), the speaker check approved in #30 (`jarvis-speaker-verification`) | Drive mode no longer decides how an utterance starts (a tap, a hold, the wheel button, later the wake word). The other half of rule 3 stays: Jarvis speaks only when spoken to. |
| Q6: the factory side of a `build` handoff (a ratatoskr change) | `jarvis-full-agent`: the build system already accepts the identity `jarvis` | Gone; Jarvis starts a build as a full agent. |
| Phase 1 "Live turn": a new streamed protocol, per-turn timings, the agent adapter seam (D15, D16, D22) | Streaming speech ([#66](https://github.com/MWest2020/jarvis/pull/66)), per-stage timing ([#60](https://github.com/MWest2020/jarvis/pull/60)), the remote adapter of `jarvis-full-agent` | Dropped. Drive mode builds on `POST /turn` as it is. |
| End of speech by silence detection (D13, D24) | Send after 3 s of silence ([#69](https://github.com/MWest2020/jarvis/pull/69)) | Built; drive mode uses it. |
| Phase 5 "Plugin" (D25) | Not about the drive; the machine side is now two parts (bridge and agent host) | Moved out of this change. The roadmap phase "Plugin" keeps its place for a change of its own. |
| Q2 one change, Q3 English only ("ja" and "nee" count), Q5 sideloaded APK | Unchanged; Q5 is how the app ships today | Kept. |

Also built since, and used here as they are: the session idle reset and close by voice
([#68](https://github.com/MWest2020/jarvis/pull/68)), read-only helper subagents
([#58](https://github.com/MWest2020/jarvis/pull/58)), and the approved but not yet built
notifications, fingerprint confirmation and speaker check (#29, #30).

## Why

Jarvis can now do what the coding session can, answers in about 15 seconds, starts speaking
sooner and sends a question when Mark stops talking. What it still cannot do is the car:

- **Nothing remembers the trip.** Back in the car after a stop, there is no "where was I".
  Each turn stands alone, and the session itself is reset after an hour idle (#68).
- **Slow results are lost.** A turn that outlasts the phone's wait (the bridge answers "still
  running" after 170 s), a tunnel that drops the connection, or a reply that broke off
  mid-stream: the work finishes on the agent host, and the answer never reaches Mark.
- **Nothing is waiting at the desk.** What was asked, answered and decided in the car is
  spread over the agent host's turn records; what Mark wanted to remember ("note: check the
  token rotation tonight") has nowhere to go.
- **The car needs more than a screen-on app.** Tones that say what is happening, the wheel
  button with the screen off, and the project changed by voice.

## The idea

The unit is the **drive**, not the turn. A drive has a start ("where was I"), a middle (a
conversation you can interrupt, with slow results arriving when they are ready) and an end (a
written log of the drive, waiting at the desk).

Three rules shape it:

1. **Jarvis speaks only when spoken to.** Everything that happens on its own (still working,
   there is more, something came back, something failed) is a tone, never a sentence. A
   recap or an arrival is spoken when Mark asks for it.
2. **The bridge handles the drive's own words.** "Where was I", "what's new", "note …",
   "switch to …", "go on", "cancel" are recognised by the bridge from Mark's own transcript,
   before anything goes to the agent, and work when the agent is busy or down. The agent's
   text never triggers them. The yes to a read-back stays what `jarvis-full-agent` defines.
3. **Nothing is lost.** A turn runs to its end whatever happens to the phone's connection.
   What could not be delivered waits as an *arrival*, announced by a tone.

### A drive, start to finish

Mark gets in, taps (or presses the wheel button) and says "where was I". Jarvis: "Wordsworth.
You asked why the index test fails; the fixture is stale. One thing came back while you were
away." He says "what's new": the review he asked for yesterday evening finished after he
parked. He says "switch to jarvis", hears "Jarvis", and asks for the drive-mode proposal. He
hears the gist and a soft tone that means there is more. "Go on" plays the rest in short parts;
a tap stops it, he asks a question, and "go on" picks the reading up where it was cut. He asks
Jarvis to start the build of the change; Jarvis reads it back, he says yes. In a tunnel a slow
question loses its connection; a minute later the arrival tone sounds. He says "note: check
the token rotation tonight". At his desk the coding session has the drive log: the questions,
the turn ids with their full answers, the build, the arrival, and the note, which it takes up.

## What Changes

- **The drive journal** on the bridge (its state volume): drives, turns, notes, arrivals and
  the listening position. A drive starts with the first event after 30 minutes without one.
- **Control words**, matched by the bridge as whole utterances: "where was I" (a recap built
  from the journal, no agent turn), "what's new", "note …", "cancel", and later "switch to …",
  "go on", "repeat", "back".
- **Nothing lost.** The app sends a turn id; a retried upload runs once. A turn that outlasts
  a patience window (45 s by default) is answered with "This is taking a while. I'll chime
  when it's done." and keeps running; its result, like a reply that could not be delivered,
  becomes an arrival. Every reply tells the app how many arrivals wait. A read-back that
  arrives in the background is not left waiting: it is cancelled, as when a read-back is not
  heard to its end (`jarvis-streaming-speech` S6), and the arrival says what was not done.
- **The drive log at the desk.** Each drive is written as Markdown on the bridge and, when
  the drive ends, handed to the agent host as data, where the coding session picks it up the
  way it picks up refused turns (#55).
- **In the app:** the drive's tones (more, arrival, problem, next to the existing waiting
  tick), a retry under the same turn id, and later the screen off with the wheel button.
- **Project by voice** against the list the bridge already has, and **replies in parts** for
  long answers, read out on "go on".

Not in this change: how an utterance starts (the wake word is #23; the speaker check is
`jarvis-speaker-verification`), notifications on the phone (`jarvis-notifications`, which this
change only connects to), what the agent may do (`jarvis-full-agent`), the plugin, Android
Auto, a Dutch voice.

## Phases

Each phase is one small build and useful without the next. `tasks.md` names the current
phase on top.

| # | Phase | You can then |
|---|---|---|
| 1 | Journal, recap and notes (bridge) | say "where was I" and "note …" with the app as it is |
| 2 | Nothing lost (bridge) | lose the connection or wait out a slow turn, and get the answer later with "what's new" |
| 3 | The app in the drive | hear the drive's tones; an upload that failed is retried once without running twice |
| 4 | The drive log at the desk | find the drive at the desk, with the notes taken up by the coding session |
| 5 | Project by voice | say "switch to ratatoskr" and have the app follow |
| 6 | Replies in parts | listen to a long answer in parts, stop it and pick it up again |
| 7 | Screen off and the wheel button | do all of it with the phone in the mount, screen off; then on the road |

## Impact

- New capability `drive-journal` (journal, recap, notes, background turns and arrivals,
  retried turns, read-backs in the background, the drive log).
- Capability `voice-bridge`: requirements added (Control words, Speak only when spoken to,
  Cancelling a turn, Project selection by voice, Replies in parts). No requirement of the
  living spec is modified, so this change does not collide with the open changes that do.
- Capability `android-client`: requirements added (Drive tones, No silent failure, Project
  follows the bridge, Listening in parts, Screen off and the wheel button, Parked test before
  driving).
- Code: `bridge/` (phases 1, 2, 4, 5, 6), `agent_host/` (phase 4: storing a drive log; phase 6:
  further spoken parts in the reply), `android/` (phases 3, 5, 6, 7). All under the
  `docs-gates` drift check.
- Wire contract with the agent host (phases 4 and 6): one data-only request kind (a drive
  log) and one optional reply field (further spoken parts), both additions in the manner of
  `jarvis-general-conversation` D51, so either side can be updated first.
- Headers between bridge and app: `X-Jarvis-Turn-Id` (in), `X-Jarvis-Arrivals` and
  `X-Jarvis-Project` and `X-Jarvis-More` (out). Old apps and HTTP Shortcuts ignore them.
- `jarvis-notifications`: when it is built, a background result is also sent as its kind
  `reply` (its N10), and in a drive a received notification is an arrival (its N9).

## Open questions

Mark approved the change; these are the choices inside it, each with the default that is
built if nobody answers. They are put to him in
[#70](https://github.com/MWest2020/jarvis/issues/70).

1. **The patience window.** How long the bridge waits before it says "I'll chime when it's
   done". Measured turns take 15 to 25 s, a web question about 20 s.
   *Default: 45 s, configurable; tuned after the parked test of phase 3.*
2. **A read-back that arrives in the background.** It could stay pending until Mark hears
   it, but its 60 seconds run on the agent host and a stale read-back is the risk the yes
   exists to avoid.
   *Default: it is cancelled at once and the arrival says what was not done ("I did not
   merge pull request 21 on jarvis; ask me again"). Asking again gives a fresh read-back.*
3. **Where the drive log lands.** On the bridge's volume only, or also on the agent host.
   *Default: both; the agent host copy is what the coding session reads. In `readonly` mode
   it stays on the bridge.*
4. **What the coding session does with a note.** A note is Mark's own words.
   *Default: it takes a note up like a turn passed on to it (#55): under its own rules, and
   it pings Mark the outcome. A note that is a question for Mark goes in his digest.*
5. **The plugin.** Out of this change (see the table above).
   *Default: yes, out; the roadmap phase stays and gets a change of its own when wanted.*
