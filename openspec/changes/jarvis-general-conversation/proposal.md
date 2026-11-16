# Change: jarvis-general-conversation

## Why

Mark, 2026-11-16, verbatim:

> "now i have to pick a project. i also want to be able to just talk to jarvis. like discuss
> an idea and let jarvis make the repo etc."

Jarvis runs in `full` mode (`jarvis-full-agent`): a voice turn is a turn of the coding
session's agent on the agent host, under the same rules and the gate. What is missing is not
power but a place to talk and a way for a new repository to become reachable:

- The app shows a flat list of repositories and makes Mark pick one before he can say
  anything. There is no "just talk" entry.
- A private repository `jarvis-home` exists for that (its `CLAUDE.md` makes Jarvis a
  sparring partner that keeps ideas in `ideas/` and proposes a repository through the usual
  read-back). It is only an ordinary project in the list.
- When Jarvis creates a repository after Mark's spoken yes, that repository cannot be used by
  voice: the bridge's project list is the pod's synced copies (decision 6 of
  `jarvis-full-agent`), refreshed only when the pod starts, and nothing clones it on the agent
  host either.

Mark asked for this directly, so it is built in the same pull request as the proposal; the
pull request is the approval point.

## What Changes

- **"General" in the app.** A pinned first entry, labelled "General", that sends its turns to
  a configurable home project (setting "Home project", default `jarvis-home`). It is selected
  when no pick is remembered and no Default project is set. The home project is not shown a
  second time in the list. The rest of the picker is unchanged. An empty Home project setting
  removes the entry.
- **In `full` mode the agent host's list is the project list.** `GET /projects` asks the agent
  host over the same ssh path, with a new request in the wrapper contract,
  `{"v":1,"list":true}`, answered `{"v":1,"status":"ok","error":null,"projects":[...]}`. A turn
  is no longer refused because the pod has no copy: the bridge checks only the name's shape
  and the wrapper decides, as it already does per turn. The read-only fallback is used only
  for a project the pod also has; otherwise the bridge says what happened ("The agent host is
  not reachable right now.") and runs nothing. In `readonly` mode nothing changes.
- **A new repository becomes a project.** After a confirming turn in which a confirmed
  `gh repo create <owner>/<name>` ran (the action log says "ran"), the wrapper itself (not the
  agent) clones it into `<projects_root>/<name>` with the turn's environment and a bounded
  time (retried by the next requests when it fails). Jarvis then says "It's ready. You can
  switch to it." and the app refreshes its list.
  The agent never writes into the home directory; the class `self` is unchanged.
- **The pod's sync stays as it is.** In `full` mode the agent host is the source of the list;
  the pod's copies only serve the read-only fallback, and a new repository reaches them at the
  pod's next start.

Not in this change: switching project by voice; cloning after a creation that ran without a
confirming turn (confirmation switched off) or through a connector tool; any change to the
gate's rules, classes or read-backs.

## Decided by default

Mark asked for this; these are the choices made for him. Each can be changed later without a
change of promise.

1. **General is an entry, not a mode.** It is the home project under another label. Its
   conversation, memory and rules are those of a project turn in `jarvis-home`; that
   repository's `CLAUDE.md` makes the difference.
2. **Home project default `jarvis-home`**, set in the app; blank removes General. General is
   offered only when the home project is in the list (from the bridge, or typed), so an
   existing install sees no change until it is.
3. **Selection order:** the last pick, then the Default project, then General, then nothing.
4. **The home project is not listed twice**: the General entry stands for it.
5. **The list request is an extension of version 1**, not a version 2 (design D51). An older
   wrapper answers it `bad_request`, and the bridge then lists the pod's copies as before.
6. **When the agent host cannot list**, `GET /projects` lists the pod's copies and says so in
   a `source` field; the app needs no change for that.
7. **Only own repositories are cloned**: the owner must be in the agent host's `own_owners`.
   With an explicit `[projects]` list on the agent host nothing is cloned; Jarvis says the
   repository exists but needs the desk to become a project. A folder already at that name
   counts only when its `origin` is exactly that repository.
8. **Clone time bound 45 seconds** (`clone_timeout_s`, at most 90), a shallow clone, into a
   hidden temporary directory under the root that is moved into place only when complete. A
   failed attempt is retried by the next requests, three attempts in all.
9. **Deployment order: the bridge first, then the agent host** (design D55). A new wrapper
   behind an old bridge would make the reply of a confirming create turn unreadable.
10. **`gh repo create --clone` is refused**, with a reason that tells the agent to ask again
    without it; the wrapper sets the repository up instead.
11. **What became of the repository travels in its own reply field**, never as a notice, so
    no notice and no read-back can hide it.
12. **A phone with typed project names** says so in a header and hears "add it to your list".

## Impact

- Capability `voice-bridge`: three requirements modified (Agent modes, Remote agent adapter,
  Project list), one added (New repository becomes a project). They modify requirements that
  are still in the open changes `jarvis-full-agent` and `add-project-list`, so this change is
  archived after those two.
- Capability `android-client`: one requirement modified (Project picker), in the open change
  `add-android-client`; archived after it.
- Code: `agent_host/` (request, projects, wrapper, naming, config), `bridge/` (remote adapter,
  full mode, server, config), the app's `:core` and `:app`.
- Wire contract D33: one more request shape, one more reply shape, one optional reply field
  (`project`), one more response header to the phone (`X-Jarvis-Projects-Changed`) and one
  request header from it (`X-Jarvis-Projects: typed`). The name pattern and list cap are shared
  in `bridge/contract.py`.
- Docs: `android-app.md`, `bridge-service.md`, `agent-host.md`; the roadmap.
- Exposure: what can be asked in `full` mode grows from "in the pod and on the agent host" to
  "on the agent host". The agent host's `project_exclusions` (or its explicit `[projects]`
  list) is now the only list that keeps a checkout out of reach by voice; it is checked before
  the agent host is updated (tasks 3.x). The coding session itself can already work in every
  one of those checkouts. The bridge's key still opens only the forced command; the list
  request reads directory names and starts no agent.
