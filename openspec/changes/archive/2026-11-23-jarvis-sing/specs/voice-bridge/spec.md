## ADDED Requirements

### Requirement: Singing on request
The system SHALL sing a reply only when the driver's own words in that turn ask for a song, the agent's spoken line starts with a tag naming a tune in the bridge's table, the reply is not a read-back, and singing is not switched off. The melodies SHALL be the bridge's own, in the public domain; the lyrics SHALL be the agent's. A song SHALL last at most 20 seconds and SHALL be rendered with the bridge's existing voice. In every other case the tag SHALL NOT be spoken and the lyrics SHALL be spoken as words; a read-back SHALL be spoken exactly as built. A notice or other fixed sentence of the bridge that comes with a sung reply SHALL be spoken, before or after the song, and SHALL NOT stop the reply from being sung.

#### Scenario: Asked to sing
- **WHEN** Mark says "Jarvis, sing a song" and the agent's line starts with `[sing: twinkle]` and its lyrics
- **THEN** the phone hears the lyrics sung to the tune, and the summary header carries the lyrics as words

#### Scenario: Asked to sing in a new session
- **WHEN** Mark asks for a song and the agent host starts a new session for the turn
- **THEN** Jarvis says "I started a new session for this project." and then sings

#### Scenario: Not asked
- **WHEN** the agent's line starts with a tag but Mark asked something else
- **THEN** the lyrics are spoken and the tag is not

#### Scenario: A read-back
- **WHEN** a gated call is read back in a turn in which Mark asked for a song
- **THEN** the read-back is spoken exactly as built, never sung

#### Scenario: Switched off
- **WHEN** `JARVIS_SING=0` and Mark asks for a song
- **THEN** the lyrics are spoken
