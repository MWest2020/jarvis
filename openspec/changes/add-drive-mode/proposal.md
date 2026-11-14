# Change: add-drive-mode

## Why

The weekend slice proved the round trip: speak, `claude -p`, hear a summary. It also showed
what the request-and-wait shape cannot do. After you stop talking there are several seconds
of silence with no sign that anything was heard. You cannot interrupt. A reply is one summary
of at most 300 characters or "check the log". A turn that takes longer than the timeout is
lost. Nothing remembers what you were doing when you get back in the car, and nothing you
said in the car is waiting for you at your desk.

The open items of the first change (approvals, a tone, project by voice, the speech and agent
interfaces, the plugin, the app) can each be built as a separate fix. This proposal instead
starts from one question: what is the car actually good for? Not for coding. You cannot see a
diff and you should not be approving tool calls at 100 km/h. The car is good for three things:
**asking** (what is the state of this, why did that fail), **listening** (a proposal, a review,
a long answer) and **directing** (build that change, note this for later). The design follows
from that.

## The idea

The unit is the **drive**, not the turn. A drive has a start (where was I), a middle (a
conversation you can interrupt, things to listen to, work you hand off) and an end (a written
log of the drive, waiting at your screen).

Four rules shape everything else:

1. **The front seat directs; it does not edit.** The agent in the voice session stays
   read-only, permanently. Anything that changes something is a *handoff*: a named action,
   defined in your own configuration, read back in one sentence and started only after a
   spoken yes. Mark's factory (`!build <repo> <change>`) is one such handoff, wired in his own
   configuration, not in the plugin.
2. **Actions come from the driver's words, never from the agent's.** The agent reads files,
   and files can contain anything. So what the agent writes is only ever spoken. Switching
   project, starting a handoff and confirming are recognised by the bridge from the driver's
   own utterance, against closed lists.
3. **The microphone opens only on a press, and Jarvis speaks only when spoken to.** One
   control (the steering-wheel or headset button, or one large touch target). Everything that
   happens on its own (heard you, still working, there is more, something came back) is a
   tone, never a sentence. The radio or a passenger cannot say yes.
4. **Nothing is lost.** A turn that takes long, or a connection that drops in a tunnel, does
   not lose the work: the result waits as an *arrival* and a tone tells you it is there.

### A drive, start to finish

Mark gets in, presses the wheel button and says "where was I". Jarvis: "Wordsworth. You asked
why the index test fails; the fixture is stale. One thing came back while you were away."
He says "what's new": the build he started yesterday finished and its pull request is open.
He says "switch to jarvis", hears "Jarvis", and asks for the drive-mode proposal to be read
to him. He hears the gist in one sentence and a soft tone that means there is more. "Go on"
plays the rest in short parts; a press stops it mid-sentence, he asks a question about one
decision, gets the answer, and "go on" picks the reading up where it was cut. Then: "build
jarvis drive mode". Jarvis: "Start a build of jarvis, change add-drive-mode. Press and say
yes." Press, "yes", "Started." He says "note: check the token rotation tonight". At his desk
the drive log has all of it: the questions, the full written answers, the note, the build
with its link, and the session id to continue the same conversation at the keyboard.

## What Changes

- **Live turn.** The reply comes back as a stream of events instead of one WAV: heard,
  working, spoken parts as they are ready, done. The agent writes the spoken part first, so
  speech can start while the written detail is still being produced. Per-turn timings are
  logged. A press stops speech at once. `POST /turn` stays for HTTP Shortcuts.
- **Replies you can listen to.** A reply is a short script of spoken parts instead of one
  capped summary. The first part is always the answer in one sentence; "go on", "repeat" and
  "back" move through the rest; the position survives a stop.
- **The drive journal.** Project switching and a small set of control words are handled by
  the bridge itself. "Where was I" gives a recap built from the journal. Long turns move to
  the background and come back as arrivals. Every drive leaves a written log on the machine.
- **Handoffs.** Named actions from the user's own configuration, with read-back and a spoken
  yes. **This replaces the per-tool approval flow** that task 1.3 and the current requirement
  "Approval for changes" describe (see Open questions, 1).
- **Two seams** (D8 and task 2.3): an agent adapter interface and a speech provider
  interface. Only the Claude Code adapter and the local speech providers are built.
