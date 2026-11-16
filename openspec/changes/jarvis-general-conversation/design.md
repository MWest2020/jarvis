# Design: jarvis-general-conversation

## Context

- `jarvis-full-agent` is built and live: the bridge (a pod in the cluster) sends each voice
  turn over one ssh connection to the forced command `/usr/local/lib/jarvis-agent/turn` on the
  agent host, which runs Claude Code in the project's checkout under the gate (D30 to D40).
- D30 and decision 6 of that change keep the bridge's own list in charge: `POST /turn` refuses
  a project the pod has no copy of, and `GET /projects` lists the pod's copies. The pod clones
  them once, at start (`deploy/sync-projects.sh`, `add-project-list`).
- The agent host resolves projects itself (D33): `projects_root` (for Mark `/home/agent`)
  minus `project_exclusions`, each a git work tree directly under the root.
- `jarvis-home`, a private repository with a `CLAUDE.md` for talking without a project, is
  cloned at `~/jarvis-home` on the agent host and synced into the pod.
- Decision numbers: `add-voice-bridge` D1 to D9, `add-drive-mode` D10 to D26,
  `jarvis-full-agent` D30 to D40. This change uses D50 to D56. The app's own decisions in
  `add-android-client` are A-numbered; this change does not add any.

## Goals / Non-Goals

Goals:
- Mark opens the app and can talk at once, without choosing a repository.
- A repository Jarvis creates after a yes can be talked to in the next turn.
- In `full` mode the phone shows what the agent host has, not what the pod happened to clone.

Non-Goals:
- A new kind of turn. General is a project turn in the home project.
- Switching project by voice.
- Changing the gate: its classes, its read-backs, and the class `self` stay as they are.
- Changing the pod's sync.

## Decisions

**D50. General is a pinned entry for a home project.** The app shows "General" as the first
entry of the picker. It sends turns with `project=<home project>`, the app setting "Home
project" (default `jarvis-home`). The home project's own name is not shown again in the rest of
the list; when the list holds the name in another case (`Jarvis-Home`), that exact spelling is
sent. A blank setting removes the entry and the picker behaves exactly as before. The entry is
shown whether or not the bridge lists the home project: a turn for a name the bridge does not
know is refused with a sentence, which tells Mark what to fix, while a hidden entry would only
puzzle him.
Selection: (0) a picker with exactly one entry selects it; (1) the last pick, when it is still
an entry; (2) the Default project, when it is an entry; (3) General, when there is one;
(4) nothing. "Never the first of a longer list by itself" still holds: General is not "the
first", it is the chosen default.
Alternative considered: a separate "talk" mode with its own session and no checkout.
Rejected: the wrapper only runs turns in a checkout (D33), the home repository gives the
conversation its instructions and a place to keep ideas, and a mode would be a second road
through the gate.

**D51. The list request: an extension of contract version 1.** A request is now one of two
shapes:

- a turn, unchanged: exactly `{"v":1,"project":…,"prompt":…,"confirm":…}`;
- a list: exactly `{"v":1,"list":true}`. Any other field, or `list` that is not `true`, is
  `bad_request`.

The answer to a list is exactly `{"v":1,"status":"ok","error":null,"projects":[…]}`: the names,
sorted, at most 500, each one that the turn path would resolve (the same name pattern, not
excluded, a work tree directly under the root, no symlink; or on the explicit list and a work
tree). It is computed by calling the turn's own resolver on every candidate, so a listed name
is always a name a turn accepts. A refusal (`disabled` from the kill switch, `bad_request`)
comes in the turn reply's shape, as before: the kill switch answers before stdin is read, so
it cannot know which request it refuses. A list takes no turn lock, touches no pending call,
starts no agent and writes no record: it reads directory names. sshd's own log still has the
connection.
Why not version 2: the version number names the shape of a turn and its reply, and neither
changes. An older wrapper reads `list` as an unknown field and answers `bad_request`; the
bridge then lists the pod's copies (D52), so a bridge that is updated before the agent host
keeps working. A version 2 would have to be negotiated for no gain.
The bridge's parser for the list answer accepts exactly the two shapes: the list shape, or a
turn-shaped reply with `status: "error"`. Anything else is not an answer.

**D52. In `full` mode the agent host decides which projects exist.**
- `GET /projects` asks the agent host (one ssh connection, a 20-second bound; on the bound ssh
  is stopped, since nothing runs there that must be left alone) and answers
  `{"projects":[…],"source":"agent_host"}`. When the agent host cannot be reached, its kill
  switch is set, its wrapper predates the list request, or its answer cannot be read, the
  bridge answers with the pod's copies and `"source":"bridge"`, and logs why. The phone reads
  only `projects`.
- `POST /turn` no longer refuses a project because the pod has no copy. The bridge checks that
  the name has the agent host's shape (`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`) and is not a path;
  the wrapper resolves it per turn, as it already does (D33). A name the agent host does not
  have is answered `no_checkout`.
- The read-only fallback of D30 keeps its four cases, and needs the pod's own copy. Without
  one nothing runs and the bridge says what happened: "The agent host is not reachable right
  now." (could not connect), "The agent host does not have this project.", "Full mode is
  switched off on the agent host.", "This project's settings need the desk."
- In `readonly` mode nothing changes: the pod's list decides, as before.
This replaces the paragraph of D30 that starts "Which projects can be asked about at all is
still decided by the bridge", and decision 6 of `jarvis-full-agent`.
What it widens: a checkout on the agent host that the pod never had (a fork, an archived or
organisation repository cloned there by hand) is now reachable by voice. The agent host's
`project_exclusions` is the list that keeps a checkout out, and it is reviewed before the
agent host is updated (task 3.2). The coding session can already work in each of those
checkouts; Jarvis is its equal (D40).

