## MODIFIED Requirements

### Requirement: Network exposure
The bridge SHALL only be reachable over the Tailscale network and SHALL reject requests without a valid token. On a workstation it listens on its Tailscale address. In a cluster it MAY listen on a private pod address, but only with an explicit opt-in, only when a tailnet-only proxy is the sole way in, and never on a wildcard or public address.

#### Scenario: Request from outside the tailnet
- **WHEN** a request arrives on any interface other than Tailscale
- **THEN** it is not served

#### Scenario: Request without a valid token
- **WHEN** a request over the tailnet has no token or a wrong one
- **THEN** the bridge answers 401 and runs no turn

#### Scenario: Private address without the opt-in
- **WHEN** the bridge is configured to listen on a private, non-Tailscale address and `JARVIS_BEHIND_TAILSCALE_PROXY` is not `1`
- **THEN** it refuses to start

#### Scenario: Behind a Tailscale proxy in a cluster
- **WHEN** the bridge runs in a pod with `JARVIS_BEHIND_TAILSCALE_PROXY=1` and listens on its pod IP
- **THEN** it logs a warning at start, it is exposed only through a tailnet-only Service without Funnel, and a NetworkPolicy admits connections to it only from the Tailscale operator's namespace

#### Scenario: Wildcard or public address with the opt-in
- **WHEN** `JARVIS_BEHIND_TAILSCALE_PROXY=1` is set and the bind address is a wildcard or a public address
- **THEN** the bridge refuses to start

#### Scenario: Connection from another namespace in the cluster
- **WHEN** a pod outside the Tailscale operator's namespace connects to the bridge's pod IP
- **THEN** the connection is dropped by the NetworkPolicy
