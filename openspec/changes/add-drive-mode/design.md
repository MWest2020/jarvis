# Design: add-drive-mode

## Context

- What exists: `POST /turn` on the tailnet with a token. One request does everything: upload
  audio, faster-whisper, `claude -p --output-format json` with `Read`, `Glob`, `Grep` only,
  one summary line, Kokoro, one WAV back (`bridge/`, D1 to D9 of `add-voice-bridge`).
- What the first test showed (`docs/how-to/bridge-service.md`, Test on 2026-11-14): a simple
  turn takes about 4 s in Claude Code alone; on a small machine a Kokoro render and one turn
  ran into the 120 s timeout. The phone round trip with HTTP Shortcuts has not been done yet.
- Rules that stay (AGENTS.md): private network plus token, an explicit tool allow-list, local
  speech by default, no third-party platforms, nothing of Mark's setup in the plugin.
- The decisions below continue the numbering of `add-voice-bridge` (D1 to D9).

## Goals / Non-Goals

Goals:
- No silent waiting: the driver always knows within a moment whether Jarvis heard, is
  working, has more, or failed.
- The driver decides when attention is taken. Jarvis never starts talking on its own.
- A spoken yes can only start something that was defined beforehand and read back.
- Work survives long turns, lost connections and stops.
- What was said in the car is available at the desk.
- Each phase is small enough for one factory build and useful without the next one.

Non-Goals:
- Editing files or running commands from the voice session.
- A wake word, an always-open microphone, talking over Jarvis without a press.
- Where the bridge runs and how it is exposed (`run-bridge-in-cluster`).
- Hosted speech providers, the Codex adapter, the local-model adapter. Only the seams.
- Android Auto, a Play Store listing, a Dutch voice.

## Decisions

**D10. The unit is the drive.** The bridge keeps a journal per drive: turns, the listening
position, notes, handoffs, arrivals. A drive starts with the first event after 30 minutes
without one (configurable) and has no explicit end. The journal is the single source for the
recap (D19), the arrivals (D20) and the drive log, which is the journal written out as
Markdown on the machine, appended as the drive goes, so no "end" event is needed.
Alternative considered: keep turns stateless and let the Claude Code session be the memory.
Rejected: the session knows the conversation of one project, not what was handed off, what
came back, what was noted or how far a reading got, and asking it costs an agent turn.

**D11. The front seat directs; it does not edit.** The agent in a voice session keeps the
read-only tool list for good. Everything that changes something is a handoff (D21).
Alternatives considered:
- *A spoken yes per tool call* (a PreToolUse hook or `--permission-prompt-tool` that asks the
  phone). This is what D6 and task 1.3 had in mind. Rejected for the car: the unit is wrong.
  One small edit is several tool calls, each needing a judgement about something the driver
  cannot see, while the turn is held open. A yes given in traffic to "Edit on summary dot py"
  is not consent to anything in particular.
- *A yes that widens the tool list for one turn.* Rejected: what was read back and what the
  agent then does are not bound to each other.
- *A built-in "worker" handoff* (a second headless session with write tools in a fresh
  worktree that opens a pull request). Attractive, and the handoff mechanism allows it as
  configuration, but shipping it in the plugin means shipping an unattended writer. Not in
  this change.
The cost: "fix that typo" by voice only works when the user has configured something to hand
it to. For Mark that is the factory. This is Open question 1.

