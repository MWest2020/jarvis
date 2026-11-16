## ADDED Requirements

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

#### Scenario: No signing key yet
- **WHEN** the signing secrets do not exist
- **THEN** CI builds a debug-signed APK and no release-signed one

### Requirement: Parked use
The app SHALL be usable on one screen with large controls and type, and its documentation SHALL state that it is for use while parked, not while driving.

#### Scenario: In the mount, parked
- **WHEN** the phone is in a car mount and the settings are done
- **THEN** a whole turn needs only the one control and no typing
