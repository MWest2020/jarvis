# Design: add-drive-mode

## Context

What exists on 2026-11-17 (see `docs/index.md` for each change):

- **The bridge** runs as a pod in the cluster, tailnet-only (`run-bridge-in-cluster`), with a
  persistent state volume (its D6). `POST /turn` takes the audio, transcribes it with
  faster-whisper in the pod, runs the turn and answers with speech: a WAV, or a stream of
  raw PCM sentence by sentence for a client that asks (`jarvis-streaming-speech`). Every
  stage is timed (`X-Jarvis-Timing`, #60). `GET /projects` lists the projects.
- **Two agent modes** (`jarvis-full-agent`). In `full` mode the turn runs on the agent host
  over one ssh connection per turn, with the coding session's rules; an irreversible action
  is refused by a gate, read back in one sentence built by rules, and released by a spoken
  yes within 60 seconds (D36). What cannot be done by voice is passed on to the coding
  session, which takes it over from the turn record (#55). Each turn leaves an owner-only
  record on the agent host with the prompt and the full reply (D38). The bridge waits up to
  170 s for the agent host, then tells the phone the turn is still running; the turn goes on,
  and its answer is not delivered.
- **Sessions** are reset after an hour without a turn, and closed by voice on request (#68).
- **The app** (`android/`): one large control (hold, or tap and stop talking: #69 sends after
  3 s of silence), the project picker with "General", a beep when the recording is sent and
  a soft tick while waiting, playback that a tap stops, errors in words.
- **Approved, not built:** notifications through a self-hosted ntfy broker
  (`jarvis-notifications`), a fingerprint confirmation (`jarvis-biometric-confirm`), a
  speaker check before transcription (`jarvis-speaker-verification`). Waking on Mark's voice
  (#23) is on the roadmap, without a change yet.

The decisions continue the numbering of `add-voice-bridge` (D1 to D9). Numbers D10 to D26
are kept so that references from other changes stay valid; a decision that no longer holds
is marked *withdrawn* with what replaced it. New decisions are D27 to D29.

## Goals / Non-Goals

Goals:
- The driver always knows, without looking, whether Jarvis is working, has more, has
  something waiting, or failed.
- The driver decides when attention is taken. Jarvis never starts talking on its own.
- Work survives long turns, lost connections and stops.
- What was said in the car is available at the desk.
- Each phase is small enough for one build and useful without the next.

Non-Goals:
- What the agent may do and how a yes works (`jarvis-full-agent`).
- How an utterance starts: tap, hold, silence, the wake word (#23), the speaker check.
- Notifications as such (`jarvis-notifications`); this change only connects to them.
- A new transport, a web client, the plugin, Android Auto, a Dutch voice.

## Decisions

**D10. The unit is the drive.** The bridge keeps a journal per drive: turns (time, project,
transcript, the spoken reply, the turn id and session id), notes, arrivals and the listening
position. A drive starts with the first event after 30 minutes without one (configurable)
and has no explicit end; it ends when the next one starts or when 30 minutes pass. It is
not a mode that is switched on: every use by voice is a drive, and "in a drive" means "while
one is open". The journal lives on the bridge's state volume, owner-only, kept 30 days.
Alternative considered: let the Claude Code session be the memory. Rejected: a session knows
one project's conversation, is reset after an hour idle (#68), and knows nothing of what
arrived, what was noted or how far a reading got; asking it costs a turn.

**D11. Withdrawn** ("the front seat directs; it does not edit"). Replaced by
`jarvis-full-agent`: in `full` mode Jarvis acts as the coding session does, under its rules,
with a spoken yes for the gated classes (D36). `readonly` mode keeps its read-only tools.

**D12. The drive's own words come from the driver only** (narrowed, as `jarvis-full-agent`
asked). The bridge recognises control words (D18) from the transcript of the driver's own
audio. The agent's text, an arrival's text and file content never trigger one; an arrival
or a recap is never fed to the agent as a prompt. What the agent may *do* is no longer
decided here: it acts under the classifier, the hooks and the gate, and only the bridge's
reading of a yes can release a gated call.

**D13. How an utterance starts is not decided here** (rewritten). #9 opened the microphone
only on a press. Since then #69 sends after silence, and the voice-and-speed phase adds the
wake word (#23) with a speaker check. Drive mode works with any of them and adds one: the
wheel or headset button (D24). What stays: the microphone is not open while Jarvis speaks
(the app has no barge-in), so a tap or a press is the interruption: playback stops, the
rest of the reply stays available, and the new utterance can be a control word such as
"cancel".

**D14. Speak only when spoken to; tones for everything else.** Tones are made on the phone,
so they play at once and without a connection:

| Tone | Means | Exists |
|---|---|---|
| sent | the recording has gone out | yes (#12, A8) |
| working | a soft tick every few seconds while the bridge works | yes (#12, A8) |
| more | the reply has further parts; "go on" plays them | phase 6 |
| arrival | something finished or came back; "what's new" plays it | phase 3 |
| problem | it failed or there is no connection; one short sentence follows when there is one | phase 3 |

Speech happens only as the answer to an utterance. Arrivals and the recap are never pushed
as speech. This is also the default `jarvis-notifications` follows (its N9 and open question
2), and a wake word does not change it: waking is the driver speaking first.

**D15. On `POST /turn`, not a new protocol** (rewritten). #9 planned a stream of JSON events
and a server-sent event stream. Streaming speech (#66) already sends the reply as it is
rendered, and the app reads headers before the audio. Drive mode adds headers, not routes:

| Header | Direction | Means |
|---|---|---|
| `X-Jarvis-Turn-Id` | app to bridge | a client-made id (a UUID); the bridge runs a turn id once (D20) |
| `X-Jarvis-Arrivals` | bridge to app | how many arrivals wait in this drive; the app plays the arrival tone when it rises |
| `X-Jarvis-Project` | bridge to app | the project is now this one (D18, phase 5) |
| `X-Jarvis-More` | bridge to app | how many further parts the reply has (D17, phase 6) |

A control word is an ordinary `POST /turn`: the bridge answers it with ordinary speech.
Clients that do not know a header ignore it; HTTP Shortcuts keeps working.

**D16. Withdrawn** (latency: streamed agent output, per-turn timings). Replaced by
streaming speech (#66) and per-stage timing (#60); the voice profile and Whisper settings
(#60) and the session idle reset (#68) are the speed work of the voice-and-speed phase.

**D17. A long reply is a script of spoken parts** (kept, re-pointed). Today the agent's
spoken line is one short summary (about 160 characters, #41). For a long answer ("read me
the proposal") the agent may write further spoken parts after it, each short and written for
the ear. The wrapper takes them like the spoken line and returns them in an optional reply
field of the wire contract (`more`: a list of strings), an addition in the manner of D51 of
`jarvis-general-conversation`. The bridge enforces the shape: at most 20 parts, about 60
words each (a longer one is split at a sentence end), each through the same filter as the
spoken line on its own; a part that fails becomes "One part is only in the written log". The
first part plays as today, then the `more` tone. "Go on" plays the next parts with a short
pause, "repeat" the current one, "back" the one before; the position is in the journal, so
it survives a stop, a question in between or a reconnect. A read-back is never split and has
no further parts (`jarvis-full-agent` D36, `jarvis-streaming-speech` S5).
Alternatives considered: a second model call that makes a listening version on request (a
round trip exactly when the driver asked for more); a longer spoken line (a monologue you
cannot navigate).

**D18. Control words are matched by the bridge, as whole utterances, against closed lists.**
After transcription (and after the speaker check, when that runs), before the agent:

| Utterance | Effect | Phase |
|---|---|---|
| "where was I" | the recap (D19) | 1 |
| "note ‹anything›" | add the text to the drive's notes; Jarvis says "Noted" | 1 |
| "what's new" | play waiting arrivals, oldest first | 2 |
| "cancel" | stop the running turn of the current project | 2 |
| "switch to ‹project›" | change project; Jarvis says the project's name | 5 |
| "go on", "repeat", "back" | move through the current script | 6 |
| "yes" / "no" (and "ja" / "nee") | not a drive word: the requirement "Voice confirmation" of `jarvis-full-agent`, its closed lists and its 60 seconds | - |

A fixed phrase counts only as the whole utterance (normalised for case and punctuation), so
"go on and explain the config" goes to the agent. The lists are configuration, with a few
spoken alternatives each ("what was I doing" for the recap). Project names are matched
fuzzily against the bridge's project list (in `full` mode the agent host's, D52) and spoken
aliases; two close candidates are read out and the driver picks. The words work while the
agent is busy or down and cost no model call. The project names are given to
faster-whisper as hotwords, so "ratatoskr" has a chance.
Alternative considered: let the agent interpret everything. Rejected: "cancel" must not
depend on the agent, and the recap must work without it.

**D19. The recap is assembled, not generated, and only on request.** "Where was I" answers
from the journal with a fixed template of at most three sentences: the project and the last
question with its spoken answer; how many arrivals wait and whether a turn is still running;
where a reading was left ("part 3 of 7"). No agent turn: it must be instant, work when the
agent host is unreachable, and invent nothing. In a new drive it recaps the last drive.

**D20. Long turns go to the background; a lost connection loses nothing.** A turn runs to
its end whatever happens to the phone's request: the bridge keeps reading the agent host's
reply after it has answered the phone. If the turn outlives the patience window (45 s by
default, below the bridge's 170 s), the bridge answers "This is taking a while. I'll chime
when it's done." and the turn's result becomes an arrival. So does a reply whose delivery
failed (the connection dropped, or a stream broke off, S6) and a turn for a project the
driver has switched away from. A retried upload with the same turn id does not start a second
turn: it gets the result if there is one, or the background answer. "Cancel" stops the
project's running turn (readonly: the process group, as the timeout does; full: by closing
the turn's ssh connection, which the wrapper must turn into stopping `claude`; task 2.1
confirms that on the host, and until it holds "cancel" says it cannot stop that one). One
running turn per project stays the rule: a second question for a busy project gets "still
working on the last one".
When `jarvis-notifications` exists, the bridge also sends a background result as its kind
`reply` (its N10); until then the arrival waits for the next reply's `X-Jarvis-Arrivals`.
In a drive a received notification is an arrival too (its N9). Arrival text is data: capped,
filtered like spoken agent text, never passed to the agent.
Alternative considered: queue further questions behind the running turn. Rejected: answers
would come back out of step with what the driver is thinking about.

**D21. Withdrawn** (handoffs). Replaced by `jarvis-full-agent`: Jarvis starts a build,
merges or creates a repository itself, under the gate, with a read-back and a yes; the build
system accepts the identity `jarvis`. What cannot be done by voice is passed on to the
coding session (#55). "Note" stays (D18) and needs no yes: it writes only to the journal.

**D22. Withdrawn** (the agent and speech seams). The agent seam exists (the readonly and
remote adapters of `jarvis-full-agent`). A speech provider seam is not needed for the drive.

**D23. Withdrawn** (a reference web client). The native app exists (`add-android-client`).

**D24. The app in the car** (rewritten). Drive mode extends the app in `android/`; it adds no
second client. In order: the drive's tones and a retry under the same turn id (phase 3); the
project following `X-Jarvis-Project` (phase 5); the `more` tone and playback of further
parts (phase 6); then the screen off (phase 7): a foreground service that holds the
microphone and playback, and a media session so that the play/pause button of the wheel or a
headset is the control. Phase 7 starts by writing down what the parked tests of phases 3 to
6 showed (microphone, Bluetooth route, which tones were unclear) and adjusts its tasks before
building. If `jarvis-notifications` has built its foreground service by then, phase 7 uses
that one instead of a second. The rules of the app stay: no Google Play services, no
analytics, only the paired bridge; sideloaded, signed with the one key (#54).

**D25. Withdrawn from this change** (the plugin). Not about the drive, and the machine side
is now a bridge and an agent host. The roadmap phase "Plugin" stays for a change of its own.
The "last drive" command is replaced by D29.

**D26. One change, seven small phases** (rewritten). `tasks.md` names the current phase on
top; a build does that phase only, and its last task moves the marker and updates
`docs/reference/roadmap.yml`. Phases 1, 2 and 4 are the bridge (4 also the agent host);
3, 5, 6 and 7 add the app's part. Phases 5 and 6 can swap. Every phase that changes what is
heard is tried parked by Mark before it is used on the road.

**D27. Arrivals reach the phone on the next reply, and by notification when that exists.**
No event stream of its own: every reply carries `X-Jarvis-Arrivals`, and the app plays the
arrival tone when the number rose since its last reply. That costs nothing and works today,
but the driver hears of an arrival only at his next utterance. The live tone, while nobody
speaks, comes with `jarvis-notifications` (an ntfy subscription the app holds anyway), so
drive mode does not build a second always-open connection.
Alternative considered: `GET /drive/events` as server-sent events (#9's D15). Rejected: a
second long-lived connection and foreground service on the phone for what the notification
listener will do.

**D28. A read-back that arrives in the background is cancelled.** A gated call waits 60
seconds on the agent host for the next utterance of its project (D36). If the turn went to
the background, nobody heard the read-back. The bridge answers the agent host `no` at once,
as for a read-back that was not played to its end (`jarvis-streaming-speech` S6), and the
arrival says what was not done ("I did not merge pull request 21 on jarvis. Ask me again
to do it."), built from the read-back sentence. Asking again gives a fresh read-back with
the current state (a merge's checks may have changed).
Alternative considered: keep it pending until the driver plays the arrival. Rejected: the
yes would come minutes after the read-back was built, and "what's new" in between is an
utterance of its own. Open question 2.

**D29. The drive log goes to the desk.** The bridge writes each drive as Markdown on its
state volume, appended as the drive goes: per turn the time, project, transcript, spoken
reply, turn id and session id; the notes; the arrivals; what was cancelled. Full written
replies are not copied: in `full` mode they are in the agent host's turn record of that turn
id (D38), in `readonly` mode the bridge's log has them. When the drive ends, the bridge sends
the log to the agent host as a request of its own kind (`drive_log`, data only, capped at
256 KB), an addition to the wire contract like the list request (D51). The wrapper never
gives it to `claude`; it stores it owner-only as `drives/<drive id>.md` in its state
directory, kept like the records. The coding session's recurring job that takes over refused
turns (#55) also reads new drive logs: it takes the notes up as requests from Mark under its
own rules and pings him the outcome. Everything else in the log is data.
Alternatives considered: the coding session fetches logs from the bridge (it would need the
phone's token, and a route that returns them); the agent host assembles the log from its
records (it does not see control words, notes or arrivals). Open questions 3 and 4.

## Risks / Trade-offs

- **Driver distraction.** Mitigations are structural: tones instead of sentences, short
  parts, silence on a tap, nothing unprompted, no screen in phase 7. Each phase that changes
  what is heard is tried parked first; the app's how-to keeps saying "parked" until then.
- **The grammar is too stiff.** Whole-utterance matching ("carry on" is not "go on").
  Alternatives are configuration; the parked tests collect the misses.
- **Speech-to-text on names.** Hotwords and fuzzy matching help; a name that still fails
  gets a spoken alias in configuration.
- **A background turn does something irreversible unheard.** It cannot: a gated call needs
  a yes to a read-back, and D28 cancels a read-back nobody heard. Ungated work (edits,
  commits) runs in the background as it would in the foreground.
- **"Cancel" in `full` mode** depends on the wrapper stopping `claude` when its connection
  closes. Until the host check of task 2.1 passes, "cancel" says it cannot stop that turn.
- **Drive logs hold transcripts and spoken replies** and, by turn id, point at full replies.
  Owner-only on both sides, 30 days on the bridge, the records' retention on the agent host.
- **The journal is on one node-local volume** (`run-bridge-in-cluster` D6). If the node
  dies, the open drive's journal waits with it; nothing is sent elsewhere.

## Migration

`POST /turn` keeps working unchanged for clients that send none of the new headers. A turn
without `X-Jarvis-Turn-Id` gets a bridge-made id and is not deduplicated. The 170 s answer
"still running" becomes the patience window's answer, earlier. The session store keeps its
format; the journal is new files next to it.

## Open Questions

For Mark: see `proposal.md`. For the build, to settle in the phase named:
- Phase 2: how the wrapper reacts when its ssh connection closes (task 2.1).
- Phase 6: whether further parts render fast enough to play after the first without a gap.
- Phase 7: microphone and audio route over car Bluetooth; whether the notifications
  service can hold the media session too.
