## ADDED Requirements

### Requirement: Send at the end of speech
When a recording was started with a tap and the setting "Send when I stop talking" is on, the app SHALL stop the recording and send it once the configured silence (default 3 s, 1.5 to 6 s) follows at least 0.6 s of speech, measured on the phone from the loudness of the recording against a noise floor taken from its start. The app SHALL NOT send a recording without speech by itself: a shorter sound SHALL be set aside with a note while the app keeps listening, a recording in which no speech counts for 10 s SHALL be dropped with a note, and at the recording limit a hands-free recording whose speech never paused SHALL be dropped, not sent. For the first recording after a read-back played to its end, 0.3 s of speech SHALL be enough, but never while a read-back cancel is still due. While silence is being counted the app SHALL show how long until it sends, and a tap SHALL still send at once. While the control is held the app SHALL NOT send by itself. A send SHALL go through the same steps as a tapped send, the read-back cancel first. The measurement SHALL need no new permission and no library outside AndroidX and Kotlin, and SHALL send nothing anywhere.

#### Scenario: A question, then silence
- **WHEN** Mark taps the control, asks a question and stops talking
- **THEN** a countdown shows, and after the configured silence the recording is sent without a second tap

#### Scenario: A cough
- **WHEN** Mark taps the control and only coughs
- **THEN** nothing is sent, the screen says only a short sound was heard, and the recording is dropped after 10 s without speech

#### Scenario: Thinking before speaking
- **WHEN** Mark taps the control, is silent for five seconds and then asks a question
- **THEN** the question is sent after the silence that follows it

#### Scenario: A spoken yes
- **WHEN** a read-back has played to its end and Mark taps the control and says "yes"
- **THEN** the yes is sent by itself after the silence

#### Scenario: Holding the control
- **WHEN** Mark holds the control and pauses longer than the silence while holding it
- **THEN** nothing is sent until he lets go

#### Scenario: The setting is off
- **WHEN** "Send when I stop talking" is off
- **THEN** a tapped recording is sent only on the second tap, as before

#### Scenario: A read-back cancel that fails
- **WHEN** a read-back cancel is still queued and fails again as a recording is sent by itself
- **THEN** the recording is not uploaded and the app says why, as for a tapped send