**D12. Actions come from the driver's words only.** The agent reads repository content, which
may contain instructions written by someone else. Therefore the agent's output has exactly
one effect: it is spoken (after the filter of D17) and logged. Project switching, handoffs,
notes and confirmations are recognised by the bridge from the transcript of the driver's own
utterance. The agent gets no tool, marker or MCP server that can trigger any of them, and
arrival text is never fed to the agent.
Alternative considered: give the agent a `handoff` tool and confirm by voice. Rejected: a
planted instruction could then put a plausible-sounding action in front of a driver whose
only check is one read-back sentence.
Alternative considered: a tool-less model call that parses the transcript into an action.
Safe (its only input is the driver's speech) and better with free phrasing, but it adds a
model round trip before every read-back. Kept as a later option if the closed grammar of D18
turns out too stiff in the parked test.

**D13. The microphone opens only on a press.** One press opens it; it closes at the end of
speech (silence, a second press, or 30 s). Playback stops the moment it opens. Consequences:
there is no echo problem to solve, because Jarvis never speaks while listening; nothing the
radio or a passenger says is ever captured; a confirmation always follows a deliberate
press; and privacy is simple to state. The press is the interruption: it works while Jarvis
speaks (speech stops, the rest of the reply stays available) and while it works (the new
utterance can be a control word such as "cancel").
Alternatives considered: a wake word (needs an always-on on-device model, false triggers in
road noise, a separate change if wanted); open-microphone barge-in (needs echo cancellation
that differs per car and Bluetooth profile, and makes every sound in the car a possible
command). This is Open question 7.

**D14. Speak only when spoken to; tones for everything else.** Six tones, produced on the
client so they play instantly and without a connection:

| Tone | Means |
|---|---|
| listening | the microphone is open |
| heard | the microphone closed and the audio is on its way |
| working | a soft pulse every few seconds while the agent works |
| more | the reply has further parts; "go on" plays them |
| arrival | something finished or came back; "what's new" plays it |
| problem | it failed or there is no connection; one short sentence follows when there is one |

Speech happens only as the direct answer to an utterance. Arrivals and the recap are never
pushed. Spoken parts are short (D17), there is at most one pending confirmation, and a press
silences Jarvis within 200 ms.
Alternative considered: speak the recap on the first press after a stop, and speak arrivals
as they come in. Rejected: a press means "I want to say something", and an unprompted
sentence arrives at a moment the driver did not choose.

**D15. A streamed HTTP response plus server-sent events; not WebSocket, not WebRTC.**
`POST /drive/turn` takes the audio and a client-made turn id and answers with a stream of
newline-delimited JSON events (`heard` with the transcript, `working`, `speech` with one
rendered part, `more`, `backgrounded`, `done` with timings, `problem`). `GET /drive/events`
is a server-sent event stream for what happens outside a turn (arrivals), resumable with
`Last-Event-ID`. The turn id makes a retried upload safe: the bridge runs a turn id once.
Why: nothing here needs audio flowing from the phone while the bridge is answering (D13), so
a bidirectional socket buys nothing. Streamed responses work with the standard-library
server the bridge already has, with `curl -N` for tests, with `fetch` in a browser and with
OkHttp on Android, and they pass an HTTP ingress.
Alternatives considered: WebSocket (a new dependency or a server rewrite for a capability the
design does not use); WebRTC (real-time audio and echo cancellation built in, but ICE and a
media stack for a half-duplex exchange of a few seconds).
Audio in events is base64; each event names its format. WAV first; a compressed format when
the parked test shows that the mobile link needs it.

**D16. Latency: remove the silence, then remove the waste, and measure both.** The agent's
thinking time cannot be designed away. What can:
- *Silence.* The `heard` tone is local, so it follows the end of speech immediately. The
  `working` pulse starts when the bridge reports the transcript.
- *Order.* The appended system prompt asks for the spoken part first and the written detail
  after it. The adapter reads `claude -p --output-format stream-json` with partial messages
  (both flags exist in the installed CLI, 2.1.x; the event shapes are confirmed in task 1.1
  before anything is built on them), so the first spoken part is rendered and sent while the
  detail is still streaming.
- *Rendering.* Text-to-speech runs per part, not per reply; the next part renders while the
  current one plays.
- *Measurement.* Every turn logs when the audio arrived, when the transcript was ready, when
  the agent's first spoken part was complete, when its audio left, and when the turn ended.
  The `done` event carries the same numbers.
Targets for the parked test, on the machine the bridge really runs on: `heard` tone within
150 ms of the end of speech; first `working` pulse within 2 s; for a question that needs no
tool call, first speech within 6 s. These are targets to measure against, not promises: the
only number we have today is "about 4 s" for the agent on a simple turn.
Alternatives considered: a long-lived agent process fed with `--input-format stream-json`
(saves process start per turn; changes how sessions, cancelling and crashes work, so only
when the timings show that start-up is what hurts); streaming speech-to-text (faster-whisper
transcribes a few seconds of speech in about a second, so the gain is small); a smaller
Whisper model (trades accuracy on technical terms, which is the scarcer thing).

**D17. A reply is a script of spoken parts.** The agent ends its work with a block of short
paragraphs written for the ear: the first is the answer in one sentence; further ones exist
only when there is more worth hearing. The bridge enforces the shape, not the model: at most
20 parts, at most about 60 words each (a longer one is split at a sentence end), and each
part passes the existing "no code, no paths" filter on its own. A part that fails is replaced
by "One part is only in the written log", instead of losing the whole reply as today. A
script of one or two parts plays through. With more, the first plays, then the `more` tone.
"Go on" plays the rest with a short pause between parts, "repeat" replays the current part,
"back" the one before. The position is in the journal, so "go on" also works after an
interruption, a question in between, or a stop. "Read me the proposal" is the same
mechanism with a longer script, not a separate feature.
Alternatives considered: a second model call that turns the reply into a listening version
on request (an extra round trip exactly when the driver asked for more); raising the summary
cap (a two-minute monologue you cannot navigate).

**D18. Control words are matched by the bridge, as whole utterances, against closed lists.**
Before an utterance reaches the agent, the bridge checks it against a small grammar:

| Utterance | Effect |
|---|---|
| "go on", "repeat", "back" | move through the current script |
| "where was I" | the recap (D19) |
| "what's new" | play waiting arrivals, oldest first |
| "switch to ‹project›" | change project; Jarvis answers with the project's name |
| "cancel" | stop the running turn of the current project |
| "note ‹anything›" | add the text to the drive's notes; Jarvis answers "Noted" |
| ‹a handoff's phrase› ‹its slots› | prepare a handoff and read it back (D21) |
| "yes" / "no" (and "ja" / "nee") | answer the pending confirmation, if there is one |

Fixed phrases count only when they are the whole utterance, so "go on and explain the
config" goes to the agent. Names are matched fuzzily against the configured ones and their
spoken aliases; two close candidates are read out and the driver picks. The words work when
the agent is busy or down, cost no model call, and cannot be influenced by file content.
The same lists (project names, aliases, handoff phrases) are given to speech-to-text as
vocabulary hints, which faster-whisper supports, so "ratatoskr" has a chance.
Alternative considered: let the agent interpret everything. Rejected by D12, and because
"stop" must not depend on a model.

**D19. The recap is assembled, not generated, and only on request.** "Where was I" answers
from the journal with a fixed template of at most three sentences: the project and the last
question with its one-sentence answer; how many arrivals wait and whether a handoff is still
running; where a reading was left ("part 3 of 7"). No agent turn: it must be instant, it
must work when the agent is unreachable, and it must not invent anything. A stop needs no
detection: the journal is simply there when the client reconnects.
Alternative considered: ask the resumed session to summarise. Better prose, but seconds of
waiting, tokens, and only one project's view.

**D20. Long turns go to the background; a lost connection loses nothing.** A turn runs to its
end on the machine whatever happens to the request. If it outlives the patience window (20 s
by default) the stream says "This is taking a while. I'll chime when it's done" and ends; the
result is stored as an arrival and the client gets the `arrival` tone. The same happens when
the connection drops or when the driver has switched project in the meantime. The agent
timeout can then be longer than a driver would wait (default raised from 120 s to 600 s),
while "cancel" still kills the process group as the timeout does now. One running turn per
project stays the rule; a second question for a busy project gets a short spoken "still
working on the last one".
Alternative considered: queue further questions behind the running turn. Rejected: answers
would come back out of step with what the driver is thinking about.

**D21. Handoffs: defined by the user, started by the driver, confirmed by voice.** A handoff
is an entry in a file in the bridge's configuration directory, outside the repository and
outside the plugin: a name, the phrases that trigger it, its slots, a read-back sentence and
a command as an argument list. Slot kinds: `choice` (a fixed list with spoken aliases),
`lookup` (a command that prints the valid values, cached briefly; for example the open
changes of the chosen repository) and `text` (free text, length-capped, passed as a single
argument and read back verbatim). The flow: the bridge resolves the slots, reads back the
resolved action ("Start a build of jarvis, change add-drive-mode. Press and say yes."), and
runs the command only when the next utterance, within 30 s, is exactly a yes. Anything else
cancels, and Jarvis says so. Reading back the resolved action rather than the transcript is
how this change handles the old design risk "echo the transcript back before executing
anything that changes files": a misheard name either resolves to something real that the
driver hears, or to nothing.
Safety properties, enforced in code: no shell, arguments are substituted only after
validation; the command gets a minimal environment without the bridge's token and a timeout;
the handoff file must be owner-only or the bridge refuses to load it; every handoff confirms
(there is no "trusted" flag); a confirmation is single-use and bound to that one resolved
action; at most five handoffs per drive by default.
Coming back: `POST /arrivals` with its own token (which can do nothing else) lets the
receiving system report a result. The text is treated as untrusted: length-capped, filtered
like agent speech, never given to the agent, announced with a tone and spoken on "what's
new". Without a report, the driver hears "Started" and nothing more.
Mark's factory is one entry in Mark's own file: phrase "build", a `choice` of repositories, a
`lookup` of changes, and a command that submits `!build <repo> <change>` as session
`jarvis`. None of that is in the repository's plugin directory (D25 checks it).
Alternatives considered: handoffs as code in the bridge (puts personal setup in the plugin);
an HTTP kind next to the command kind (useful when the bridge runs in a container without
the tools; added when `run-bridge-in-cluster` needs it, not before).
Also built in, and not a handoff: "note". It writes only to the journal, so it needs no yes.

**D22. Two seams, cut where the stream needs them** (D8 and task 2.3). The agent adapter:
start a turn (prompt, working directory, session id), get a stream of events (text as it
arrives, then the final reply and session id, or an error), and cancel. The speech
providers: transcribe (audio, language, vocabulary hints) and synthesize (text, voice), each
with a warm-up. The bridge core imports neither Claude Code nor Whisper nor Kokoro directly,
and a fake adapter and fake speech providers make the whole turn testable in CI without
models. Providers are chosen in configuration; the defaults are local, and at start the
bridge logs which are active and whether audio leaves the machine. Only the Claude Code
adapter and the two local providers are built.
Alternative considered: defer the seams to the plugin phase. Rejected: the streamed turn
rewrites both call sites anyway, and the fakes are what let the factory test phases 1 to 4.

**D23. A reference web client before the native app.** One static page served by the bridge:
a full-screen button, the six tones, end-of-speech detection, the playback queue. No external
scripts, fonts or requests; the token arrives once in the URL fragment of the pairing link
and stays in the phone's browser storage. It is the client for the parked test (task 4.2)
and for automated end-to-end tests with a fake microphone. It is not the car client: a
browser stops recording when the screen goes off and does not reliably get the wheel button.
It needs HTTPS for microphone access, which the bridge does not do itself: the page is used
through the HTTPS name Tailscale provides (`tailscale serve`, or the operator in the
cluster). The bridge's own listener and the requirement "Network exposure" do not change.
Alternative considered: go straight to the native app. Rejected: the protocol would first be
exercised in the toolchain the factory can test least, and the parked test would wait for
the last phase. This is Open question 4.

**D24. The native app is thin.** Kotlin, one screen that is dark while in use, a foreground
service that holds the microphone and playback, and a media session so that the play/pause
button on the wheel or a headset is the one control. Pairing by scanning the QR code from
D25. It speaks the protocol of D15 and adds nothing to it. No Google Play services, no
analytics, no cloud speech; the only host it contacts is the paired bridge. End of speech:
silence detection with a second press as the reliable fallback, because road noise defeats
simple detectors. Which microphone and audio route work best over car Bluetooth is not
decided here: phase 6 starts by writing down what the parked tests of phases 2 to 4 showed,
and adjusts its tasks before building.
Built as a signed APK in GitHub Actions and sideloaded (Open question 5). The phone is in a
mount or a pocket, never in the hand.

**D25. The plugin.** `plugin/` holds the manifest, the bridge package and three commands:
start the bridge, show its status, and pair a phone (prints a QR code with the address and a
fresh token in the terminal). Configuration is the plugin's own settings plus the handoff
file. A "last drive" command prints the latest drive log into a Claude Code session, so the
desk can pick up what the car left. A CI gate fails the build when anything under `plugin/`
matches a deny-list of names from Mark's setup; the list lives outside `plugin/`.
Alternative considered: publish the bridge as a Python package and let the plugin be a thin
installer. Possibly later; one artefact is easier to review now.

**D26. One change, six phases, one build each.** `tasks.md` names the current phase on top;
a build does that phase only, and its last task moves the marker and updates
`docs/reference/roadmap.yml`. Phases 1 to 4 are the bridge and the web client; 5 and 6 are
packaging and the app. Phase 5 can move before phase 3 without harm if the plugin becomes
urgent; phase 6 must follow phase 2. This is Open question 2.

## Risks / Trade-offs

- **Driver distraction.** Mitigations are structural: one control, tones instead of
  sentences, short parts, silence on a press, nothing unprompted, no screen. Each phase that
  changes what is heard is tried parked first (task 4.2), and the parked checklist is the
  gate for using it while driving.
- **A wrong yes.** Limited by D13 (a press before every utterance), D21 (only predefined
  actions, read back resolved, single-use confirmation) and by what handoffs the user chooses
  to define. A handoff is as dangerous as its command; the docs say so plainly.
- **The grammar is too stiff.** Whole-utterance matching may feel rigid ("carry on" is not
  "go on"). Aliases are configuration; the parked test collects the misses; D12 names the
  fallback.
- **Speech-to-text on names.** Vocabulary hints and fuzzy matching help; a name that still
  fails gets a spoken alias in configuration.
- **The stream-json shapes change with a CLI update.** They are confirmed in task 1.1,
  isolated in the adapter, covered by a recorded-output test, and the non-streamed path of
  `POST /turn` stays as a fallback.
- **The living spec lags** while a six-phase change is open, and `run-bridge-in-cluster`
  edits the same capability. This change does not modify "Network exposure" or "Session
  continuity", so the two do not collide on a requirement.
- **The factory cannot run an Android build locally.** CI builds and tests the app; the
  builder's own checks cover the protocol through the web client.
- **Drive logs hold full replies**, which can quote file contents. Owner-only files, in the
  state directory, deleted after 30 days by default.
- **The one part that is not sovereign is the model.** Speech, journal, client and transport
  are the user's own. The adapter seam (D22) is what keeps a local model possible later.

## Migration

`POST /turn` keeps working unchanged, so the HTTP Shortcuts setup and the open phone round
trip of the weekend test are not affected. The summary line of the weekend slice becomes the
first spoken part; `/turn` returns exactly that part. The session store keeps its format.

## Open Questions

For Mark: see `proposal.md`. For the build, to settle in the phase named:
- Phase 1: the exact stream-json events to rely on (task 1.1).
- Phase 2: whether WAV is small enough over the mobile link, or parts must be compressed.
- Phase 6: microphone and audio route over car Bluetooth; which end-of-speech detector.
