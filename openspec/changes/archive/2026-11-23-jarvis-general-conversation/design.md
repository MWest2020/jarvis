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
entry of the picker, when the home project is in the list (from the bridge, or typed). It sends turns with `project=<home project>`, the app setting "Home
project" (default `jarvis-home`). The home project's own name is not shown again in the rest of
the list; when the list holds the name in another case (`Jarvis-Home`), that exact spelling is
sent. A blank setting removes the entry and the picker behaves exactly as before. When the
list does not hold the home project, there is no General entry either: on an existing
install with a typed list, or a bridge without `jarvis-home`, nothing changes until the home
project is there.
Selection: (0) exactly one real project in the list selects it (General is that project
under another label, so it is not counted twice); (1) the last pick, when it is still an
entry; (2) the Default project, when it is an entry; (3) General, when there is one;
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
is always a name a turn accepts. An entry that cannot be looked at (permissions) is skipped
and named on stderr. A root that cannot be listed at all is `no_checkout`: no list is not an
empty list, and the bridge then lists the pod's copies and logs why. A refusal (`disabled`
from the kill switch, `bad_request`, `no_checkout`) comes in the turn reply's shape, as
before: the kill switch answers before stdin is read, so it cannot know which request it
refuses. A list touches no pending call, starts no agent and writes no record: it reads
directory names. sshd's own log still has the connection. When clones are pending (D53), the
list starts their retry after it has answered, in a detached child, and only when the turn
lock is free.
The name pattern and the 500 live in one module both sides read, `bridge/contract.py`,
which the installer puts next to `bridge/summary.py` on the agent host.
Why not version 2: the version number names the shape of a turn and its reply, and neither
changes. An older wrapper reads `list` as an unknown field and answers `bad_request`; the
bridge then lists the pod's copies (D52), so a bridge that is updated before the agent host
keeps working. A version 2 would have to be negotiated for no gain.
The bridge's parser for the list answer accepts exactly the two shapes: the list shape, or a
turn-shaped reply with `status: "error"`. Anything else is not an answer.

