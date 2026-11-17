## ADDED Requirements

### Requirement: Control words
The bridge SHALL recognise a fixed set of control utterances itself, after transcription and before the agent sees the transcript, and SHALL act on a fixed phrase only when it is the whole utterance. Control words SHALL work while the agent is busy or unavailable and SHALL cost no agent turn. Text written by the agent, arrival text and file content SHALL never be treated as a control utterance. An affirmation or refusal of a pending read-back SHALL remain governed by the requirement "Voice confirmation" and SHALL NOT be a control word.

#### Scenario: A control word on its own
- **WHEN** the driver says "where was I"
- **THEN** the recap is spoken and no agent turn runs

#### Scenario: The same words inside a question
- **WHEN** the driver says "go on and explain the configuration"
- **THEN** the utterance goes to the agent as a normal question

#### Scenario: The agent writes a control phrase
- **WHEN** the agent's reply contains the words "switch to" followed by a project name
- **THEN** the project does not change

### Requirement: Speak only when spoken to
The system SHALL only speak as the direct answer to an utterance. Results, arrivals and notifications that happen on their own SHALL be signalled by a tone and spoken only when the driver asks.

#### Scenario: Something finishes while driving
- **WHEN** a background turn finishes and the driver has not asked for it
- **THEN** nothing is spoken until the driver asks what is new

### Requirement: Cancelling a turn
The driver SHALL be able to stop the running turn of the current project by saying "cancel". Stopping playback SHALL NOT cancel the turn. When the system cannot stop a running turn, it SHALL say so and the result SHALL still become an arrival.

#### Scenario: Cancelling work
- **WHEN** the driver says "cancel" while the agent works on the current project
- **THEN** the agent stops, Jarvis says it was cancelled, and no arrival follows

#### Scenario: A turn that cannot be stopped
- **WHEN** the driver says "cancel" for a turn the bridge has no way to stop
- **THEN** Jarvis says it cannot stop that one and will chime when it is done

### Requirement: Project selection by voice
The driver SHALL be able to change the active project by voice. The bridge SHALL match the spoken name against the projects it lists and their configured spoken aliases only, SHALL speak the chosen name back, and SHALL tell the client which project is now active.

#### Scenario: Switching
- **WHEN** the driver says "switch to" followed by a listed project name
- **THEN** that project becomes active, Jarvis says its name, the app shows it, and the next question goes to that project

#### Scenario: Two close matches
- **WHEN** the spoken name matches two projects about equally well
- **THEN** Jarvis reads out both and switches only after the driver picks one

#### Scenario: No match
- **WHEN** the spoken name matches no listed project
- **THEN** the active project stays the same and Jarvis says it did not find one

### Requirement: Replies in parts
A reply MAY carry further spoken parts after its spoken line. The bridge SHALL enforce a maximum number of parts and a maximum length per part, SHALL check every part on its own like the spoken line, and SHALL replace a part that fails with a short sentence saying it is only in the written log. Further parts SHALL be played only when the driver asks, and the listening position SHALL survive an interruption, a question in between and a reconnect. A read-back SHALL NOT have further parts.

#### Scenario: A long reply
- **WHEN** a reply has further parts
- **THEN** the spoken line is played, the client signals that there is more, and the next part is played only after the driver says "go on"

#### Scenario: Picking up a reading
- **WHEN** the driver stopped a reading at its third part, asked something else, and then says "go on"
- **THEN** playback continues with the third part

#### Scenario: One part is not speakable
- **WHEN** one further part contains a path or code and the others do not
- **THEN** that part is replaced by a short sentence saying it is only in the written log, and the other parts are spoken
