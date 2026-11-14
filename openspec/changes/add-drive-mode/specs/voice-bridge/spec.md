## MODIFIED Requirements

### Requirement: Voice turn
The system SHALL accept spoken input from the phone, transcribe it, send it to the active Claude Code session, and return a spoken reply. The reply SHALL be delivered as a stream of events, and each spoken part SHALL be sent as soon as it is rendered, without waiting for the rest of the reply. The single-response endpoint of the first version SHALL keep working.

#### Scenario: Normal question
- **WHEN** Mark presses to talk and asks a question about the current project
- **THEN** the bridge transcribes the audio, resumes the project's Claude Code session, and the phone plays the spoken parts of the reply

#### Scenario: Speech starts before the reply is complete
- **WHEN** the agent has written its first spoken part and is still producing the written detail
- **THEN** the bridge sends that part as audio before the turn's final event

#### Scenario: A retried upload
- **WHEN** the client sends the same turn id twice because the first upload seemed to fail
- **THEN** the agent turn runs once and both requests get its result

#### Scenario: The first client still works
- **WHEN** a request arrives on the single-response endpoint used by HTTP Shortcuts
- **THEN** it gets one audio response with the first spoken part of the reply

### Requirement: Spoken output
The system SHALL speak a reply as a script of short spoken parts, never code, diffs or full paths. The first part SHALL be the answer in one sentence. The bridge SHALL enforce a maximum number of parts and a maximum length per part, and SHALL check every part on its own for code and paths. The full reply SHALL be logged on the machine.

#### Scenario: Reply contains code
- **WHEN** Claude Code's reply includes a code block
- **THEN** only the spoken parts are spoken and the full reply is logged on the machine

#### Scenario: One part is not speakable
- **WHEN** one spoken part contains a path or code and the others do not
- **THEN** that part is replaced by a short sentence saying it is only in the written log, and the other parts are spoken

#### Scenario: A long reply
- **WHEN** a reply has more than two spoken parts
- **THEN** the first part is played, a tone signals that there is more, and the rest is played only after the driver says "go on"

#### Scenario: Picking up a reading
- **WHEN** the driver interrupted a reply at its third part, asked something else, and then says "go on"
- **THEN** playback continues with the third part

### Requirement: Approval for changes
The agent in a voice session SHALL only have read-only tools, and no spoken confirmation SHALL widen them. An action that changes anything SHALL only be started as a handoff, after the bridge has read the resolved action back and the driver has confirmed it by voice.

#### Scenario: Edit needs approval
- **WHEN** Claude Code wants to run a tool that is not on the read-only allow-list
- **THEN** the tool does not run, whatever is said afterwards, and the spoken reply says that this needs a handoff or a screen

#### Scenario: A change by voice
- **WHEN** the driver asks for a configured handoff and says yes after the read-back
- **THEN** the handoff's command runs outside the voice session, and the voice session's tools are unchanged

## ADDED Requirements

### Requirement: Audible progress
The system SHALL let the driver hear the state of a turn without speech: that the utterance was heard, that the agent is working, that a reply has more parts, and that something failed. The client SHALL produce these tones itself, so they do not depend on the connection.

#### Scenario: Waiting for the agent
- **WHEN** the driver has finished speaking and the agent has not answered yet
- **THEN** the client plays the heard tone at once and a working tone repeats until speech starts

#### Scenario: The turn fails
- **WHEN** the agent turn fails or times out
- **THEN** the client plays the problem tone and one short spoken sentence, never silence

### Requirement: Press to talk
The microphone SHALL only be open between a press of the client's control and the end of that utterance. Playback SHALL stop when the microphone opens.

#### Scenario: Speech in the car without a press
- **WHEN** a passenger or the radio speaks and the control has not been pressed
- **THEN** nothing is recorded or sent

#### Scenario: End of an utterance
- **WHEN** the driver stops speaking, presses again, or 30 seconds have passed
- **THEN** the microphone closes and the audio is sent

