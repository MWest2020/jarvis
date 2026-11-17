## ADDED Requirements

### Requirement: Drive journal
The bridge SHALL keep a journal per drive that records each turn (time, project, transcript, spoken reply, turn id and session id), the notes, the arrivals and the listening position. A new drive SHALL start with the first event after a configurable period without one. The journal SHALL be stored on the bridge's state volume in files only the owner can read, and SHALL be deleted after a configurable retention period.

#### Scenario: Back after a stop
- **WHEN** the driver speaks twenty minutes after the last turn
- **THEN** the same drive continues and its journal is intact

#### Scenario: The next morning
- **WHEN** the first utterance comes after a night without events
- **THEN** a new drive starts and the previous drive's journal stays available

#### Scenario: The bridge restarts
- **WHEN** the bridge process is restarted during a drive
- **THEN** everything up to the last completed event is still in the journal

### Requirement: Recap on request
The system SHALL answer "where was I" from the journal with at most three short sentences: the active project with the last question and its spoken answer, what is waiting or still running, and where a reading was left. The recap SHALL NOT require an agent turn and SHALL NOT be spoken unless asked for.

#### Scenario: Asking for the recap
- **WHEN** the driver says "where was I" after a stop
- **THEN** Jarvis names the project, the last question and its answer, and says how many arrivals are waiting

#### Scenario: The agent host is unreachable
- **WHEN** the driver says "where was I" while no agent turn can be started
- **THEN** the recap is still spoken

### Requirement: Notes
The driver SHALL be able to leave a note by voice. A note SHALL be stored in the journal and the drive log only, and SHALL NOT be sent to the agent in a turn.

#### Scenario: Leaving a note
- **WHEN** the driver says "note" followed by a sentence
- **THEN** the sentence is added to the drive's notes and Jarvis says "Noted"

### Requirement: Retried turns run once
A client MAY send its own id for a turn. The bridge SHALL run a turn id at most once: a request that repeats the id of a running or finished turn SHALL get that turn's result, or the answer that it is still running, and SHALL NOT start a second turn. A request without an id SHALL be handled as before.

#### Scenario: An upload that seemed to fail
- **WHEN** the phone sends the same turn id twice because the first upload seemed to fail
- **THEN** the agent turn runs once and the second request gets its reply

### Requirement: Background turns and arrivals
A turn SHALL run to its end regardless of the state of the phone's connection. When a turn outlasts the patience window, when its reply could not be delivered in full, or when the driver has switched to another project, its result SHALL be stored as an arrival. Every reply SHALL state how many arrivals of the drive wait. Arrivals SHALL be spoken, oldest first, when the driver asks what is new, and not before. Arrival text SHALL be length-capped, checked like spoken agent text and never passed to the agent. There SHALL be at most one running turn per project.

#### Scenario: A slow turn
- **WHEN** a turn is still running after the patience window
- **THEN** Jarvis says it will chime when it is done, the request ends, and the result later becomes an arrival

#### Scenario: A tunnel
- **WHEN** the connection drops during a turn and the driver speaks again a minute later
- **THEN** that reply says an arrival waits, and "what's new" plays the result

#### Scenario: A second question for a busy project
- **WHEN** the driver asks a question while that project's turn is still running
- **THEN** Jarvis says it is still working on the last one and does not start a second turn

### Requirement: No read-back waits unheard
When a turn whose reply is a read-back of a gated action has gone to the background, the bridge SHALL cancel the pending action at once, as for a read-back that was not played to its end, and SHALL store an arrival that says which action was not done. Such an arrival SHALL NOT be confirmable.

#### Scenario: A merge read back in the background
- **WHEN** a background turn ends with the read-back of a merge
- **THEN** the merge is cancelled on the agent host, "what's new" says it was not done and can be asked for again, and a "yes" after it releases nothing

### Requirement: Drive log at the desk
The bridge SHALL write each drive as a readable document, appended as the drive goes: each question as transcribed, the spoken reply, the turn id and session id, the notes, the arrivals and what was cancelled. In `full` mode the bridge SHALL deliver the log of a finished drive to the agent host as data, which SHALL store it owner-only and SHALL NOT pass it to the agent as a prompt.

#### Scenario: At the desk
- **WHEN** the coding session opens the log of this morning's drive on the agent host
- **THEN** it finds each question, its turn id to read the full reply from the turn record, the session id to resume, and the notes

#### Scenario: Read-only mode
- **WHEN** the bridge runs in `readonly` mode
- **THEN** the drive log is kept on the bridge only
