## ADDED Requirements

### Requirement: Signed confirmation
The system SHALL accept, as a confirmation of a pending gated action, a signature made on the user's phone by a key that the phone releases only after a strong biometric check, one signature per check. The signature SHALL cover the pending action's identifier, the hash of the exact call, the hash of the read-back sentence that the phone displayed, and the answer yes. The agent host SHALL verify the signature itself, against its own pending action and the read-back it issued and against a list of enrolled public keys that only an administrator of the agent host can change; it SHALL NOT rely on the bridge's word that a signature was valid. A signed confirmation SHALL release the same single call, once, within the same time limit, as a spoken confirmation does, and a confirmed call SHALL still pass the coding session's permission rules. If the signature cannot be verified for any reason, the call SHALL NOT run and the pending action SHALL be cleared.

The agent host's configuration SHALL set, per class of gated action, whether a spoken yes, a signed confirmation or either is accepted; a call in several classes SHALL take the strictest. Where a signed confirmation is required, a spoken yes SHALL NOT release the call, the read-back SHALL say that a fingerprint is needed, and without an enrolled key the call SHALL NOT be confirmable. Switching confirmation off by voice SHALL require the strictest method configured for any class. A public key SHALL NOT be enrolled, replaced or removed by a voice turn or through the bridge. The record of a confirmed action and the notification outside the voice channel SHALL say by which method it was confirmed and, for a signed one, by which key. The system does not claim these guarantees against an agent that deliberately alters the agent host's own state, or against a phone whose app has been replaced.

#### Scenario: Confirmed with a fingerprint
- **WHEN** a merge is pending, the phone shows its read-back, Mark touches the sensor and the phone sends the signature within the time limit
- **THEN** the agent host verifies it, that merge runs once, and the record says it was confirmed by fingerprint with that key

#### Scenario: A confirmation the bridge made up
- **WHEN** the class of the pending action requires a signed confirmation and the agent host receives a confirmation without a signature, or with a signature it cannot verify, from the bridge
- **THEN** the action does not run and the pending action is cleared

#### Scenario: A spoken yes for a class that needs the fingerprint
- **WHEN** the pending action's class requires a signed confirmation and Mark says "yes"
- **THEN** the action does not run, the pending action is gone, and Jarvis says that it needs his fingerprint

#### Scenario: Another sentence on the screen
- **WHEN** the phone was shown a read-back that differs in any byte from the one the agent host issued, and the user signs it
- **THEN** the signature does not verify and the action does not run

#### Scenario: A signature for another action
- **WHEN** a valid signature for one pending action is sent again later, or for a pending action with another identifier or another call
- **THEN** it releases nothing

#### Scenario: A signature made for another purpose
- **WHEN** a signature made with the enrolled key for a purpose other than confirming an action is sent as a confirmation
- **THEN** it does not verify

#### Scenario: Too late
- **WHEN** the signature arrives after the time limit of the pending action
- **THEN** the action does not run

#### Scenario: Either method
- **WHEN** the class accepts either method and Mark says "yes" instead of using his fingerprint
- **THEN** the action is confirmed as a spoken confirmation, and the record says voice

#### Scenario: Several classes
- **WHEN** a call falls in one class that accepts a spoken yes and one that requires a signed confirmation
- **THEN** a signed confirmation is required

#### Scenario: No key enrolled
- **WHEN** a class requires a signed confirmation and no public key is enrolled on the agent host
- **THEN** nothing is pending and Jarvis says that one needs the desk

#### Scenario: Enrolling a key by voice
- **WHEN** a voice turn tries to add, replace or remove an enrolled public key
- **THEN** the write is refused and Jarvis says that one needs the desk

#### Scenario: Switching the gate off by voice
- **WHEN** any class requires a signed confirmation and Mark asks by voice to switch confirmation off
- **THEN** a spoken yes does not switch it off; a signed confirmation does

#### Scenario: The verifier fails
- **WHEN** the agent host cannot run the verification or cannot read the list of keys
- **THEN** the action does not run and it is not treated as a spoken yes

#### Scenario: Cancelled on the phone
- **WHEN** Mark taps cancel on the confirmation screen
- **THEN** the pending action is cleared on the agent host as after a spoken no, and no signature is needed
