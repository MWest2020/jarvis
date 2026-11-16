## MODIFIED Requirements

### Requirement: Agent modes
The bridge SHALL support two agent modes, chosen in its configuration: `readonly` (the default), in which the turn runs next to the bridge with the read-only tool list, and `full`, in which the turn runs on the agent host. In `full` mode the bridge SHALL answer a turn in `readonly` mode, and say so, only when the agent host could not be reached, does not list the project, has its kill switch set, or refuses the project's settings, and only when the bridge has its own copy of the project. Without its own copy the bridge SHALL run no turn and SHALL say which of these happened. The bridge SHALL NOT run a turn a second time once the agent host has started it. The agent host SHALL have one setting that refuses every turn.

#### Scenario: Default
- **WHEN** the bridge starts without an agent mode in its configuration
- **THEN** it runs in `readonly` mode and never connects to an agent host

#### Scenario: The agent host is unreachable
- **WHEN** a turn in `full` mode cannot connect to the agent host, and the bridge has its own copy of the project
- **THEN** the turn is answered read-only and the spoken reply starts by saying that the agent host could not be reached

#### Scenario: Unreachable, and no copy next to the bridge
- **WHEN** a turn in `full` mode cannot connect to the agent host, and the bridge has no copy of the project
- **THEN** no turn runs and the spoken reply is "The agent host is not reachable right now."

#### Scenario: The kill switch
- **WHEN** full mode is switched off in the agent host's configuration
- **THEN** the agent host starts no turn, and the bridge answers read-only and says so, or, without its own copy of the project, only says so

#### Scenario: The connection drops during a turn
- **WHEN** the connection to the agent host is lost after the turn has started there
- **THEN** the bridge reports the failure, does not run the turn again in any mode, and the turn on the agent host runs to its end and is recorded

### Requirement: Remote agent adapter
In `full` mode the bridge SHALL run each turn through one ssh connection to the agent host, authenticated with a dedicated key whose only permitted use is a fixed wrapper. A request SHALL be either a turn, containing only a project name, the prompt and, when there is one, the answer to a pending confirmation, or a request for the list of projects, containing nothing else. The wrapper SHALL run headless Claude Code as the coding session's own user, in the project's checkout on the agent host, with the user's settings, instructions, memory and hooks loaded as for the coding session, and with the current instructions on every turn of a resumed session. A project SHALL be a name on the agent host's own list, or a directory directly under its configured root that is not excluded; it SHALL be a git repository with a work tree whose real path lies under that root. Of the agent's reply only the line marked for speech SHALL leave the agent host. The agent host SHALL decide which projects can be asked about: the bridge SHALL refuse, before the agent host is contacted, only a name that does not have the shape the agent host accepts. A list request SHALL name exactly the projects a turn would accept, SHALL start no agent and SHALL touch no pending confirmation. Speech-to-text and text-to-speech SHALL stay with the bridge.

#### Scenario: A turn on the agent host
- **WHEN** Mark asks something in `full` mode about a project on the agent host's list
- **THEN** the agent answers from that checkout, with the same instructions and memory as the coding session, and the bridge speaks the summary

#### Scenario: A project only on the agent host
- **WHEN** a project has a checkout on the agent host but the bridge has no copy of it
- **THEN** it appears in the phone's project list in `full` mode and a turn for it runs on the agent host

#### Scenario: A name the agent host does not have
- **WHEN** a turn in `full` mode names a project that has the right shape but is not on the agent host's list
- **THEN** the agent host starts no turn, and the bridge answers read-only from its own copy or, without one, says that the agent host does not have this project

#### Scenario: A project that is not a work tree
- **WHEN** the named directory is a bare repository, is excluded, or is a symlink that leads outside the root
- **THEN** the agent host starts no turn there, and the list request does not name it

#### Scenario: The agent host is busy or short of memory
- **WHEN** another full turn is running on the agent host, or less memory is available than the configured floor
- **THEN** no turn is started and the spoken reply says the agent host is busy

#### Scenario: An older wrapper and the list request
- **WHEN** the bridge asks for the list and the agent host's wrapper does not know the list request
- **THEN** the wrapper refuses it as a bad request, starts nothing, and the bridge lists its own copies