**D53. After a confirmed `gh repo create`, the wrapper clones the repository.**
- *Which call.* The read-back of `gh repo create <owner>/<name> …` (naming, class `create`)
  now carries what the wrapper needs afterwards, the repository `owner/name`, next to its
  sentence (like a merge carries its number and pin). Nothing else triggers a clone: not
  `gh repo fork`, not `gh gist create`, not a connector tool, not an API call.
- *When.* After a confirming turn whose confirmed call has the outcome exactly `ran` in the
  action log (`record.ran`). `ran, failed`, `not run` and `unknown` clone nothing. The decision
  is the wrapper's, from the grant it issued and the log line with that call's hash; never
  from the agent's words.
- *Whether.* All of: the agent host uses `projects_root` (with an explicit `[projects]` list
  there is no place a new project could appear); the owner is one of `own_owners`; the name
  has the project name shape and is not in `project_exclusions`. When
  `<projects_root>/<name>` is already a project (the turn path resolves it), nothing is cloned
  and it counts as ready. When it exists and is not a project, nothing is touched.
- *How.* `clone_command` (default `gh repo clone`) with `owner/name` and a destination, run by
  the wrapper with the turn's environment (the environment file plus the turn's identity, the
  grant variables removed), stdin closed, `cwd` the root, killed with its process group at
  `clone_timeout_s` (default 120). The destination is a fresh hidden directory under the root
  (`.jarvis-clone-<random>/<name>`, hidden so neither the bridge's scan nor the list sees it)
  that is renamed to `<projects_root>/<name>` only when the clone exited 0 and is a work tree.
  Whatever is left of a failed or timed-out clone is removed. The rename fails if the name
  appeared in the meantime; then the clone is removed and the existing directory is left.
- *What is said.* A new notice: `project_ready`, sentence "It's ready; you can switch to it.",
  or `project_not_ready`, sentence "The repository was created, but I could not set it up as a
  project. That needs the desk." Both come after the agent's own summary, not before it (the
  summary says what was made; the notice says what follows). The bridge adds the response
  header `X-Jarvis-Projects-Changed: 1` with `project_ready`, and the app then fetches its list
  again (when its list comes from the bridge).
- *Record.* The confirmed action in the record gets `"project": "ready"`, or
  `"project": "not ready: <why>"`.
- *What does not change.* The agent never writes into the home directory: a write there is
  class `self` and stays desk-only. The clone is the wrapper's own act, like the notify
  command, after the agent turn has ended. It does not hold a grant and runs no hook.
Alternatives considered:
- *Let the agent clone it.* Rejected: a write directly under the home directory is class
  `self` (D36); opening that class for clones would open it for everything that looks like one.
- *Clone on the next list request.* Rejected: the list would then write, and which repositories
  to clone would have to be guessed from the forge instead of known from the confirmed call.
- *Clone with `--clone` in the agent's own call.* Rejected: it clones into the turn's checkout
  (`jarvis-home/<name>`), a repository inside a repository, and not a project.

**D54. The pod's sync stays as it is.** It runs at pod start and fills the copies the
read-only fallback needs. In `full` mode it no longer decides what can be asked. A new
repository reaches the pod at its next start; until then a turn for it that falls back finds no
copy and says so (D52). Nothing new in the pod has to be configured.

**D55. Contract changes, and the order of updates.** One request shape (list), one reply shape
(list), two notices (`project_ready`, `project_not_ready`), one header to the phone. The
contract test holds both sides against each other for all of them. Mixed versions:

| Bridge | Agent host | What happens |
|---|---|---|
| new | old | the list is refused `bad_request`; `GET /projects` lists the pod's copies; turns work; no clone |
| old | new | the list is never asked; a confirming create turn ends with a notice the old bridge does not know, so that one reply is "an answer I could not read" |
| new | new | as designed |

So the bridge is updated first, then the agent host; the agent host's how-to says so. The app
can be updated at any time: an old app ignores the header and has no General entry.

**D56. The app refreshes its list after `project_ready`.** The bridge says so in the header;
the app fetches `GET /projects` again after playing the reply, when its list comes from the
bridge. A typed list is left alone. The selection does not change by itself: Mark switches.

## Risks / Trade-offs

- **More checkouts are reachable by voice** (D52). Mitigated by the exclusions, reviewed
  before the update; accepted as the same reach the coding session has.
- **The clone needs the forge.** If `gh` cannot reach GitHub, or the new repository is not yet
  visible, the clone fails and Jarvis says it needs the desk, where it is one command. The
  agent cannot do it by voice instead: a clone into the home directory is class `self`.
- **A turn takes longer.** The clone runs inside the confirming turn, under its turn lock, up
  to 120 seconds more. When the bridge stops waiting (170 s) it says the turn is still running;
  the clone still finishes and is recorded.
- **Confirmation switched off.** A `gh repo create` that runs without a read-back (class
  `create` skipped) has no confirming turn, so nothing is cloned. Stated in the how-to.
- **A listed name the pod does not have.** A fallback turn for it cannot answer read-only.
  Said in a fixed sentence, never a silent wrong answer.

## Migration

Nothing changes in `readonly` mode. In `full` mode: update the bridge image, then the agent
host (installer). The app update is independent. No configuration is required: the two new
agent host keys have defaults, and the app's Home project defaults to `jarvis-home`.

## Open Questions

None. The choices made for Mark are listed in the proposal, "Decided by default".
