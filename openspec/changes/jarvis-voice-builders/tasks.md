# Tasks

## 1. Decide
- [x] **Mark.** A voice turn starts its own builder. Mark, by voice to Jarvis, 2026-11-23,
      after a builder for a bug fix was refused: "Now why was it refused? You need to be
      able to start your own sessions." His rule: a voice request means build it.
- [x] **Coding session, on the merits.** `general-purpose` only; `claude` and `fork` stay
      refused (proposal). No spoken yes to start a builder (the default of the
      assignment): the gate guards every risky call inside it, and a read-back of the start
      could only say "start a builder", never the agent's task.

## 2. Gate
- [x] `[subagents] builder_types = ["general-purpose"]` in `gate_rules.toml`, parsed in
      `rules.py` (`subagent_builder_types`; `subagent_types` is both lists); a host-local
      file may add a built-in, an agent file still shadows it.
- [x] `classify.py`: no change to the rule itself; the docstrings say what a builder is.
- [x] `decision.py` and `gate`: the subagent refusal names every type that can start and
      that a helper that would outlive the turn goes to the desk; every denial tells a
      subagent to hand back at once.
- [x] Tests:
  - `tests/test_agent_host_gate_builders.py`: a `general-purpose` builder starts without a
    yes (confirmation on and off), in every spelling, as `Agent` and `Task`; remote and
    worktree isolation, name, team, mode and background still refuse, as do `claude`,
    `fork`, `statusline-setup`, no type and an agent file named `general-purpose`; through
    the real wrapper and gate, with the hook input of a call made inside a subagent: a
    builder's edits, commit, branch push and pull request run; its merge is read back
    like the turn's own and its yes releases it once, in the builder's directory, and not
    a different call; a second gated call is not read back; a call that needs the desk
    (unnameable, `self`, an agent file, a mail to someone else) reaches the desk with
    notice `needs_desk`; a builder cannot start a subagent the turn cannot; with
    confirmation off the desk-only calls stay refused.
  - `tests/test_agent_host_gate_entrypoint.py`: the same through the real gate process.
  - `tests/test_agent_host_gate_subagents.py`: updated for the new type list.

## 2b. Security review of #96 (2026-11-23)
- [x] Holes in main fixed first, in their own PR (#98): a nested `claude` session and
      rewriting the turn checkout's tree are desk-only; the real fix for the second is #97.
- [x] Fan-out: `max_builders = 2` counted per turn in `TurnState` (`builders`), `Agent`/`Task`
      with `agent_id` in the hook input refused (`subagent:nested`), and
      `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1`, `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS=2`
      pinned in `turn-settings.json`.
- [x] Detached processes outlive the turn: proposal and how-to corrected; cgroup scope per
      turn is #99.
- [x] Tests use a recorded 2.1.322 subagent hook input (`tests/data/`), `cwd` the checkout:
      `git -C <worktree>` work runs, a gated `cd <worktree> && ...` is run-alone, a gated
      `git -C` push needs the desk, a push to main from the checkout is read back, the
      nested-agent refusal and the builder count. The prompt tells builders to use
      `git -C` and `--repo`.
- [x] Host: check that 2.1.322 honours both `CLAUDE_CODE_MAX_*` variables (2026-11-23,
      installed `df2c7af`). Depth 1: a builder is not offered `Agent` or `Task` (its tool
      list; `ToolSearch select:Agent,Task` answers "No matching deferred tools found"; its
      meta says `spawnDepth: 1`), while a depth-1 subagent without the pin has `Agent`.
      Concurrency 2: of three `Explore` helpers started at once, two ran side by side and
      Claude Code refused the third ("Concurrent subagent limit reached. You can run 2
      subagents at once."), turn `8f5326ef…`.
- [ ] Host: record a subagent hook input on the installed gate to compare with the fixture.
      Not possible as installed: the action log keeps `tool`, `what`, `cwd`, `status` and
      `hash`, never the hook input. What could be compared (2026-11-23): the hook `cwd` of a
      builder's call is the turn checkout, also for `git -C <worktree>` (as in the fixture);
      the real `agent_id` is 17 characters (`a` and 16 hex, e.g. `afad571b0e1e6abd0`, twelve
      subagents), the fixture's 16 (`a5f3c2e1b7d94e08`); the gate only tests that the key is
      present. A full record needs a hook that dumps its input.

## 3. Prompt and docs
- [x] `prompt.md`: Jarvis may start its own builder; the gate guards its calls; long work
      goes to the desk with `run_in_background: true`; end the turn when a builder reports
      a refusal or a call that waits for a yes; ask again for a read-back nobody heard.
- [x] `docs/how-to/agent-host.md`: "Subagents a voice turn may start" and "What a builder
      may do, and what still needs Mark"; the update runbook empties both type lists when
      check 13 fails.
- [x] D59 in `openspec/changes/jarvis-full-agent/design.md`.

## 4. Wiring (host)
- [x] Merged, then installed on agent-lxc with `install.sh` (new rules and prompt):
      `/usr/local/lib/jarvis-agent/VERSION` is `df2c7af`, 2026-11-23.
- [x] Host check 13 again with the installed gate and without a test rule: a
      `general-purpose` subagent starts, its `git status` runs, a probe merge from inside
      it is read back (answered no), a `Write` to the gate's configuration is refused.
      2026-11-23, installed `df2c7af`, Claude Code 2.1.322, through the installed wrapper in
      a throwaway sandbox clone: builder `git -C <worktree> status` ran (turn `a33b07b0…`);
      its `gh pr merge 1 --repo MWest2020/jarvis-sandbox-probe --match-head-commit …` read
      back as "Merge pull request 1 on jarvis-sandbox-probe, at commit 1177e37. Status
      unknown." (turn `66781768…`), answered no, record `a2041f5e…` `cancelled`, nothing
      ran; its `Write` of `/etc/jarvis-agent/config.toml` refused `self:write` (turn
      `dc464318…`, file unchanged). Also `self:session` for `claude --version` in the turn
      and in a builder (`301cc557…`, `46b192f6…`), `self:checkout-tree` for `git stash` in
      the turn checkout (`2094e202…`) while `git -C <linked worktree> checkout other` ran
      (`d1354b4c…`). The turn agent declined by itself (its prompt) to make a third builder
      call, `claude -p` and `git checkout <branch>` in the checkout, so `subagent:too-many`
      and `subagent:nested` were shown on the installed `decision.decide` in a harness, not
      live; a builder has no `Agent` tool to nest with.
- [ ] By voice: "fix <a small bug>". The builder opens a pull request; "merge it" is read
      back.
- [ ] Archive, after `jarvis-full-agent`.
