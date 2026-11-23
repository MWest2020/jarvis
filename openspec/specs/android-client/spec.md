# android-client Specification

## Purpose
TBD - created by archiving change add-android-client. Update Purpose after archive.

## Requirements

### Requirement: Talk control
The app SHALL offer one large control that records a question either while it is held or between two taps, and SHALL send the recording when the hold ends or on the second tap.

#### Scenario: Press and hold
- **WHEN** Mark holds the control, asks a question and lets go
- **THEN** the recording is sent to the bridge for the selected project

#### Scenario: Tap to start, tap to send
- **WHEN** Mark taps the control briefly, asks a question and taps it again
- **THEN** the recording is sent to the bridge for the selected project

#### Scenario: The app leaves the screen while recording
- **WHEN** the app goes to the background during a recording
- **THEN** the recording is discarded and nothing is sent later

### Requirement: Spoken round trip
The app SHALL upload the recording to the bridge's turn route in a format the bridge decodes, and SHALL play the audio reply as soon as it arrives.

#### Scenario: A normal turn
- **WHEN** the bridge answers a recording with an audio reply
- **THEN** the phone plays the reply without a further tap

#### Scenario: Stopping a reply
- **WHEN** Mark taps the control while a reply plays
- **THEN** playback stops

### Requirement: Audible waiting
The app SHALL confirm with a tone that the recording was sent and SHALL keep signalling with a soft tone at short intervals until the reply or an error arrives. The tones SHALL be produced on the phone.

#### Scenario: A turn of seventeen seconds
- **WHEN** the bridge takes seventeen seconds to answer
- **THEN** Mark hears an acknowledgement at once and a soft tick every few seconds until the reply starts

### Requirement: Project picker
The app SHALL show the project names on the main screen, SHALL send each turn for the selected one, and SHALL remember the selection. The names are the ones typed in the settings, or, when the projects field is empty or `*`, the list the bridge returns on `GET /projects`, fetched with the same client and route rules as a turn when the app starts and when Mark asks for a refresh.

#### Scenario: Switching project
- **WHEN** Mark selects another project and asks a question
- **THEN** the turn is sent for that project, and the same project is selected the next time the app opens

#### Scenario: Projects from the bridge
- **WHEN** the projects field is empty or `*` and the app starts
- **THEN** the app asks the bridge for its projects and shows them in the picker

#### Scenario: Refreshing the list
- **WHEN** Mark taps Refresh on the main screen
- **THEN** the app fetches the list again, keeps the selected project if it is still there, and otherwise selects the first

#### Scenario: The list cannot be fetched
- **WHEN** the bridge refuses the token, does not offer the route, or cannot be reached
- **THEN** the app says so in a sentence and keeps the last list it had

#### Scenario: Typed names
- **WHEN** the projects field holds names
- **THEN** the app shows exactly those names and does not ask the bridge

### Requirement: Summary on screen
The app SHALL show the summary text the bridge returns with a reply, in large type.

#### Scenario: A reply with a summary
- **WHEN** the bridge returns a reply with a summary
- **THEN** the summary is on the screen while the reply plays and stays there afterwards

### Requirement: Errors in words
The app SHALL tell the user in a plain sentence what went wrong and what to do, SHALL NOT show a bare status code as the only information, and SHALL play an error tone.

#### Scenario: Wrong token
- **WHEN** the bridge refuses the token
- **THEN** the app says the token was refused and to check it in the settings

#### Scenario: Busy project
- **WHEN** the bridge reports that the project is busy with another turn
- **THEN** the app says so and that Mark can try again in a moment

#### Scenario: No tailnet
- **WHEN** the bridge's name does not resolve or the connection is refused
- **THEN** the app asks whether Tailscale is on

### Requirement: Token storage
The app SHALL keep the token encrypted with a key held in the Android Keystore, SHALL NOT display it after it is saved, SHALL NOT write it to any log, and SHALL exclude it from backup and device transfer.

#### Scenario: Opening the settings again
- **WHEN** Mark opens the settings after saving a token
- **THEN** the token field is empty and the app says a token is stored

#### Scenario: A failed request
- **WHEN** a request fails for any reason
- **THEN** neither the message on the screen nor any log output contains the token

### Requirement: Plain HTTP only on the tailnet
The app SHALL send over plain HTTP only to a `*.ts.net` name, only on a connection bound to the Tailscale VPN network, and only when every address the name resolves to is a Tailscale address that this network routes. It SHALL require HTTPS for every other address, SHALL NOT use a proxy, and SHALL NOT follow redirects.

#### Scenario: A plain HTTP address outside the tailnet
- **WHEN** Mark enters an `http://` address whose host is not under `.ts.net`
- **THEN** the settings refuse it

#### Scenario: Tailscale is off on a hostile network
- **WHEN** Tailscale is off and the network's own resolver answers the bridge's `*.ts.net` name with an address inside the Tailscale range
- **THEN** nothing is sent and the app says that Tailscale is off

#### Scenario: The name resolves outside the tunnel's routes
- **WHEN** Tailscale is on and the bridge's name resolves to an address that the Tailscale network does not route
- **THEN** nothing is sent

#### Scenario: A proxy is configured on the phone
- **WHEN** the phone has a system HTTP proxy configured
- **THEN** the turn does not go through it

### Requirement: Client sovereignty
The app SHALL contact no host other than the configured bridge and SHALL contain no Google Play services, Firebase, analytics or other third-party library beyond AndroidX, Kotlin and kotlinx.

#### Scenario: A third-party dependency is added
- **WHEN** a library outside AndroidX, Kotlin and kotlinx reaches the app's runtime classpath
- **THEN** the build fails

### Requirement: Sideloaded distribution
The app SHALL be built in CI as an APK for sideloading and SHALL NOT be published to an app store. An APK built on main SHALL be signed with one stable key, stored as repository secrets, that the repository owner asked for (a key made by an agent at the owner's request counts); an APK from a pull request SHALL NOT be signed with it.

#### Scenario: A build on main
- **WHEN** a change to the app is merged
- **THEN** CI offers the APK as a download to people with access to the private repository

#### Scenario: A push to main without the signing secrets
- **WHEN** a signing secret is missing or empty on a push to main
- **THEN** the CI run fails and offers no APK

#### Scenario: A pull request build
- **WHEN** CI builds the app for a pull request
- **THEN** the APK gets a per-run key and is never signed with the stable key

### Requirement: Parked use
The app SHALL be usable on one screen with large controls and type, and its documentation SHALL state that it is for use while parked, not while driving.

#### Scenario: In the mount, parked
- **WHEN** the phone is in a car mount and the settings are done
- **THEN** a whole turn needs only the one control and no typing

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
