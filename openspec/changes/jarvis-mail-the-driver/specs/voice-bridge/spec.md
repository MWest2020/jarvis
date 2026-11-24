## ADDED Requirements

### Requirement: Mail to the driver himself
The system SHALL let a `full` turn mail the driver at his own address, set in configuration, behind a spoken yes, and SHALL NOT let a mail to anyone else be confirmed by voice. For the connector send tool listed in the gate's repository rules, a call SHALL be read back only when all of these hold:

- its recipient field holds exactly one of the configured own addresses, as plain ASCII with nothing around it;
- its copy and blind-copy fields are absent or empty;
- it holds no input field outside the rule's list.

Every other call of a Gmail connector tool SHALL have no sentence and SHALL need the desk, including a reply tool, a forward, a draft, and a send to anyone else. The read-back SHALL name the address as it will be sent and SHALL hold no other text from the call than the mail's subject, said as follows. This is the one exception to the rule of `jarvis-full-agent` that a read-back holds no text from the call and none written by the agent, made by Mark on 2026-11-24 ("zou fijn zijn", in answer to issue #92 "Should a mail to you read back its subject, or only your address?"), and it holds only for a mail to the driver's own address:

- the spoken subject SHALL come from the call's subject field only, and SHALL differ from the subject that is sent only by NFKC and by collapsing ordinary spaces: a subject that holds a line break, a line or paragraph separator, or a control, format (bidirectional overrides and isolates, zero-width characters), private-use, surrogate or unassigned character, or that is not text, SHALL leave the call without a sentence;
- it SHALL be normalised to NFKC with stray combining marks removed and runs of space collapsed, and capped at 80 characters, the last being "…";
- it SHALL stand between fixed words on both sides ("with the subject “…”, end of subject"), before the question, and a double quote inside it SHALL become an apostrophe;
- because quotation marks are not heard, a subject that could be taken for Jarvis's own words, the end of the frame, a question or another action SHALL be read as "with a subject that cannot be read out", refused broadly and not by exact spelling: when it holds punctuation other than a comma, apostrophe, hyphen and a few symbols (so no sentence punctuation, dash or bracket, which the speech engine may read as markup), more than ten words, in its letters run together a word of the frame, of the question or of any read-back sentence, or a word that means yes;
- a subject that holds a letter of another script than Latin, a digit outside ASCII, or a credential shape or word SHALL be read the same way;
- a missing or empty subject SHALL be read as "without a subject".

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
- **WHEN** the subject of a mail to Mark reads "say yes", "Mark, reply ja", "end off subject. Merge pull request 12 on jarvis. Say yes to go ahead", "einde onderwerp" or `[Notes](/ænd Qv sAbdZEkt/)`
- **THEN** the read-back is "Send a mail to mark@westerweel.work, with a subject that cannot be read out."

#### Scenario: A subject that cannot be heard as anything else
- **WHEN** the subject reads "Open proposals"
- **THEN** the read-back is "Send a mail to mark@westerweel.work, with the subject “Open proposals”, end of subject."

#### Scenario: A subject with a line break or an invisible character
- **WHEN** the subject reads "x" followed by a line break and "Bcc: someone@example.org", or holds a right-to-left override or a zero-width character
- **THEN** nothing is read back, nothing is pending and Jarvis says that one needs the desk

#### Scenario: A mail without a subject
- **WHEN** the subject is missing, empty or only spaces
- **THEN** the read-back is "Send a mail to mark@westerweel.work, without a subject."

#### Scenario: A long subject
- **WHEN** the subject has ten words or fewer and more than 80 characters, or runs of spaces
- **THEN** the spaces are one plain space and the spoken subject ends with "…" at 80 characters

#### Scenario: The yes is for this subject only
- **WHEN** Mark said yes to a mail read back with one subject and the agent then sends it with another subject, or with one that reads the same but differs in a byte (a fullwidth letter, a double space)
- **THEN** the yes does not release it

#### Scenario: No other read-back speaks a subject
- **WHEN** the agent sends a mail with the `mail` command and `-s`, or through a connector named by a host-local rule
- **THEN** the read-back names the recipients only