### Requirement: Project list
The bridge SHALL answer `GET /projects` with the names of the projects it serves, sorted, as JSON, and never with their paths. It SHALL apply the same token check as a turn. With `JARVIS_PROJECTS=*` it SHALL serve every git checkout directly under `JARVIS_PROJECTS_ROOT`, named after its directory, and SHALL notice checkouts that appear or disappear within a short time. In `full` mode the names SHALL be the agent host's list; when the agent host cannot give it, the bridge SHALL answer with its own projects and say in the answer which list it is.

#### Scenario: Listing with the token
- **WHEN** the phone sends `GET /projects` with a valid token
- **THEN** the bridge answers 200 with `{"projects": [...]}`, the names sorted, and no paths

#### Scenario: Listing without a valid token
- **WHEN** `GET /projects` arrives without a token or with a wrong one
- **THEN** the bridge answers 401 and names no project

#### Scenario: Every checkout under a root
- **WHEN** the bridge runs with `JARVIS_PROJECTS=*` and `JARVIS_PROJECTS_ROOT=/work`, and `/work` holds the git checkouts `alpha` and `bravo` and a plain directory `notes`
- **THEN** the project list is `alpha` and `bravo`, and a turn for either runs in its directory

#### Scenario: A name that is not a checkout
- **WHEN** a turn names a project that is not a checkout under the root, such as `..` or a hidden directory
- **THEN** the bridge answers 400 and runs no turn

#### Scenario: Wildcard without a root
- **WHEN** `JARVIS_PROJECTS=*` is set and `JARVIS_PROJECTS_ROOT` is missing or not a directory
- **THEN** the bridge refuses to start

#### Scenario: The list in full mode
- **WHEN** the phone sends `GET /projects` to a bridge in `full` mode and the agent host answers the list request
- **THEN** the answer holds the agent host's names and `"source": "agent_host"`

#### Scenario: The agent host cannot list
- **WHEN** the agent host cannot be reached, has its kill switch set, or gives no readable list within 20 seconds
- **THEN** the answer holds the bridge's own projects and `"source": "bridge"`

## ADDED Requirements

### Requirement: New repository becomes a project
In `full` mode, after a confirming turn in which a confirmed creation of a repository with `gh repo create <owner>/<name>` ran, the wrapper SHALL itself clone that repository to `<name>` directly under the agent host's project root, with the turn's environment and within a bounded time, so that it is a project at the next turn. It SHALL do so only when the agent host resolves projects under a root, the owner is one of the agent host's own owners, the name is a valid project name that is not excluded, and nothing is at that place yet. A clone SHALL appear under its name only when it is complete. The agent SHALL NOT be given a way to write into the home directory for this. The spoken reply SHALL say that the project is ready, or that it needs the desk, after the agent's own summary, and the phone SHALL be told that the project list changed.

#### Scenario: A repository created after a yes
- **WHEN** Mark says yes to "Create the private repository birdsong." and the creation ran
- **THEN** the wrapper clones it to `birdsong` under the project root, the reply ends with "It's ready. You can switch to it.", and the app's list shows `birdsong`

#### Scenario: The creation did not run
- **WHEN** the confirming turn ends without the confirmed creation having run, or it ran and failed
- **THEN** nothing is cloned and no project sentence is spoken

#### Scenario: The clone fails or takes too long
- **WHEN** the clone exits with an error or exceeds its time bound
- **THEN** nothing partial is left under the root, and the reply ends with "The repository was created, but I could not set it up as a project. That needs the desk."

#### Scenario: Another owner
- **WHEN** the confirmed creation names an owner that is not one of the agent host's own owners
- **THEN** nothing is cloned and the reply says that it needs the desk

#### Scenario: The name is already taken
- **WHEN** a directory with the repository's name already exists under the root
- **THEN** it is not touched; it counts as ready only when it is already a project

#### Scenario: A creation by another road
- **WHEN** a repository is created with a connector tool, an API call, a fork, or without a confirming turn
- **THEN** nothing is cloned
