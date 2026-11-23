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

### Requirement: Network exposure
The bridge SHALL only be reachable over the Tailscale network and SHALL reject requests without a valid token. On a workstation it listens on its Tailscale address. In a cluster it MAY listen on a private pod address, but only with an explicit opt-in, only when a tailnet-only proxy is the sole way in, and never on a wildcard or public address.

#### Scenario: Request from outside the tailnet
- **WHEN** a request arrives on any interface other than Tailscale
- **THEN** it is not served

#### Scenario: Request without a valid token
- **WHEN** a request over the tailnet has no token or a wrong one
- **THEN** the bridge answers 401 and runs no turn

#### Scenario: Private address without the opt-in
- **WHEN** the bridge is configured to listen on a private, non-Tailscale address and `JARVIS_BEHIND_TAILSCALE_PROXY` is not `1`
- **THEN** it refuses to start

#### Scenario: Behind a Tailscale proxy in a cluster
- **WHEN** the bridge runs in a pod with `JARVIS_BEHIND_TAILSCALE_PROXY=1` and listens on its pod IP
- **THEN** it logs a warning at start, it is exposed only through a tailnet-only Service without Funnel, and a NetworkPolicy admits connections to it only from the Tailscale operator's proxy for that Service

#### Scenario: Wildcard or public address with the opt-in
- **WHEN** `JARVIS_BEHIND_TAILSCALE_PROXY=1` is set and the bind address is a wildcard or a public address
- **THEN** the bridge refuses to start

#### Scenario: Connection from another namespace in the cluster
- **WHEN** any pod other than that proxy connects to the bridge's pod IP
- **THEN** the connection is dropped by the NetworkPolicy

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

### Requirement: Project list
The bridge SHALL answer `GET /projects` with the names of the projects it serves, sorted, as JSON, and never with their paths. It SHALL apply the same token check as a turn. With `JARVIS_PROJECTS=*` it SHALL serve every git checkout directly under `JARVIS_PROJECTS_ROOT`, named after its directory, and SHALL notice checkouts that appear or disappear within a short time.

#### Scenario: Listing with the token
- **WHEN** the phone sends `GET /projects` with a valid token
- **THEN** the bridge answers 200 with `{"projects": [...]}`, the names sorted, and no paths

#### Scenario: Listing without a valid token
- **WHEN** `GET /projects` arrives without a token or with a wrong one
- **THEN** the bridge answers 401 and names no project

#### Scenario: Every checkout under a root
- **WHEN** the bridge runs with `JARVIS_PROJECTS=*` and `JARVIS_PROJECTS_ROOT=/work`, and `/work` holds the git checkouts `alpha` and `bravo` and a plain directory `notes`
- **THEN** the project list is `alpha` and `bravo`, and a turn for either runs in its directory

#### Scenario: A name that is not a checkout
- **WHEN** a turn names a project that is not a checkout under the root, such as `..` or a hidden directory
- **THEN** the bridge answers 400 and runs no turn

#### Scenario: Wildcard without a root
- **WHEN** `JARVIS_PROJECTS=*` is set and `JARVIS_PROJECTS_ROOT` is missing or not a directory
- **THEN** the bridge refuses to start

### Requirement: Project sync for one owner
The project sync SHALL, when asked for every repository, clone or refresh only repositories owned by the configured account that are neither archived nor forks, SHALL never clone a repository of another owner or organisation, SHALL keep shallow copies, SHALL skip and log repositories over a size limit, and SHALL fail only when no repository is available.

#### Scenario: A repository of an organisation
- **WHEN** the token can read repositories of an organisation as well as the owner's own
- **THEN** only the owner's own repositories are cloned

#### Scenario: One clone fails
- **WHEN** one repository cannot be cloned and others can
- **THEN** the failure is logged, the others are synced, and the run succeeds

#### Scenario: The list cannot be read
- **WHEN** the GitHub API does not answer
- **THEN** the copies already on the volume are refreshed and kept, and the run fails only if there are none

#### Scenario: A repository that is too large
- **WHEN** a repository is larger than the size limit
- **THEN** it is skipped and the skip is logged with its size

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

### Requirement: Streamed reply
When a turn's request accepts `audio/L16;rate=24000;channels=1` over HTTP/1.1, the bridge SHALL send the status and every reply header as soon as the agent has answered, and SHALL then send the spoken reply as raw 16-bit PCM in a chunked body, each sentence as soon as it is rendered. Every other request SHALL get the WAV reply unchanged. A reply that asks for a spoken yes SHALL be rendered whole, exactly as built, before its headers are sent. An error after the headers SHALL end the body without its terminating chunk. A read-back reply SHALL carry the read-back's id, and the bridge SHALL cancel that pending read-back on the agent host when the phone reports, with the token, that it did not play it to its end; the report SHALL be idempotent. The bridge SHALL log the time to the first audio byte.

#### Scenario: A client that asks for the stream
- **WHEN** the phone sends a turn with `Accept: audio/L16;rate=24000;channels=1`
- **THEN** the headers, the summary among them, arrive before any audio, and the first sentence arrives before the second is rendered

#### Scenario: A client that does not ask
- **WHEN** a turn arrives without that `Accept` value, or over HTTP/1.0, or the bridge runs with `JARVIS_STREAM_REPLIES=0`
- **THEN** the bridge answers `audio/wav` with a `Content-Length`, exactly as before

#### Scenario: A read-back
- **WHEN** the reply is a read-back that waits for a spoken yes
- **THEN** it is rendered whole with the same words and order as built before any byte is sent, and a rendering error is answered 500 with nothing pending

#### Scenario: The phone did not play a read-back to its end
- **WHEN** the phone sends `POST /turn/cancel` with the token, the project and the read-back's id from `X-Jarvis-Confirmation-Id`
- **THEN** the bridge answers `no` to the agent host for that pending call and nothing stays pending; a second cancel, or one for an id that is not pending, changes nothing

#### Scenario: Rendering fails mid-stream
- **WHEN** Kokoro fails after the first sentence was sent
- **THEN** the body ends without its terminating chunk and the connection is closed
