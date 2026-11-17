## ADDED Requirements

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
