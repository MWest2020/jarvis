## ADDED Requirements

### Requirement: Biometric confirmation
The app SHALL be able to confirm a pending action with a signature from a key that is created in the phone's secure hardware, cannot be exported, is not part of any backup, and can sign only after a strong biometric check of the phone's owner, one signature per check. A biometric enrolled on the phone after the key was created SHALL make the key unusable. A biometric check that needs no deliberate act of the owner, such as a recognised face, SHALL be followed by an explicit tap before anything is signed. The app SHALL show the read-back sentence it received, SHALL sign the hash of exactly the text it displays together with the pending action's identifier and call hash, and SHALL NOT sign without the sentence on screen. It SHALL offer a way to cancel, SHALL send nothing after the time limit, and SHALL NOT offer the confirmation from a notification or from the lock screen. The app SHALL show the public key and its fingerprint so that the owner can enrol it, and SHALL say so in words when the key is missing or no longer valid. This SHALL add one Android permission (use of the biometric sensor) and no library outside AndroidX, Kotlin and kotlinx.

#### Scenario: Confirming with the fingerprint
- **WHEN** a reply asks for a confirmation and Mark touches the sensor while the sentence is on screen
- **THEN** the app sends one signature for that pending action and plays the bridge's answer

#### Scenario: The sensor does not recognise the finger
- **WHEN** the biometric check fails or is dismissed
- **THEN** nothing is signed and nothing is sent as a yes

#### Scenario: A face that is only seen
- **WHEN** the phone recognises its owner's face while the sentence is on screen and the owner does not tap to confirm
- **THEN** nothing is signed

#### Scenario: One touch, one signature
- **WHEN** a second confirmation is asked for right after a first one was signed
- **THEN** the key signs again only after a new biometric check

#### Scenario: Cancel
- **WHEN** Mark taps cancel on the confirmation screen
- **THEN** the app sends a no, without a signature

#### Scenario: Time is up
- **WHEN** the time limit of the pending action passes while the sentence is on screen
- **THEN** the screen closes and the app sends nothing

#### Scenario: A new fingerprint on the phone
- **WHEN** a biometric was enrolled on the phone after the key was created
- **THEN** the key signs nothing, and the app says that a new key must be created and enrolled at the desk

#### Scenario: No strong biometric on the phone
- **WHEN** the user tries to create the key on a phone without an enrolled strong biometric
- **THEN** no key is created and the app says why

#### Scenario: Enrolment
- **WHEN** the user creates the key
- **THEN** the app shows the public key and its fingerprint, and the private key never leaves the phone

#### Scenario: A bridge that does not ask for it
- **WHEN** a reply needs a confirmation but carries no confirmation details, as from an older bridge
- **THEN** the app plays the read-back as an ordinary reply and offers no fingerprint
