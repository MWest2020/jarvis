## MODIFIED Requirements

### Requirement: Beatboxing on request
The system SHALL beatbox a reply only when the driver asked for a beat (his own words in that turn ask for one, or he asked for a beat in words in that conversation less than ten minutes before, for at most three such follow-ups), the agent's spoken line starts with a beatbox tag, the reply is not a read-back, and beatboxing is not switched off. The beats SHALL be the bridge's own, from its table; a tag that names no beat in the table SHALL get one of them at random. Every sound SHALL be synthesised or made from the bridge's existing voice, with no samples or recordings. A beat SHALL last at most 15 seconds, and the agent's own line SHALL be spoken before it. In every other case the tag SHALL NOT be spoken; when the tag stood first on the agent's line, that line SHALL NOT be spoken either and the bridge SHALL say a short line of its own that promises no beat. A beat that cannot be rendered SHALL be replaced by such a line too. A read-back SHALL be spoken exactly as built. A notice or other fixed sentence of the bridge that comes with the reply SHALL be spoken before or after the beat.

#### Scenario: Asked for a beat
- **WHEN** Mark says "Jarvis, drop a beat" and the agent's line starts with `[beatbox: hip-hop]` and a short line
- **THEN** the phone hears the line and then the hip-hop beat, and the summary header carries the line

#### Scenario: No beat named
- **WHEN** Mark says "doe eens een beat" and the agent's line starts with `[beatbox]`
- **THEN** the phone hears one of the bridge's beats, chosen at random

#### Scenario: Not asked
- **WHEN** the agent's line starts with a beatbox tag but Mark asked something else, and no beat was played in the last ten minutes
- **THEN** Jarvis says "Sorry, I didn't catch that you wanted a beat." and neither the tag nor the agent's line is spoken

#### Scenario: Another one
- **WHEN** a beat was played a minute ago in this conversation, Mark asks for another and the transcript comes out garbled ("Okay, du buchst im Katz."), and the agent's line starts with `[beatbox: boots-and-cats]`
- **THEN** the phone hears the line and then the boots-and-cats beat

#### Scenario: A read-back
- **WHEN** a gated call is read back in a turn in which Mark asked for a beat
- **THEN** the read-back is spoken exactly as built, never beatboxed

#### Scenario: Switched off
- **WHEN** `JARVIS_BEATBOX=0` and Mark asks for a beat
- **THEN** Jarvis says "Beatboxing is switched off."

### Requirement: Spoken output
The system SHALL speak only a short summary, never code, diffs or full paths. When the agent's summary holds code or there is none, the system SHALL speak what can be said of the summary or else of the reply, at most 160 characters, with code, paths and anything shaped like a credential left out. Only when nothing is left SHALL it say a fixed sentence, and that sentence SHALL NOT point the driver to a log or a screen.

#### Scenario: Reply contains code
- **WHEN** Claude Code's reply includes a code block
- **THEN** only the summary is spoken and the full reply is logged on the machine

#### Scenario: No summary line
- **WHEN** the agent's reply has no summary line and starts with "The cluster is healthy and there's no incident right now."
- **THEN** Jarvis says that sentence (and the next ones, up to the spoken length), never "Check the log"

#### Scenario: Nothing speakable
- **WHEN** the agent's reply is only a code block
- **THEN** Jarvis says "Done, but I couldn't put the answer into words. Ask me to say it again."
