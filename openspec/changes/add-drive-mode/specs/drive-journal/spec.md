## ADDED Requirements

### Requirement: Drive journal
The bridge SHALL keep a journal per drive that records each turn, the listening position, notes, handoffs and arrivals. A new drive SHALL start with the first event after a configurable period without one. The journal SHALL be stored on the machine in files only the owner can read, and SHALL be deleted after a configurable retention period.

#### Scenario: Back after a stop
- **WHEN** the client reconnects twenty minutes after the last turn
- **THEN** the same drive continues and its journal is intact

#### Scenario: The next morning
- **WHEN** the first utterance comes after a night without events
- **THEN** a new drive starts and the previous drive's journal stays available

### Requirement: Recap on request
The system SHALL answer "where was I" from the journal with at most three short sentences: the active project with the last question and its one-sentence answer, what is waiting or still running, and where a reading was left. The recap SHALL NOT require an agent turn and SHALL NOT be spoken unless asked for.

#### Scenario: Asking for the recap
- **WHEN** the driver says "where was I" after a stop
- **THEN** Jarvis names the project, the last question and its answer, and says how many arrivals are waiting

#### Scenario: The agent is unreachable
- **WHEN** the driver says "where was I" while the coding agent cannot be started
- **THEN** the recap is still spoken

### Requirement: Background turns and arrivals
A turn SHALL run to its end on the machine regardless of the state of the connection. When a turn outlasts the patience window, or its connection is lost, or the driver has switched project, its result SHALL be stored as an arrival. Arrivals SHALL be announced by a tone and spoken, oldest first, when the driver asks what is new. There SHALL be at most one running turn per project.

#### Scenario: A slow turn
- **WHEN** a turn is still running after the patience window
- **THEN** Jarvis says it will chime when it is done, the stream ends, and the result later becomes an arrival

#### Scenario: A tunnel
- **WHEN** the connection drops during a turn and returns a minute later
- **THEN** the client receives the arrival tone and "what's new" plays the result

#### Scenario: A second question for a busy project
- **WHEN** the driver asks a question while that project's turn is still running
- **THEN** Jarvis says it is still working on the last one and does not start a second turn

### Requirement: Notes
The driver SHALL be able to leave a note by voice. A note SHALL be stored in the journal only and SHALL NOT be sent to the agent or to any other system.

#### Scenario: Leaving a note
- **WHEN** the driver says "note" followed by a sentence
- **THEN** the sentence is added to the drive's notes and Jarvis says "Noted"

### Requirement: Drive log
The bridge SHALL write each drive as a readable document on the machine, appended as the drive goes: every question as transcribed, the full written reply, the notes, the handoffs with their outcome, the arrivals, and the session id per project so the conversation can be continued at a screen.

#### Scenario: At the desk
- **WHEN** Mark opens the log of this morning's drive
- **THEN** he finds the full written reply to each question he asked and the session id to resume

#### Scenario: The bridge stops mid-drive
- **WHEN** the bridge process is restarted during a drive
- **THEN** everything up to the last completed event is in the drive log
