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

## 3. Prompt and docs
- [x] `prompt.md`: Jarvis may start its own builder; the gate guards its calls; long work
      goes to the desk with `run_in_background: true`; end the turn when a builder reports
      a refusal or a call that waits for a yes; ask again for a read-back nobody heard.
- [x] `docs/how-to/agent-host.md`: "Subagents a voice turn may start" and "What a builder
      may do, and what still needs Mark"; the update runbook empties both type lists when
      check 13 fails.
- [x] D59 in `openspec/changes/jarvis-full-agent/design.md`.

## 4. Wiring (host)
- [ ] Merged, then installed on agent-lxc with `install.sh` (new rules and prompt).
- [ ] Host check 13 again with the installed gate and without a test rule: a
      `general-purpose` subagent starts, its `git status` runs, a probe merge from inside
      it is read back (answered no), a `Write` to the gate's configuration is refused.
- [ ] By voice: "fix <a small bug>". The builder opens a pull request; "merge it" is read
      back.
- [ ] Archive, after `jarvis-full-agent`.
