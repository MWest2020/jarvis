## ADDED Requirements

### Requirement: Notifications in
The bridge SHALL accept a notification from a configured sender as one small structured request: a kind from a closed list (a build, a question, a reply, a confirmation), optionally an outcome, a project, a short text and a link. Each sender SHALL have its own token, which SHALL be accepted for notifying only and only for the kinds build and question; a notification of the kinds reply and confirmation SHALL be created by the bridge itself and SHALL be refused from every sender. The phone's token SHALL NOT be accepted for notifying. The bridge SHALL apply to the text the same filter as to spoken output (no code, no paths, nothing shaped like a secret), SHALL cap its length, SHALL drop a link that is not HTTPS to an allowed host, and SHALL limit how many notifications one sender can send per hour. It SHALL pass the result to a notification broker that is reachable only inside the private network and that involves no service outside the user's own machines. The bridge SHALL NOT pass the text of a notification to the agent.

#### Scenario: A build finished
- **WHEN** the build system posts a notification of kind build with outcome ok for a known project, with its own token
- **THEN** the bridge accepts it and the broker holds a message with that kind, outcome, project and the filtered text

#### Scenario: A sender token on another route
- **WHEN** a request to start a turn or to list projects carries a sender token
- **THEN** it is refused as if it had no token

#### Scenario: A sender claims a confirmation
- **WHEN** a sender posts a notification of kind confirmation or reply with a valid sender token
- **THEN** it is refused and nothing is published

#### Scenario: The phone's token on the notify route
- **WHEN** a notification is posted with the phone's token
- **THEN** it is refused

#### Scenario: Text with a secret or code
- **WHEN** a sender's text contains something shaped like a credential, a code block or a file path
- **THEN** that part does not reach the broker, and a text that is empty afterwards is replaced by the fixed sentence of its kind

#### Scenario: An unknown kind
- **WHEN** a notification names a kind outside the closed list, or carries a field the bridge does not know
- **THEN** it is refused and nothing is published

#### Scenario: A sender in a loop
- **WHEN** one sender posts more notifications than its hourly limit
- **THEN** the ones over the limit are refused and the other senders are not affected

#### Scenario: The broker is down
- **WHEN** the bridge cannot reach the broker
- **THEN** the sender is told that the notification was not accepted, and the bridge keeps no copy

#### Scenario: Nothing leaves the private network
- **WHEN** a notification travels from a sender to the phone
- **THEN** no host outside the private network is contacted by the bridge or by the broker

#### Scenario: A notification that reads like an instruction
- **WHEN** a notification's text says to merge something or to run a command
- **THEN** no agent turn is started by it and no turn receives it as a prompt
