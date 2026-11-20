## ADDED Requirements

### Requirement: Egress of full turns
When egress control is enforced on the agent host, the system SHALL let a `full` turn's processes, and every process they start, open outbound connections only to the agent host's egress proxy and to the ssh destinations the configuration lists, enforced by the operating system rather than by environment variables, and SHALL NOT change what the coding session at the desk can reach. The proxy SHALL allow a request only when its origin is on a root-owned allow-list and, where the rule limits them, its method and path are listed; it SHALL deny everything else. A host without a credential or a method or path rule MAY be passed through by name without TLS interception. The proxy SHALL record every allowed and denied request (time, method, origin, path without query, status, decision, reason, whether a credential was added) without bodies or headers, and the turn record SHALL summarise the requests of that turn. A voice turn SHALL NOT be able to change the allow-list, the proxy or the filter. If the filter cannot be verified at the start of a turn, that turn SHALL NOT run in `full` mode. Egress control SHALL be off unless the agent host's administrator switches it on. The system does not claim these guarantees against a turn that has root on the agent host.

#### Scenario: A request to a host not on the list
- **WHEN** egress control is enforced and a command in a voice turn sends a request to a host that is not on the list, through the proxy or directly
- **THEN** the request does not leave the agent host, the denial is logged, and the turn record names the denied origin

#### Scenario: A program that ignores the proxy settings
- **WHEN** a program in a voice turn connects directly to an address, ignoring the proxy settings
- **THEN** the connection is dropped by the operating system and logged

#### Scenario: The desk is not affected
- **WHEN** egress control is enforced and the coding session at the desk opens a connection
- **THEN** that connection is not filtered by the turn's rules

#### Scenario: The filter is missing
- **WHEN** a turn starts and the filter for turns is not loaded or no longer matches the turn's processes
- **THEN** the turn does not run in `full` mode and the bridge answers it read-only

### Requirement: Credentials held outside the turn
When credential replacement is enabled, a `full` turn SHALL run as a user that cannot read the real credentials the proxy holds, and SHALL hold only placeholders for them. The proxy SHALL replace a credential header only over HTTPS, only on the origin, methods and paths listed for that credential, only when the whole header value equals the configured placeholder, and never on a redirect to another origin; a request with any other value in that header SHALL be denied. The proxy SHALL redact literal copies of a real credential in responses and in its log. The proxy's configuration SHALL name credentials, never contain them. Credentials that are not HTTP headers, such as ssh keys, SHALL NOT be readable by the turn; the turn MAY use them through an agent run by another user, limited to listed destinations. The system does not claim to prevent an allowed host from returning a credential in a transformed form, nor data the turn can read from leaving through an allowed host that accepts writes.

#### Scenario: The GitHub token in a voice turn
- **WHEN** credential replacement is enabled and a voice turn calls the GitHub API with `gh`
- **THEN** the turn's environment and files hold only a placeholder, and the proxy adds the real token to that request because its origin, method and path are listed

#### Scenario: A placeholder sent elsewhere
- **WHEN** a voice turn sends the placeholder to an origin, method or path that is not listed for that credential
- **THEN** no real credential is added and the request is denied

#### Scenario: Reading the secret
- **WHEN** a voice turn tries to read the proxy's credentials with a shell command or with Claude Code's file tools
- **THEN** the read fails because the turn's user has no access to them