### Requirement: Interruption
The driver SHALL be able to stop Jarvis speaking with one press, and the client SHALL silence playback within 200 milliseconds of it. Stopping speech SHALL NOT cancel the work or discard the rest of the reply. Cancelling a running turn SHALL be a separate, spoken command.

#### Scenario: Stopping a reply
- **WHEN** the driver presses the control while a reply is playing
- **THEN** playback stops, the microphone opens, and the remaining parts stay available for "go on"

#### Scenario: Cancelling work
- **WHEN** the driver says "cancel" while the agent is working on the current project
- **THEN** the agent process and its children are stopped and Jarvis says it was cancelled

### Requirement: Speak only when spoken to
The system SHALL only speak as the direct answer to an utterance. Events that happen on their own SHALL be signalled by a tone and spoken only when the driver asks.

#### Scenario: Something finishes while driving
- **WHEN** a background turn or a handoff reports a result and the driver has not asked for it
- **THEN** the client plays the arrival tone and says nothing

### Requirement: Control words
The bridge SHALL recognise a fixed set of control utterances itself, before the agent sees the transcript, and SHALL act on a fixed phrase only when it is the whole utterance. Control words SHALL work while the agent is busy or unavailable. Text written by the agent SHALL never be treated as a control utterance.

#### Scenario: A control word on its own
- **WHEN** the driver says "repeat"
- **THEN** the current spoken part is played again and no agent turn runs

#### Scenario: The same words inside a question
- **WHEN** the driver says "go on and explain the configuration"
- **THEN** the utterance goes to the agent as a normal question

#### Scenario: The agent writes a control phrase
- **WHEN** the agent's reply contains the words "switch to" followed by a project name
- **THEN** the project does not change

### Requirement: Project selection by voice
The driver SHALL be able to change the active project by voice. The bridge SHALL match the spoken name against the configured project names and their spoken aliases only, and SHALL speak the chosen name back.

#### Scenario: Switching
- **WHEN** the driver says "switch to" followed by a configured project name
- **THEN** that project becomes active, Jarvis says its name, and the next question resumes that project's session

#### Scenario: Two close matches
- **WHEN** the spoken name matches two configured projects about equally well
- **THEN** Jarvis reads out both and switches only after the driver picks one

#### Scenario: No match
- **WHEN** the spoken name matches no configured project
- **THEN** the active project stays the same and Jarvis says it did not find one

### Requirement: Turn timings
The bridge SHALL record for every turn when the audio arrived, when the transcript was ready, when the first spoken part was ready, when its audio was sent, and when the turn ended, and SHALL write these to the log and to the turn's final event.

#### Scenario: Reading the timings
- **WHEN** a turn completes
- **THEN** its final event and the log both contain the five timestamps

### Requirement: Agent adapter
The bridge SHALL talk to the coding agent through one adapter interface that starts a turn, yields the reply as a stream of events, resumes a session, and cancels. The bridge core SHALL NOT depend on a specific agent.

#### Scenario: A turn without Claude Code
- **WHEN** the bridge is configured with a test adapter that returns a fixed reply
- **THEN** a complete voice turn runs, with no Claude Code installed

### Requirement: Speech providers
The bridge SHALL use speech-to-text and text-to-speech through one interface each, chosen in configuration. The default providers SHALL run on the user's own machine. A provider that sends audio or text elsewhere SHALL only be active when explicitly configured, and the bridge SHALL state in its log at start which providers are active and whether audio leaves the machine.

#### Scenario: Default configuration
- **WHEN** the bridge starts without any speech provider configured
- **THEN** it uses the local providers and logs that no audio leaves the machine

#### Scenario: Vocabulary hints
- **WHEN** a turn is transcribed
- **THEN** the configured project names, aliases and handoff phrases are passed to the speech-to-text provider as hints
