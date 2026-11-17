## ADDED Requirements

### Requirement: Streamed playback
The app SHALL ask the bridge for a streamed reply and SHALL start playing it while it still arrives, after a short buffer. It SHALL play a WAV reply as before when the bridge answers with one. A tap SHALL stop a streamed reply as it stops a WAV reply. When a streamed reply breaks off, the app SHALL keep the summary on screen and say that the spoken reply broke off. When a read-back (a reply that waits for a spoken yes) does not play to its end, the app SHALL cancel it on the bridge and say that nothing was confirmed.

#### Scenario: A bridge that streams
- **WHEN** the bridge answers with `audio/L16;rate=24000;channels=1`
- **THEN** the summary is shown when the headers arrive and playback starts before the reply has fully arrived

#### Scenario: A bridge that does not stream
- **WHEN** the bridge answers with `audio/wav`
- **THEN** the app plays it as before

#### Scenario: The stream breaks off
- **WHEN** the connection ends before the stream's end
- **THEN** playback stops after what arrived, the summary stays on screen, and the app says the spoken reply broke off

#### Scenario: A read-back stopped with a tap
- **WHEN** Mark taps while a read-back plays, or it breaks off or cannot be played
- **THEN** the app cancels it on the bridge, shows "Cancelled. Nothing was confirmed.", and a later yes confirms nothing
