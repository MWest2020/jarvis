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
  `[own_mail]` in `agent_host/gate_rules.toml`, lists the connector tool that sends one
  mail (Gmail `send_message`) and the input fields it may hold, including
  `replyThreadId` for threading (not `replyToMessageId`, which needs the connector's
  read access and whose server-side handling cannot be checked without sending). A new configuration key,
  `own_mail`, holds the user's own addresses. A call is read back as "Send a mail to
  <address>, with the subject “<subject>”, end of subject." only when all of these hold:
  - `to` holds exactly one own address, as plain ASCII with nothing around it;
  - there is no cc and no bcc;
  - there is no field outside the list, so no draft and no attachment.

  Every other Gmail call has no sentence and needs the desk. That includes the
  connector's `reply`, which may copy a thread's default cc recipients the call does not
  show. A host-local `[[tool_actions]]` rule is never consulted for a Gmail tool, and
  `[own_mail]` is read from the repository's rules only, so a host file cannot widen it.
- **No Gmail tool is ever exempt from confirmation.** `confirm = "off"` and
  `confirm_skip` do not apply to any `mcp__` tool whose name contains "gmail". Before this
  change, either setting let a send to any address with an attachment, a forward or a
  draft through unasked, because the switch is checked before naming.
- **The read-back names the address and the subject.** At first it named the address
  only, under the `jarvis-full-agent` rule that a read-back holds no text from the call:
  speaking the subject puts the agent's own words in the driver's ear at the one moment he
  is asked for a yes. Mark decided otherwise on 2026-11-24 (#92, digest reply: *"zou fijn
  zijn"*). So the subject is the one exception to that rule, for a mail to his own address
  only, and it is made safe to speak: from the `subject` field only, NFKC without control
  or format characters, white space collapsed, capped at 80 characters with "…", framed
  by fixed words on both sides ("with the subject “…”, end of subject"), "without a
  subject" when there is none, and "with a subject that cannot be read out" when it could
  close the frame itself, holds another script's letters or looks like a credential. The
  yes stays bound to the hash of the whole call, so a swapped subject is refused.
- **Mail that is labelled, hidden or marked is gated too.** `label_thread`, `unlabel`,
  `spam` and `apply_` join the `[mcp]` words. Gmail's `label_thread`, `unlabel_thread`,
  `mark_thread_spam` and `apply_sensitive_thread_label` used to run ungated. They have no
  sentence, so they need the desk. Reading mail stays ungated, as before.
- **Jarvis knows.** A paragraph in `agent_host/prompt.md` says how to mail the driver and
  that nobody else can be mailed by voice.

## Impact

- Added requirement in `voice-bridge`: Mail to the driver himself.
- Affected code: `agent_host/gate_rules.toml`, `agent_host/rules.py`,
  `agent_host/naming.py`, `agent_host/targets.py`, `agent_host/decision.py`, `agent_host/config.py`, `agent_host/config.example.toml`,
  `agent_host/prompt.md`.
- Fits into `jarvis-full-agent` (whose spec already says that sending mail is gated and
  read back with every recipient) as a narrow naming rule. It is a change of its own
  because that change is large and mid-flight. Archive it after `jarvis-full-agent`. It
  narrows one rule of that change: a mail to the driver's own address reads back its
  subject. When both are archived, the `jarvis-full-agent` read-back requirement carries
  that exception.
- Nothing of anyone's own setup is in the repository: the address is configuration.
- How it works: [`docs/how-to/agent-host.md`](../../../docs/how-to/agent-host.md#mail-to-yourself).
