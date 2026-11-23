# Change: jarvis-mail-the-driver

## Why

Mark, 2026-11-23: *"en odin kan niet mailen net als jij, of weet niet hoe. Jarvis moet dit
ook kunnen."* The coding session mails Mark every day through the claude.ai Gmail
connector. A `full` turn sees the same connector, and `jarvis-full-agent` promises that
Jarvis may do whatever the session may, mail included, behind a spoken yes. In practice
that mail never happens:

- the gate gates `mcp__claude_ai_Gmail__send_message` (its name says "send", class
  `message`), but no rule can read it back: a connector tool is nameable only through a
  host-local `[[tool_actions]]` rule, and the agent host has none. So every mail is
  refused with `needs_desk`, and the rest of that turn goes to the desk too;
- nothing in the prompt says that Jarvis can mail.

Mark asked for this directly, so it is built in the same PR as this proposal. The PR is
the approval point.

## What Changes

- **A mail to the driver himself can be confirmed by voice.** A new rule section,
  `[own_mail]` in `agent_host/gate_rules.toml`, lists the connector tools that send one
  mail (Gmail `send_message` and `reply`) and the input fields they may hold. A new
  configuration key, `own_mail`, holds the user's own addresses. A call of one of those
  tools is read back as "Send a mail to <address>." only when:
  - `to` holds exactly one own address;
  - there is no cc, no bcc and no reply-all;
  - there is no field outside the list, so no draft and no attachment.

  Every other call of them has no sentence and needs the desk. A host-local
  `[[tool_actions]]` rule is not consulted for these tools, so it cannot widen them.
- **The read-back names the address, not the subject.** The `jarvis-full-agent` rule that
  a read-back holds no text from the call stands. Speaking the subject would put the
  agent's own words in the driver's ear at the one moment he is asked for a yes. Whether to
  speak it anyway is Mark's decision (see tasks).
- **Mail that is labelled, hidden or marked is gated too.** `label_thread`, `unlabel`,
  `spam` and `apply_` join the `[mcp]` words. Gmail's `label_thread`, `unlabel_thread`,
  `mark_thread_spam` and `apply_sensitive_thread_label` used to run ungated. They have no
  sentence, so they need the desk. Reading mail stays ungated, as before.
- **Jarvis knows.** A paragraph in `agent_host/prompt.md` says how to mail the driver and
  that nobody else can be mailed by voice.

## Impact

- Added requirement in `voice-bridge`: Mail to the driver himself.
- Affected code: `agent_host/gate_rules.toml`, `agent_host/rules.py`,
  `agent_host/naming.py`, `agent_host/config.py`, `agent_host/config.example.toml`,
  `agent_host/prompt.md`.
- Fits into `jarvis-full-agent` (whose spec already says that sending mail is gated and
  read back with every recipient) as a narrow naming rule. It is a change of its own
  because that change is large and mid-flight. Archive it after `jarvis-full-agent`.
- Nothing of anyone's own setup is in the repository: the address is configuration.
- How it works: [`docs/how-to/agent-host.md`](../../../docs/how-to/agent-host.md#mail-to-yourself).
