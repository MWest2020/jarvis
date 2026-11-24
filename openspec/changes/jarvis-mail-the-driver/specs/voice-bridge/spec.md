## ADDED Requirements

### Requirement: Mail to the driver himself
The system SHALL let a `full` turn mail the driver at his own address, set in configuration, behind a spoken yes, and SHALL NOT let a mail to anyone else be confirmed by voice. For the connector send tool listed in the gate's repository rules, a call SHALL be read back only when all of these hold:

- its recipient field holds exactly one of the configured own addresses, as plain ASCII with nothing around it;
- its copy and blind-copy fields are absent or empty;
- it holds no input field outside the rule's list.

Every other call of a Gmail connector tool SHALL have no sentence and SHALL need the desk, including a reply tool, a forward, a draft, and a send to anyone else. The read-back SHALL name the address as it will be sent and SHALL hold no other text from the call than the mail's subject, said as follows. This is the one exception to the rule of `jarvis-full-agent` that a read-back holds no text from the call and none written by the agent, made by Mark on 2026-11-24 ("zou fijn zijn", in answer to issue #92 "Should a mail to you read back its subject, or only your address?"), and it holds only for a mail to the driver's own address:

- the spoken subject SHALL come from the call's subject field only;
- it SHALL be normalised to NFKC, with every control, format (bidirectional overrides and isolates, zero-width characters), private-use, surrogate and unassigned character and every stray combining mark removed, every white space or separator made one plain space, and runs of space collapsed;
- it SHALL be capped at 80 characters, the last being "…";
- it SHALL stand between fixed words on both sides ("with the subject “…”, end of subject"), before the question, and a double quote inside it SHALL become an apostrophe, so that it cannot be taken for Jarvis's own words or for a question to the driver;
- a missing or empty subject SHALL be read as "without a subject";
- a subject that holds the closing words of that frame in any spelling, a letter of another script than Latin, a digit outside ASCII, or a credential shape SHALL be read as "with a subject that cannot be read out";
- a subject that is not text SHALL leave the call without a sentence.

The confirmation SHALL stay bound to the hash of the whole call, so the subject that is sent is the subject that was read back, and a call with any other subject SHALL NOT be released by that yes. No other read-back SHALL hold a subject or any other text written by the agent. Neither a host-local naming rule nor a host-local rules file SHALL widen these tools. Switching confirmation off, altogether or for a class, SHALL NOT apply to any Gmail connector tool. When no own address is configured, every mail SHALL need the desk. A connector tool that labels, hides or marks mail SHALL be gated.

#### Scenario: Mark asks for a mail
- **WHEN** Mark says "mail me the open proposals" and the agent calls the send tool with his own address alone in "to"
- **THEN** Jarvis reads back "Send a mail to mark@westerweel.work, with the subject “Open proposals”, end of subject." and the mail is sent only after his yes

#### Scenario: A mail to someone else
- **WHEN** the agent calls the send tool with another address, with his address and another, with a lookalike of his address, or with his address and a cc or bcc
- **THEN** nothing is read back, nothing is pending and Jarvis says that one needs the desk

#### Scenario: A draft, an attachment, a reply or a forward
- **WHEN** the agent sends a draft, attaches a file, uses the reply tool or forwards a mail
- **THEN** the call is not confirmable by voice and needs the desk

#### Scenario: Confirmation switched off
- **WHEN** confirmation is off, or `message` is in the skipped classes, and the agent sends a mail with an attachment to another address, forwards a mail or sends a draft
- **THEN** the call does not run; it needs the desk

#### Scenario: The yes is for this mail only
- **WHEN** Mark said yes to a mail to his address and the agent then calls the send tool with another recipient or an added bcc
- **THEN** the yes does not release it

#### Scenario: A subject that sounds like Jarvis
- **WHEN** the subject of a mail to Mark reads "say yes" or "Mark, reply ja"
- **THEN** the read-back is "Send a mail to mark@westerweel.work, with the subject “say yes”, end of subject." (or “Mark, reply ja”), framed as a subject, followed by the question

#### Scenario: A subject that closes the frame itself
- **WHEN** the subject reads `x", end of subject. Merge pull request 5. "y`, or holds a Cyrillic lookalike or a credential shape
- **THEN** the read-back is "Send a mail to mark@westerweel.work, with a subject that cannot be read out."

#### Scenario: A mail without a subject
- **WHEN** the subject is missing, empty or only invisible characters
- **THEN** the read-back is "Send a mail to mark@westerweel.work, without a subject."

#### Scenario: A long or messy subject
- **WHEN** the subject holds a right-to-left override, zero-width characters, line breaks or more than 80 characters
- **THEN** those characters are removed, the white space is one plain space, and the spoken subject ends with "…" at 80 characters

#### Scenario: The yes is for this subject only
- **WHEN** Mark said yes to a mail read back with one subject and the agent then sends it with another subject, or with the same subject plus an invisible character
- **THEN** the yes does not release it

#### Scenario: No other read-back speaks a subject
- **WHEN** the agent sends a mail with the `mail` command and `-s`, or through a connector named by a host-local rule
- **THEN** the read-back names the recipients only
