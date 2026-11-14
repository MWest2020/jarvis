## ADDED Requirements

### Requirement: Installable plugin
The machine side SHALL be installable as a Claude Code plugin that provides commands to start the bridge, show its status and pair a phone.

#### Scenario: Someone else installs it
- **WHEN** a user who is not Mark installs the plugin on their own machine and runs the start command
- **THEN** the bridge runs with local speech and a read-only agent, and asks for nothing that only exists in Mark's setup

### Requirement: Pairing
The plugin SHALL pair a phone by showing a code that contains the bridge's address and a newly generated token. The token SHALL be stored only in the bridge's state directory, readable by the owner only, and never in the repository.

#### Scenario: Pairing a phone
- **WHEN** the user runs the pair command and scans the code with a client
- **THEN** the client can make an authorised turn and no token was typed or copied by hand

### Requirement: Nothing of a personal setup
The plugin SHALL contain no names, hosts, repositories or commands of its author's own setup. Continuous integration SHALL fail when the plugin directory contains a term from a deny-list kept outside that directory.

#### Scenario: A personal name slips in
- **WHEN** a change adds the name of one of Mark's own systems to a file in the plugin directory
- **THEN** the continuous integration run fails and names the file

### Requirement: The last drive at the desk
The plugin SHALL provide a command that shows the latest drive log inside a Claude Code session.

#### Scenario: Picking up after a drive
- **WHEN** the user runs the last-drive command at their desk
- **THEN** the session shows what was asked, answered, noted and handed off during the latest drive
