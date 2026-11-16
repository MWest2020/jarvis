# Tasks: jarvis-general-conversation

Built in the same pull request as the proposal (Mark asked for it). Every step updates
`docs/` in the same commit (AGENTS.md). Archive after `add-project-list`,
`add-android-client` and `jarvis-full-agent`: this change modifies requirements that are
still in those.

- **(host)**: needs the agent host itself; done by the coding session there, not in this PR.
- **(Mark)**: needs Mark's phone.

## 1. Agent host

- [x] 1.1 `agent_host/request.py`: the list request `{"v":1,"list":true}` (exact fields,
  `list` must be `true`); a turn request is unchanged (D51).
- [x] 1.2 `agent_host/projects.py`: `list_projects(config)`, every candidate through
  `resolve_project`, sorted, at most 500 (D51).
- [x] 1.3 `agent_host/wrapper.py`: answer a list with exactly `v`, `status`, `error`,
  `projects`; no lock, no pending call touched, no agent, no record (D51).
- [x] 1.4 `agent_host/naming.py`: the read-back of `gh repo create <owner>/<name>` carries
  `("create", "owner/name")` as its status (D53).
- [x] 1.5 `agent_host/wrapper.py`: after a confirming turn whose confirmed call `ran`, clone
  a confirmed `repo create` into `<projects_root>/<name>` (own owner, valid and not excluded
  name, nothing there yet; hidden temporary directory, renamed when complete, removed
  otherwise; `clone_timeout_s`); notices `project_ready` / `project_not_ready`; the record says
  which (D53).
- [x] 1.6 `agent_host/config.py`, `config.example.toml`: `clone_command` (default
  `["gh", "repo", "clone"]`), `clone_timeout_s` (default 120).
- [x] 1.7 Tests: the list request and its refusals, the listed names equal what a turn
  accepts, the clone and each case where it does not happen (not ran, ran and failed, other
  owner, explicit list, excluded, existing directory, failing and timed-out clone leave
  nothing).

## 2. Bridge

- [x] 2.1 `bridge/remote_adapter.py`: the list request and the parser for its two answer
  shapes; `list_projects()` with a 20-second bound that stops ssh; the two notices and their
  sentences, spoken after the summary; `AgentTurn.projects_changed` (D51, D53).
- [x] 2.2 `bridge/config.py`, `bridge/server.py`: in `full` mode `POST /turn` accepts any name
  of the agent host's shape; `GET /projects` asks the agent host and falls back to the pod's
  list, with `source` (D52).
- [x] 2.3 `bridge/full_mode.py`: no read-only fallback without the pod's copy; the fixed
  sentences instead (D52).
- [x] 2.4 `bridge/server.py`: `X-Jarvis-Projects-Changed: 1` with `project_ready` (D56).
- [x] 2.5 Contract test (`tests/test_contract_with_wrapper.py`): the list request read by the
  wrapper's parser, the wrapper's list reply and its refusals read by the bridge, the notices
  on both sides, the clone through the real wrapper read by the bridge.

## 3. App

- [x] 3.1 `:core`: `ProjectMenu` (the General entry, the home project not listed twice, its
  exact spelling) and the selection order of D50 in `ProjectPick`, with tests.
- [x] 3.2 `:core`: `TurnOutcome.Reply.projectsChanged` from the header, with a test.
- [x] 3.3 `:app`: the Home project setting (default `jarvis-home`, blank removes General), the
  picker shows the entries, the refresh after a reply that says the list changed.

## 4. Docs and roadmap

- [x] 4.1 `docs/how-to/android-app.md`: General, the Home project setting, the order of
  selection, the automatic refresh.
- [x] 4.2 `docs/how-to/bridge-service.md`: the list in `full` mode, `source`, the fallback
  only with the pod's copy, the new header.
- [x] 4.3 `docs/how-to/agent-host.md`: the list request, the clone after a create, the two
  keys, the order of updates (bridge first), review `project_exclusions` before updating.
- [x] 4.4 `docs/reference/roadmap.yml`: the item for this change.

## 5. Rollout (not in this PR)

- [ ] 5.1 Deploy the bridge image (cluster repository).
- [ ] 5.2 **(host)** Review `project_exclusions` on the agent host against the checkouts in
  `projects_root`: every checkout not excluded becomes reachable by voice (D52). Then update
  the agent host with the installer.
- [ ] 5.3 **(host)** Check: `GET /projects` shows the agent host's names with
  `"source": "agent_host"`; a turn for a project the pod lacks runs on the agent host.
- [ ] 5.4 **(Mark)** Install the new APK; General is selected; a spoken idea in General is
  answered; "make a repo for it" is read back, a yes creates it, Jarvis says it is ready, and
  the new project is in the list and answers.
