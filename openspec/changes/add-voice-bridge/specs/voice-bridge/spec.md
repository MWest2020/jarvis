## ADDED Requirements

<!-- Draft by Claude (2026-11-14), derived from design.md; the pasted text did not include this file. Mark checks it. -->

### Requirement: Spoken turn over the bridge
The bridge SHALL accept a recorded utterance from the phone, transcribe it, send the text to the coding agent, and return a spoken reply.

#### Scenario: A question gets a spoken answer
- **WHEN** the phone sends an utterance with a valid token over the tailnet
- **THEN** the bridge returns audio of a short spoken summary of the agent's reply

### Requirement: One session per project
The bridge SHALL resume the same agent session for a project on each turn, using the session ID from the previous reply.

#### Scenario: A follow-up keeps context
- **WHEN** a second utterance for the same project arrives
- **THEN** the agent is called with the previous session ID and answers in that context

### Requirement: Reachable only by the owner
The bridge SHALL listen only on the configured private interface (Tailscale by default) and SHALL refuse a request without the shared token.

#### Scenario: No token
- **WHEN** a request arrives without the token
- **THEN** the bridge refuses it and runs nothing

### Requirement: Spoken output is a summary
The bridge SHALL send only the spoken summary to text-to-speech and SHALL keep the full reply in a log on the machine.

#### Scenario: A reply with code
- **WHEN** the agent's reply contains code or paths
- **THEN** only the summary is spoken and the full reply is in the machine log

### Requirement: No unattended permission bypass
Headless agent runs SHALL use an explicit tool allow-list; an action outside it SHALL be announced by voice and run only after a spoken yes, or be deferred.

#### Scenario: A file-changing action
- **WHEN** the agent wants to run a tool that is not on the allow-list
- **THEN** the bridge speaks what it wants to do and waits for a spoken yes, or defers it
