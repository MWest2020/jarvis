## MODIFIED Requirements

### Requirement: Session continuity
The system SHALL keep one Claude Code session per project across turns, in each agent mode separately. In `full` mode the agent host SHALL keep the session ids itself, SHALL accept none from the bridge, and SHALL use a stored id only when it is a well-formed session id, passed so that it cannot be read as an option. In `full` mode the agent host SHALL start a new session for a project, and the driver SHALL be told so, when the project's last turn ended longer ago than a configured idle time (60 minutes by default; the setting can switch this off). When the driver asks to close the conversation, the agent SHALL say goodbye and mark its reply so, the mark SHALL never be spoken, and the agent host SHALL then start the project's next turn in a new session. The mark SHALL act on the turn's own project only and SHALL carry no argument.

#### Scenario: Follow-up
- **WHEN** Mark asks a follow-up question within the same project, within the idle time
- **THEN** the turn resumes that project's session for the current mode using the stored session ID, so earlier context is kept

#### Scenario: Two modes, two conversations
- **WHEN** a project has had turns in `readonly` mode and in `full` mode
- **THEN** a `full` turn resumes the agent host's session and a `readonly` turn the bridge's, never each other's

#### Scenario: A stored session id that is an option
- **WHEN** the stored session id for a project is not a well-formed id, for example `--dangerously-skip-permissions`
- **THEN** it is discarded, no such option reaches the command line, and the turn starts a new session

#### Scenario: After an hour idle
- **WHEN** Mark asks something in a project whose last `full` turn ended more than the idle time ago
- **THEN** the turn starts a new session, and Jarvis says "I started a new session for this project." before the answer

#### Scenario: Closing the conversation
- **WHEN** Mark says "that's all" at the end of a finished request
- **THEN** Jarvis says goodbye, nothing of the mark is spoken, and the project's next turn starts a new session without a notice

#### Scenario: A close that names something else
- **WHEN** the agent's reply ends with a close mark followed by another project's name
- **THEN** it is not a close, and no session of any project is dropped

#### Scenario: A close while a confirmation is pending
- **WHEN** the agent marks its reply as closing while a gated call waits for the driver's yes
- **THEN** the session is kept, and the confirming turn resumes it
