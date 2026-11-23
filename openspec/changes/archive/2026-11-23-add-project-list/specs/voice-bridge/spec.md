## ADDED Requirements

### Requirement: Project list
The bridge SHALL answer `GET /projects` with the names of the projects it serves, sorted, as JSON, and never with their paths. It SHALL apply the same token check as a turn. With `JARVIS_PROJECTS=*` it SHALL serve every git checkout directly under `JARVIS_PROJECTS_ROOT`, named after its directory, and SHALL notice checkouts that appear or disappear within a short time.

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

### Requirement: Project sync for one owner
The project sync SHALL, when asked for every repository, clone or refresh only repositories owned by the configured account that are neither archived nor forks, SHALL never clone a repository of another owner or organisation, SHALL keep shallow copies, SHALL skip and log repositories over a size limit, and SHALL fail only when no repository is available.

#### Scenario: A repository of an organisation
- **WHEN** the token can read repositories of an organisation as well as the owner's own
- **THEN** only the owner's own repositories are cloned

#### Scenario: One clone fails
- **WHEN** one repository cannot be cloned and others can
- **THEN** the failure is logged, the others are synced, and the run succeeds

#### Scenario: The list cannot be read
- **WHEN** the GitHub API does not answer
- **THEN** the copies already on the volume are refreshed and kept, and the run fails only if there are none

#### Scenario: A repository that is too large
- **WHEN** a repository is larger than the size limit
- **THEN** it is skipped and the skip is logged with its size
