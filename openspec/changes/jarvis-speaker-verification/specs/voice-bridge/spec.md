## ADDED Requirements

### Requirement: Speaker verification
The bridge SHALL be able to compare the voice in an utterance with the enrolled voice of its user, with a model that runs on the bridge itself; it SHALL NOT send audio or anything derived from it to a service outside the user's own machines. The check SHALL have three modes: off, which is the default; observe, in which every turn runs and the outcome is recorded; and enforce. In enforce mode an utterance whose voice does not match SHALL NOT be transcribed, SHALL NOT start an agent turn and SHALL NOT be kept, and the reply SHALL be one fixed sentence. An utterance with too little speech to compare SHALL be accepted in enforce mode only when it follows a matching utterance for the same project within the confirmation time limit, unless the configuration says to refuse it. If the check itself fails in enforce mode, the turn SHALL be refused and the reply SHALL say that the voice check is not working. With no enrolled voice, the check SHALL behave as off and the bridge SHALL say so when it starts. The threshold SHALL be a configured value, and the bridge SHALL NOT start in enforce mode without one.

A match SHALL NOT grant anything: it SHALL NOT replace the token, the private network, the read-back, a spoken or signed confirmation, or any refusal of the permission rules, and SHALL NOT count as a confirmation. The system does not claim to tell a live voice from a recording or a synthetic copy, and does not claim that a short utterance was spoken by its user.

#### Scenario: Mark asks a question
- **WHEN** the check is in enforce mode and Mark asks a question in a sentence
- **THEN** the turn runs as it does today

#### Scenario: A passenger speaks
- **WHEN** the check is in enforce mode and someone else speaks a request into the phone
- **THEN** nothing is transcribed, no agent turn starts, nothing of the utterance is kept, and Jarvis says only that it did not recognise the voice

#### Scenario: Observe mode
- **WHEN** the check is in observe mode and someone else speaks a request
- **THEN** the turn runs as it does today and the outcome of the check is recorded with it

#### Scenario: The yes after a read-back
- **WHEN** Mark's request matched, a read-back follows, and a short "yes" comes within the time limit
- **THEN** the "yes" is accepted by the speaker check, and the rules of the confirmation itself still decide

#### Scenario: A short utterance out of nowhere
- **WHEN** the check is in enforce mode and a one-word utterance arrives with no matching utterance before it within the time limit
- **THEN** it is refused like a voice that does not match

#### Scenario: A match is not a confirmation
- **WHEN** a gated action is pending and an utterance matches Mark's voice but is not an exact affirmation
- **THEN** the action is not confirmed

#### Scenario: A match is not a token
- **WHEN** a request arrives without a valid token, with audio in Mark's voice
- **THEN** it is refused before the audio is looked at

#### Scenario: A recording of Mark
- **WHEN** a recording of Mark's voice is played into the phone
- **THEN** the speaker check may accept it, and every other rule applies as if the check did not exist

#### Scenario: The check is broken
- **WHEN** the check is in enforce mode and the model or the enrolled voice cannot be used
- **THEN** the turn is refused, the reply says that the voice check is not working, and no utterance is accepted unchecked

#### Scenario: Nothing enrolled
- **WHEN** a mode other than off is configured and no voice is enrolled
- **THEN** every turn runs as with the check off, and the bridge reports at start-up that no voice is enrolled

#### Scenario: Enforce without a threshold
- **WHEN** enforce mode is configured without a threshold
- **THEN** the bridge does not start and says why

### Requirement: Voiceprint
The bridge SHALL create the enrolled voice only in an enrolment that is started on the bridge's own machine and that ends by itself after a fixed time; a request with the phone's token alone SHALL NOT start, change or replace it. The voiceprint SHALL be stored only on the bridge, encrypted, with its key kept outside the storage that holds it. The recordings used for enrolment SHALL be deleted when it ends. The bridge SHALL offer no way to read the voiceprint out, SHALL offer its user a command that deletes it, and SHALL NOT write it, or audio it was made from, to a log, a turn record, a prompt, another machine or the repository. The bridge SHALL NOT store a voiceprint or an embedding of any voice other than the enrolled one. A voiceprint made with another model SHALL be refused, not compared.

#### Scenario: Enrolling
- **WHEN** an enrolment is opened on the bridge's machine and Mark speaks the asked number of sentences through his phone
- **THEN** a voiceprint is stored encrypted, the recordings are gone, and the check can be switched on

#### Scenario: Enrolling with the token alone
- **WHEN** a recording is posted for enrolment and no enrolment was opened on the bridge's machine
- **THEN** it is refused and the stored voiceprint is unchanged

#### Scenario: A recording that does not fit
- **WHEN** one enrolment recording differs strongly from the others, or is too short
- **THEN** it is not used and another is asked for

#### Scenario: Reading it out
- **WHEN** any request asks the bridge for the voiceprint
- **THEN** there is no route that returns it

#### Scenario: A copy of the storage
- **WHEN** someone reads a copy or a backup of the bridge's storage without the key
- **THEN** they do not get the voiceprint

#### Scenario: Forgetting
- **WHEN** Mark runs the command that deletes the voiceprint
- **THEN** it is gone, and the check behaves as with nothing enrolled

#### Scenario: Another voice
- **WHEN** an utterance by someone else is checked
- **THEN** nothing derived from that voice is stored

#### Scenario: The model was replaced
- **WHEN** the bridge starts with a model other than the one the voiceprint was made with
- **THEN** the voiceprint is not used, and the check behaves as with nothing enrolled