**D52. In `full` mode the agent host decides which projects exist.**
- `GET /projects` asks the agent host (one ssh connection, an 8-second bound, well under the
  phone's own wait; on the bound ssh
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
(`agent_host/clones.py`.)
- *Which call.* The read-back of `gh repo create <owner>/<name> …` (naming, class `create`)
  now carries what the wrapper needs afterwards, the repository `owner/name`, next to its
  sentence (like a merge carries its number and pin). Only a plain call: the class `create`
  and nothing else, no `sudo` or ssh host in front of it, no variable set before it. Nothing
  else triggers a clone: not `gh repo fork`, not `gh gist create`, not a connector tool, not
  an API call. `gh repo create … --clone` (or `-c`) is refused, not read back: it would clone
  into the turn's checkout, a repository inside a repository. Its refusal tells the agent to
  ask once more without the flag (reason code `create_clone`, no notice), and that form is
  read back as usual.
- *When.* After a confirming turn whose confirmed call has the outcome exactly `ran` in the
  action log (`record.ran`). `ran, failed`, `not run` and `unknown` clone nothing. The decision
  is the wrapper's, from the grant it issued and the log line with that call's hash; never
  from the agent's words.
- *Whether.* All of: the agent host uses `projects_root` (with an explicit `[projects]` list
  there is no place a new project could appear); the owner is one of `own_owners`; the name
  has the project name shape and is not in `project_exclusions`. Otherwise: `needs_desk`.
  When something is at `<projects_root>/<name>` already, nothing is touched: it counts as
  ready only when it is a project whose `origin` resolves, by the forge rule of D36
  (`pushaddr`), to exactly that `owner/name`; anything else is `name_taken`.
- *How.* `clone_command` (default `gh repo clone`) with `owner/name`, a destination and
  `-- --depth 1` (a new repository has one commit or none; a shallow clone has a work tree like
  any other, so the project rules hold), run by the wrapper with the turn's environment (the
  environment file plus the turn's identity, the grant variables removed), stdin closed,
  `cwd` the root, its process group killed at `clone_timeout_s` (default 45, at most 90) and on
  every other way out. The destination is a fresh hidden directory under the root
  (`.jarvis-clone-<random>/<name>`, hidden so neither the bridge's scan nor the list sees it),
  moved to `<projects_root>/<name>` only when the clone exited 0 and is a work tree, with
  `renameat2(RENAME_NOREPLACE)`; where that is missing, a check right before a plain rename,
  whose window is accepted (a plain rename could only replace an empty directory).
  Whatever is left of an attempt is removed.
- *Retries.* A failed or timed-out attempt answers `retrying` and leaves a marker in
  `state_dir/clones/<name>.json`. Every later wrapper request retries one pending clone, the
  oldest, so the turn lock is held for at most one attempt: a turn first, under its turn
  lock, before its agent runs; a list after its answer, in the background (the bridge waits
  only 8 seconds for a list). At most three attempts per repository, each bounded by
  `clone_timeout_s`; after the third the marker is gone and the outcome is `needs_desk`.
  Every retry writes a record (outcome `clone`). Staging directories older than an hour are
  removed at the start of every retry (`record.prune` can do it too when given the root; it
  has no caller until the retention timer of `jarvis-full-agent` task 4.1). The state directory
  is writable by the agent (D40), so a marker is checked in full before use; the worst a
  planted one can do is have one of the user's own repositories cloned to an empty name
  under the root, which the coding session can do anyway. Accepted.
- *What is said.* Not a notice: the reply gets an optional field `project`,
  `{"name", "ready", "reason"}` with `reason` one of `retrying`, `name_taken`, `needs_desk`
  (null when ready), only for a name that is a project name (bridge.contract), on **every**
  reply of that turn: with the summary, with a new read-back,
  with a notice (`new_session`, `settings_changed`, …) and with an error. Notices compete for
  one slot; this must never lose to one, nor silence one. The field is left out when there is
  nothing to say, so a bridge from before it reads every other reply unchanged. The bridge
  says, after the summary (before a read-back, so its question stays last; after the line of a
  failed turn): "It's ready. You can switch to it." / "I'm still setting it up at the desk." /
  "A different folder with that name exists. That needs the desk." / "The repository was
  created, but I could not set it up as a project. That needs the desk." When ready it adds
  `X-Jarvis-Projects-Changed: 1`, and the app fetches its list again (D56). An app that shows
  typed names says so with `X-Jarvis-Projects: typed` on its turns and hears "It's ready. Add
  it to your project list in the app." instead.
- *Record.* The confirmed action in the record gets `"project": "ready"`, or
  `"project": "not ready (<reason>): <why>"`. A repository the forge accepts but no project can
  be named after (`.github`, over 64 characters) is in the record only: the reply has no
  `project` field (the bridge would refuse it) and carries the notice `needs_desk` instead.
- *What does not change.* The agent never writes into the home directory: a write there is
  class `self` and stays desk-only. The clone is the wrapper's own act, like the notify
  command, after the agent turn has ended. It does not hold a grant and runs no hook.
Alternatives considered:
- *Let the agent clone it.* Rejected: a write directly under the home directory is class
  `self` (D36); opening that class for clones would open it for everything that looks like one.
- *Clone on the next list request only.* Rejected: which repositories to clone would have to
  be guessed from the forge instead of known from the confirmed call. A list only retries what
  a confirmed call already asked for.
- *Clone with `--clone` in the agent's own call.* Rejected and refused: it clones into the
  turn's checkout (`jarvis-home/<name>`), a repository inside a repository, and not a project.
- *Notices `project_ready` / `project_not_ready`.* Built first and dropped after review: one
  notice per reply meant a settings change, a new read-back or an error hid the news, and the
  news hid `new_session`.

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
| old | new | the list is never asked; a reply with the new `project` field (only after a confirmed create) is "an answer I could not read"; every other reply is as before |
| new | new | as designed |

So the bridge is updated first, then the agent host; the agent host's how-to says so. The app
can be updated at any time: an old app ignores the header, sends no `X-Jarvis-Projects` and
has no General entry. One more header from the phone (`X-Jarvis-Projects: typed`) is new; a
bridge that does not know it ignores it.

**D56. The app refreshes its list when a new project is ready.** The bridge says so in the
header; the app fetches `GET /projects` again once the reply has finished playing (or was
stopped), when its list comes from the bridge. If a refresh is already running, one more
follows it. An error shown after the reply is not cleared by that refresh. The selection does
not change by itself: whatever is selected stays selected while it is still in the list.
Mark switches. A typed list is left alone; Jarvis tells him to add the name (D53).

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
