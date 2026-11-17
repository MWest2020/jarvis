## ADDED Requirements

### Requirement: Notification listener
The app SHALL be able to receive notifications from the configured notification server over a connection that it holds itself, without Google Play services, Firebase or any other push service. The listener SHALL be off until the user switches it on, SHALL run as a foreground service of a declared type with a permanent notification that says whether it is connected, and SHALL start again after a reboot or an app update when it was on. It SHALL use the same network rules as a voice turn: plain HTTP only for a tailnet name and only through the Tailscale tunnel, never through a proxy, and no connection when Tailscale is off. Its credential SHALL be able to read notifications only and SHALL be stored like the bridge token. After a break in the connection the app SHALL fetch what it missed and SHALL show each notification once. The listener SHALL add exactly four Android permissions to the app (showing notifications, running a foreground service, its declared type, starting at boot) and no other.

#### Scenario: A notification while the phone is locked in a pocket
- **WHEN** the listener is on, the phone is on the tailnet and the bridge accepts a notification
- **THEN** the phone shows it within about a minute

#### Scenario: Off by default
- **WHEN** the app is installed and the listener was never switched on
- **THEN** no service runs, no connection is held and no notification permission is asked for

#### Scenario: Tailscale is off
- **WHEN** the listener is on and the phone leaves the tailnet
- **THEN** nothing is sent outside the tunnel and the permanent notification says that it is not connected

#### Scenario: Catching up
- **WHEN** the phone comes back to the tailnet after an hour in which two notifications were sent
- **THEN** both are shown, once each

#### Scenario: After a reboot
- **WHEN** the phone restarts and the listener was on
- **THEN** the listener runs again without the app being opened

#### Scenario: Permission refused
- **WHEN** the user refuses the permission to show notifications
- **THEN** the listener stays off and the app says why

#### Scenario: Another permission appears
- **WHEN** the built app asks for an Android permission that no approved change has named
- **THEN** the build fails

### Requirement: Lock screen content
A notification posted by the app SHALL contain only one fixed sentence chosen by its kind and outcome. The project, text written by a sender, a link, code, a path, a secret and the read-back of a pending action SHALL NOT be part of the notification in any field, so that a locked phone cannot show them under any lock-screen setting of the system. The app SHALL show the project and the filtered text only on its own screen, which SHALL NOT be shown over the lock screen. The app SHALL offer a setting that keeps its notifications off the lock screen entirely. A notification SHALL NOT carry a button that performs an action, SHALL NOT be passed on to a watch or a car display, and a tap on it SHALL only open the app. The app SHALL NOT write the text of a notification to the phone's storage.

#### Scenario: A failed build on a locked phone
- **WHEN** a notification of kind build with outcome failed arrives while the phone is locked
- **THEN** the lock screen shows "A build failed" and nothing else from the notification

#### Scenario: The phone shows all notification content on the lock screen
- **WHEN** the system's lock-screen setting is "show all notification content" and a notification with a project and a sender's text arrives while the phone is locked
- **THEN** the lock screen still shows only the fixed sentence, because the notification holds nothing else

#### Scenario: Reading what it was about
- **WHEN** the user taps the notification and unlocks the phone
- **THEN** the app shows the project and the filtered text

#### Scenario: The shade of an unlocked phone
- **WHEN** the phone is unlocked and the notification is expanded in the shade
- **THEN** it still shows only the fixed sentence

#### Scenario: Hidden on the lock screen
- **WHEN** the user has switched on the setting that keeps notifications off the lock screen
- **THEN** a locked phone shows no Jarvis notification at all, and they appear after unlocking

#### Scenario: A pending confirmation
- **WHEN** a notification of kind confirmation arrives
- **THEN** it says only that a confirmation is waiting, locked or unlocked, and the action can be confirmed only inside the app

#### Scenario: No action from the shade
- **WHEN** the user looks at any Jarvis notification
- **THEN** it offers no button, and tapping it opens the app

## MODIFIED Requirements

### Requirement: Client sovereignty
The app SHALL contact no host other than the configured bridge and, when the notification listener is on, the configured notification server, and SHALL contain no Google Play services, Firebase, analytics or other third-party library beyond AndroidX, Kotlin and kotlinx.

#### Scenario: A third-party dependency is added
- **WHEN** a library outside AndroidX, Kotlin and kotlinx reaches the app's runtime classpath
- **THEN** the build fails

#### Scenario: Notifications without a push service
- **WHEN** the notification listener is built into the app
- **THEN** the runtime classpath still holds only AndroidX, Kotlin and kotlinx, and the app holds its own connection to the user's notification server
