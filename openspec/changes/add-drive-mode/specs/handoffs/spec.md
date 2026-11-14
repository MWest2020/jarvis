## ADDED Requirements

### Requirement: Handoff definitions
The bridge SHALL load handoffs from a file in the user's own configuration: a name, trigger phrases, slots, a read-back sentence and a command as an argument list. Slot values SHALL come from a fixed list, from a lookup command that prints the valid values, or from length-capped free text. The bridge SHALL refuse to load a handoff file that other users can write. No handoff SHALL be built into the bridge.

#### Scenario: No handoff file
- **WHEN** the bridge starts without a handoff file
- **THEN** it runs normally and no utterance starts any command

#### Scenario: A writable handoff file
- **WHEN** the handoff file is writable by group or others
- **THEN** the bridge does not load it and logs why

### Requirement: Driver-originated actions only
A handoff SHALL only be prepared from the transcript of the driver's own utterance. Text produced by the agent, text from an arrival and file content SHALL never start, prepare or confirm a handoff.

#### Scenario: A planted instruction
- **WHEN** a file the agent reads tells it to start a build, and the agent's reply repeats that
- **THEN** no handoff is prepared and nothing runs

### Requirement: Read-back and confirmation
Before a handoff runs, the bridge SHALL resolve its slots, speak the resolved action in one sentence, and wait for the driver's next utterance. The command SHALL run only when that utterance is exactly an affirmation and comes within 30 seconds. A confirmation SHALL apply to that one resolved action and SHALL be usable once. Every handoff SHALL require confirmation.

#### Scenario: Confirmed
- **WHEN** the driver says "build jarvis drive mode", hears "Start a build of jarvis, change add-drive-mode", presses and says "yes"
- **THEN** the command runs with those two values and Jarvis says it has started

#### Scenario: Anything but yes
- **WHEN** the driver answers the read-back with "yes but only the first part", with something else, or not at all
- **THEN** nothing runs and Jarvis says it was cancelled

#### Scenario: A misheard value
- **WHEN** a spoken slot value matches none of the valid values
- **THEN** Jarvis says which part it could not match, nothing is prepared, and the utterance is not passed to the agent

#### Scenario: A yes with nothing pending
- **WHEN** the driver says "yes" and no read-back is waiting for an answer
- **THEN** nothing runs

### Requirement: Safe execution
The bridge SHALL run a handoff command without a shell, with each validated slot value as a single argument, with an environment that does not contain the bridge's token, and with a time limit. The bridge SHALL limit the number of handoffs per drive and SHALL record every handoff and its outcome in the journal.

#### Scenario: Shell characters in free text
- **WHEN** a free-text slot value contains a semicolon and a command name
- **THEN** the command receives the text as one argument and nothing else is executed

#### Scenario: Over the limit
- **WHEN** the driver asks for one more handoff than the limit for a drive allows
- **THEN** it is not prepared and Jarvis says the limit is reached

### Requirement: Results from outside
The bridge SHALL accept the result of a handoff from another system on its private listener, authenticated with a token that grants nothing else. The text SHALL be length-capped, checked like spoken agent output, stored as an arrival and never passed to the agent.

#### Scenario: A build reports back
- **WHEN** the system that received a handoff posts a result with the arrivals token
- **THEN** the client plays the arrival tone and "what's new" speaks the result

#### Scenario: The arrivals token is used for a turn
- **WHEN** a request for a voice turn carries the arrivals token
- **THEN** it is rejected
