## ADDED Requirements

### Requirement: Drive tones
The app SHALL signal, with tones made on the phone, that a reply has further parts, that an arrival waits, and that a turn failed or the bridge cannot be reached, each with its own tone and next to the existing tones for a sent recording and for waiting. The app SHALL play the arrival tone when the number of waiting arrivals the bridge reports has risen since its previous reply.

#### Scenario: No connection
- **WHEN** the driver starts a turn and the bridge cannot be reached
- **THEN** the app plays the problem tone

#### Scenario: Something came back
- **WHEN** a reply reports more waiting arrivals than the reply before it
- **THEN** the app plays the arrival tone

### Requirement: No silent failure
The app SHALL send a turn id with every recording and SHALL retry a failed upload once within ten seconds under the same id. When the retry fails, the app SHALL discard the recording and play the problem tone, and SHALL NOT send it later.

#### Scenario: A brief gap
- **WHEN** an upload fails and the connection is back within ten seconds
- **THEN** the turn runs once and the driver hears the reply

#### Scenario: A long gap
- **WHEN** the connection stays away for longer than ten seconds
- **THEN** the recording is discarded, the problem tone plays, and nothing is sent later

### Requirement: Project follows the bridge
When a reply names the active project, the app SHALL select that project and send the next turn for it.

#### Scenario: Switched by voice
- **WHEN** the driver switched project by voice and asks the next question
- **THEN** the question goes to the new project without touching the screen

### Requirement: Listening in parts
The app SHALL play the tone for "there is more" after a reply that has further parts, and SHALL stop playback at once on a tap or press without discarding what remains.

#### Scenario: Stopping a reading
- **WHEN** the driver taps while a part is playing
- **THEN** playback stops and the remaining parts stay available for "go on"

### Requirement: Screen off and the wheel button
The app SHALL be usable for a whole drive with the screen off, and SHALL take the play/pause button of the car or a headset as its control.

#### Scenario: Screen off
- **WHEN** the phone's screen is off and the driver presses play/pause on the steering wheel
- **THEN** a turn starts as with a tap on the screen

#### Scenario: A whole drive without the screen
- **WHEN** the driver asks for the recap, asks questions, listens to a long reply, switches project and answers a read-back
- **THEN** every step is done with the button and by voice

### Requirement: Parked test before driving
Each phase that changes what the driver hears or does SHALL be tried in a parked car, with the results recorded in the documentation, before it is used while driving. Until then the documentation SHALL say that it is for use while parked.

#### Scenario: A new phase is deployed
- **WHEN** a phase that changes the spoken interaction is deployed
- **THEN** its parked-test checklist is filled in before its first use on the road
