## ADDED Requirements

### Requirement: One control, no screen
A client SHALL be fully usable with one control and without looking at or reading from a screen. While in use it SHALL show no text that needs reading.

#### Scenario: A whole drive without the screen
- **WHEN** the driver asks questions, listens to a long reply, switches project and confirms a handoff
- **THEN** every step is done with the one control and by voice

### Requirement: Tones
A client SHALL play the same six tones for the same events (listening, heard, working, more, arrival, problem) and SHALL produce them locally.

#### Scenario: No connection
- **WHEN** the driver presses the control and the bridge cannot be reached
- **THEN** the client plays the problem tone

### Requirement: No silent failure
A client SHALL retry a failed upload once within ten seconds under the same turn id, and otherwise discard the recording and signal the problem. A client SHALL reconnect to the event stream on its own and ask for the events it missed.

#### Scenario: A brief gap
- **WHEN** an upload fails and the connection is back within ten seconds
- **THEN** the turn runs once and the driver hears the reply

#### Scenario: A long gap
- **WHEN** the connection stays away for longer than ten seconds
- **THEN** the recording is discarded, the problem tone plays, and nothing is sent later

### Requirement: Client sovereignty
A client SHALL contact no host other than the paired bridge, SHALL NOT use cloud speech services, analytics or third-party scripts, and SHALL keep the token only on the device.

#### Scenario: The web client loads
- **WHEN** the reference web client is opened
- **THEN** every request it makes goes to the bridge it was loaded from

#### Scenario: The Android app's dependencies
- **WHEN** the Android app is built
- **THEN** the build fails if a Google Play services or analytics dependency is present

### Requirement: Reference web client
The bridge SHALL serve a web client that implements the drive protocol with one full-screen control, for use while parked and for automated tests. The page SHALL be reachable only the way the bridge itself is.

#### Scenario: Parked use
- **WHEN** Mark opens the page on his phone over the tailnet's HTTPS name and presses the control
- **THEN** he can complete a spoken turn without any other app

### Requirement: Native Android client
The system SHALL provide a native Android app that implements the drive protocol with the screen off and takes the play/pause button of the car or a headset as its control.

#### Scenario: Screen off
- **WHEN** the phone's screen is off and the driver presses play/pause on the steering wheel
- **THEN** the listening tone plays and the utterance is recorded and sent

### Requirement: Parked test before driving
Each phase that changes what the driver hears or does SHALL be tried in a parked car, with the results recorded in the documentation, before it is used while driving.

#### Scenario: A new phase is deployed
- **WHEN** a phase that changes the spoken interaction is deployed
- **THEN** the parked-test checklist for it is filled in before its first use on the road
