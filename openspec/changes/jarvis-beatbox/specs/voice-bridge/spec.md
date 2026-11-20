## ADDED Requirements

### Requirement: Beatboxing on request
The system SHALL beatbox a reply only when the driver's own words in that turn ask for a beat, the agent's spoken line starts with a beatbox tag, the reply is not a read-back, and beatboxing is not switched off. The beats SHALL be the bridge's own, from its table; a tag that names no beat in the table SHALL get one of them at random. Every sound SHALL be synthesised or made from the bridge's existing voice, with no samples or recordings. A beat SHALL last at most 15 seconds, and the agent's own line SHALL be spoken before it. In every other case the tag SHALL NOT be spoken and the line SHALL be spoken as words; a read-back SHALL be spoken exactly as built. A notice or other fixed sentence of the bridge that comes with the reply SHALL be spoken before or after the beat.

#### Scenario: Asked for a beat
- **WHEN** Mark says "Jarvis, drop a beat" and the agent's line starts with `[beatbox: hip-hop]` and a short line
- **THEN** the phone hears the line and then the hip-hop beat, and the summary header carries the line

#### Scenario: No beat named
- **WHEN** Mark says "doe eens een beat" and the agent's line starts with `[beatbox]`
- **THEN** the phone hears one of the bridge's beats, chosen at random

#### Scenario: Not asked
- **WHEN** the agent's line starts with a beatbox tag but Mark asked something else
- **THEN** the line is spoken and the tag is not

#### Scenario: A read-back
- **WHEN** a gated call is read back in a turn in which Mark asked for a beat
- **THEN** the read-back is spoken exactly as built, never beatboxed

#### Scenario: Switched off
- **WHEN** `JARVIS_BEATBOX=0` and Mark asks for a beat
- **THEN** the line is spoken
