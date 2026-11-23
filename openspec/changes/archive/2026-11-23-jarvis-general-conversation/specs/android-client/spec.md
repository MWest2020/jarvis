## MODIFIED Requirements

### Requirement: Project picker
The app SHALL show the project names on the main screen, SHALL send each turn for the selected one, and SHALL remember the selection. The names are the ones typed in the settings, or, when the projects field is empty or `*`, the list the bridge returns on `GET /projects`, fetched with the same client and route rules as a turn when the app starts, when Mark asks for a refresh, and when a reply says the list changed. When the home project (setting Home project, default `jarvis-home`) is in the list, the first entry SHALL be "General", which sends turns for it and stands in for that name in the list. With nothing remembered and no Default project in the list, the app SHALL select General, and without General it SHALL select nothing unless there is only one project. A refresh SHALL NOT change the selection while the selected project is still in the list.

#### Scenario: Switching project
- **WHEN** Mark selects another project and asks a question
- **THEN** the turn is sent for that project, and the same project is selected the next time the app opens

#### Scenario: Projects from the bridge
- **WHEN** the projects field is empty or `*` and the app starts
- **THEN** the app asks the bridge for its projects and shows them in the picker

#### Scenario: Refreshing the list
- **WHEN** Mark taps Refresh on the main screen
- **THEN** the app fetches the list again, keeps the selected project if it is still there, and otherwise selects by the same order as at start

#### Scenario: The list cannot be fetched
- **WHEN** the bridge refuses the token, does not offer the route, or cannot be reached
- **THEN** the app says so in a sentence and keeps the last list it had

#### Scenario: Typed names
- **WHEN** the projects field holds names
- **THEN** the app shows exactly those names (the home project among them as General) and does not ask the bridge

#### Scenario: Just talk
- **WHEN** Mark opens the app for the first time with the default settings
- **THEN** "General" is the first entry and is selected, and a question goes to `jarvis-home`

#### Scenario: The home project in the list
- **WHEN** the bridge lists the home project
- **THEN** it is shown once, as General, and not a second time under its own name

#### Scenario: A remembered pick or a Default project
- **WHEN** Mark picked a project before, or set a Default project that is in the list
- **THEN** that project is selected, not General

#### Scenario: No General
- **WHEN** the Home project setting is blank, or the list does not hold the home project
- **THEN** there is no General entry and the picker works as without this setting

#### Scenario: A new project is ready
- **WHEN** a reply carries `X-Jarvis-Projects-Changed: 1` and the list comes from the bridge
- **THEN** the app fetches the list again once the reply has finished playing, the selected project stays as it was, and an error shown since is kept

#### Scenario: Typed names and a new project
- **WHEN** the projects field holds names
- **THEN** each turn says so to the bridge in a header, so that Jarvis asks Mark to add a new project to the list