- **A reference web client**, served by the bridge, for the parked test (task 4.2) and for
  automated end-to-end tests. Then the **native Android app** on the same protocol, adding
  what a browser cannot do: screen off, the wheel button, Bluetooth.
- **The plugin** (D7): the machine side as an installable Claude Code plugin, pairing by QR
  code, and a CI gate that fails when anything of Mark's setup is in it.

Not in this change: where the bridge runs or how it is exposed (that is
`run-bridge-in-cluster`; the requirement "Network exposure" is not touched here), file edits
by voice, a wake word or an always-open microphone, hosted speech providers, the Codex and
local-model adapters, Android Auto, and a Dutch voice.

## Phases

Each phase ships on its own and is one factory build (architect, builder, reviewer,
security). `tasks.md` names the current phase on top, the way the weekend slice did.

| # | Phase | You can then |
|---|---|---|
| 1 | Live turn | hear that you were heard, hear the answer start sooner, stop it (proven with curl) |
| 2 | Reference client and parked test | use it parked from the phone's browser with one button; task 4.2 is run |
| 3 | The drive | switch project by voice, listen to long replies, ask "where was I", read the drive log |
| 4 | Handoffs | start a build or leave a note by voice, with read-back and a yes; hear when it comes back |
| 5 | Plugin | install the machine side on another machine with nothing of Mark's setup in it |
| 6 | Android app | do all of it with the screen off, from the wheel button |

## Impact

- Capability `voice-bridge`: three requirements modified (Voice turn, Spoken output, Approval
  for changes), nine added.
- New capabilities: `drive-journal`, `handoffs`, `phone-client`, `plugin`.
- Code: `bridge/` (phases 1, 3, 4), new `client/` (phases 2 and 6) and `plugin/` (phase 5).
  Both paths are already under the `docs-gates` drift check.
- One new dependency at most in the bridge (none is needed for the transport, see D15). The
  Android app is a new toolchain; it builds in GitHub Actions, not on the builder's machine.
- Touchpoints with `run-bridge-in-cluster`, for that change to handle, not this one: the
  state directory now holds the journal and drive logs and needs to persist; streamed
  responses and server-sent events must pass the Tailscale ingress unbuffered; handoff
  commands need their tools in the image.
- Outside this repository: Mark's `build` handoff needs ratatoskr to accept a build request
  from the session `jarvis` and, for arrivals, to report back (Open questions, 6).

## Open questions for Mark

Each has a default. If you merge this without answering, the default is what gets built.

1. **Should a spoken yes ever unlock file edits or commands inside the voice session?** That
   is what task 1.3 and the current spec say. This proposal says no: the voice session stays
   read-only and changes only happen through handoffs (D11).
   *Default: no. The requirement "Approval for changes" is rewritten accordingly.*
2. **One change built in six phases, or one change per phase?** One change keeps the design
   in one place; the cost is that the living spec only updates when the whole change is
   archived.
   *Default: one change, with the current phase named on top of `tasks.md`. If the Android
   phase is still far off when phase 5 is done, split it into its own change then.*
3. **Language.** Kokoro has no Dutch voice, and the rule "not Piper" removes the usual local
   Dutch one. Speech in and out stays English; "ja" and "nee" count as yes and no.
   *Default: English only in this change. Dutch is a later change, once there is a local
   Dutch voice you like.*
4. **A web client before the native app?** It lets the factory test end to end without an
   Android toolchain and makes the parked test possible two phases earlier. It needs the
   HTTPS name that Tailscale gives the bridge (`tailscale serve` or the operator), because a
   browser only opens the microphone on HTTPS.
   *Default: yes.*
5. **How the Android app reaches the phone.** A signed APK built by CI and sideloaded; no
   Play Store listing, no Google services in the app, no Android Auto.
   *Default: sideloaded APK from the private repository's releases.*
6. **The factory side of the `build` handoff.** The plugin only runs the command your
   configuration names. For Mark's setup that needs a small ratatoskr change (accept the
   request from session `jarvis`; post an arrival when the build finishes).
   *Default: odin proposes that change in ratatoskr when phase 4 starts. Until it lands, the
   handoff says "Started" and the result does not come back by voice.*
7. **Press to talk only?** No wake word and no open microphone in this change: every
   utterance, including a yes, follows a press (D13). It is hands on the wheel, not
   button-free.
   *Default: yes. A wake word is a separate change after phase 6, and only on-device.*
