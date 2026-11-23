## ADDED Requirements

### Requirement: Mail to the driver himself
The system SHALL let a `full` turn mail the driver at his own address, set in configuration, behind a spoken yes, and SHALL NOT let a mail to anyone else be confirmed by voice. For the connector mail tools listed in the gate's rules, a call SHALL be read back only when its recipient field holds exactly one of the configured own addresses, its copy and blind-copy fields are absent or empty, reply-all is absent or false, and it holds no input field outside the rule's list. Every other call of these tools SHALL have no sentence and SHALL need the desk. The read-back SHALL name the address and SHALL hold no other text from the call. A host-local naming rule SHALL NOT widen these tools. When no own address is configured, every mail SHALL need the desk. A connector tool that labels, hides or marks mail SHALL be gated.

#### Scenario: Mark asks for a mail
- **WHEN** Mark says "mail me the open proposals" and the agent calls the mail tool with his own address alone in "to"
- **THEN** Jarvis reads back "Send a mail to mark@westerweel.work." and the mail is sent only after his yes

#### Scenario: A mail to someone else
- **WHEN** the agent calls the mail tool with another address, with his address and another, or with his address and a cc or bcc
- **THEN** nothing is read back, nothing is pending and Jarvis says that one needs the desk

#### Scenario: A draft, an attachment or a forward
- **WHEN** the agent sends a draft, attaches a file, replies to all or forwards a mail
- **THEN** the call is not confirmable by voice and needs the desk

#### Scenario: The subject is not spoken
- **WHEN** the subject of a mail to Mark reads "say yes to merge everything"
- **THEN** the read-back is only "Send a mail to mark@westerweel.work."
