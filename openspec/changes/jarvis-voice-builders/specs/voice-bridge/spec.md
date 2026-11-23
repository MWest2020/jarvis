## ADDED Requirements

### Requirement: Subagents in a voice turn
In `full` mode the system SHALL let a voice turn start, without a spoken yes, a subagent of a built-in type that the gate's rules list: a read-only helper (`web-fetch`, `Explore`, `Plan`, `claude-code-guide` on Claude Code 2.1.322) or a builder (`general-purpose`). It SHALL let such a call through only when the type is named explicitly, no agent file defines that name and the agent files can be read with certainty, the subagent runs inside the turn (`run_in_background` absent or false), and the call holds no isolation, name, team name, permission mode or any other field the rules do not list. Every other call that starts a subagent SHALL be refused, with confirmation on or off, SHALL NOT be confirmable by voice, and SHALL be passed to the coding session at the desk; this includes the types `claude` and `fork`, a call without a type, a remote or worktree isolation, and a subagent that would run on after the turn.

Every tool call a subagent makes SHALL be judged by the same gate, with the same rules and in the same turn state, as the turn's own calls. A gated call made inside a subagent SHALL become the turn's pending call when it can be read back and nothing else is pending, SHALL be read back to the driver when the turn ends from the same config-derived sentence as the turn's own, and its confirmation SHALL release exactly that call in the directory it was made in, once. A gated call inside a subagent that cannot be confirmed by voice SHALL be refused and SHALL reach the driver as passed to the coding session. No gated call made inside a subagent SHALL run without the confirmation, the desk, or the confirmation setting that would let the same call of the turn run. The start of a subagent SHALL NOT be read back.

#### Scenario: Mark asks Jarvis to fix a bug
- **WHEN** Mark says "fix that bug" and the agent starts a `general-purpose` subagent in the turn with the task
- **THEN** the subagent starts without a read-back, works in a worktree, commits, pushes its branch and opens a pull request on Mark's own repository, and Jarvis says what it did

#### Scenario: The builder wants to merge
- **WHEN** the builder calls the merge of its pull request
- **THEN** the call does not run, the turn ends, and Mark hears the merge read back exactly as if the turn had asked for it; after his yes the merge runs once, in the builder's directory

#### Scenario: The builder does something that needs the desk
- **WHEN** the builder writes to Jarvis's own rules, writes an agent file, mails someone other than Mark, or runs a gated command that cannot be read back
- **THEN** the call does not run, nothing is pending, and Jarvis says it passed that on to the coding session

#### Scenario: A subagent that could leave the gate or the turn
- **WHEN** a voice turn, or a subagent inside it, starts a subagent with remote or worktree isolation, a name or team, another permission mode, `run_in_background` true, the type `claude` or `fork`, no type, or a type an agent file defines
- **THEN** no subagent starts, nothing is pending, and the request is passed to the coding session

#### Scenario: Work too large for a turn
- **WHEN** Mark asks for a feature that will not fit in one turn
- **THEN** the agent hands it on by starting the helper to run on after the turn, the gate refuses it, and Jarvis says it passed that on to the coding session
