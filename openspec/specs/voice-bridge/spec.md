# voice-bridge Specification

## Purpose
Created by the change `add-voice-bridge`. Claude Code is currently used from the phone via Termius over SSH on a Tailscale network.

## Requirements

### Requirement: Voice turn
The system SHALL accept spoken input from the phone, transcribe it, send it to the active Claude Code session, and return a spoken reply.

#### Scenario: Normal question
- **WHEN** Mark taps to talk and asks a question about the current project
- **THEN** the bridge transcribes the audio, resumes the project's Claude Code session, and the phone plays a spoken summary of the reply

### Requirement: Session continuity
The system SHALL keep one Claude Code session per project across turns.

#### Scenario: Follow-up
- **WHEN** Mark asks a follow-up question within the same project
- **THEN** the bridge resumes the session using the stored session ID, so earlier context is kept

### Requirement: Network exposure
The bridge SHALL only be reachable over the Tailscale network and SHALL reject requests without a valid token.

#### Scenario: Request from outside the tailnet
- **WHEN** a request arrives on any interface other than Tailscale
- **THEN** it is not served

### Requirement: Spoken output
The system SHALL speak only a short summary, never code, diffs or full paths.

#### Scenario: Reply contains code
- **WHEN** Claude Code's reply includes a code block
- **THEN** only the summary is spoken and the full reply is logged on the machine

### Requirement: Approval for changes
The system SHALL NOT perform actions outside the allow-list without explicit spoken confirmation.

#### Scenario: Edit needs approval
- **WHEN** Claude Code wants to run a tool that is not on the allow-list
- **THEN** the phone asks for confirmation by voice and the action only proceeds after a spoken yes
