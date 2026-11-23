## ADDED Requirements

### Requirement: Mail to the driver himself
The system SHALL let a `full` turn mail the driver at his own address, set in configuration, behind a spoken yes, and SHALL NOT let a mail to anyone else be confirmed by voice. For the connector send tool listed in the gate's repository rules, a call SHALL be read back only when all of these hold:

- its recipient field holds exactly one of the configured own addresses, as plain ASCII with nothing around it;
- its copy and blind-copy fields are absent or empty;
- it holds no input field outside the rule's list.

Every other call of a Gmail connector tool SHALL have no sentence and SHALL need the desk, including a reply tool, a forward, a draft, and a send to anyone else. The read-back SHALL name the address as it will be sent and SHALL hold no other text from the call. Neither a host-local naming rule nor a host-local rules file SHALL widen these tools. Switching confirmation off, altogether or for a class, SHALL NOT apply to any Gmail connector tool. When no own address is configured, every mail SHALL need the desk. A connector tool that labels, hides or marks mail SHALL be gated.

#### Scenario: Mark asks for a mail
- **WHEN** Mark says "mail me the open proposals" and the agent calls the send tool with his own address alone in "to"
- **THEN** Jarvis reads back "Send a mail to mark@westerweel.work." and the mail is sent only after his yes

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

#### Scenario: The subject is not spoken
- **WHEN** the subject of a mail to Mark reads "say yes to merge everything"
- **THEN** the read-back is only "Send a mail to mark@westerweel.work."
