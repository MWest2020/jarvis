# Tasks

## 1. Decide
- [x] **Mark.** Build it. Mark, 2026-11-23: "Jarvis moet dit ook kunnen" (mail him, as the
      coding session does). Scope from the coding session: Mark is the only recipient,
      enforced by the gate.
- [ ] **Mark.** Speak the subject in the read-back? Default: no. The read-back names the
      address only, under the rule of `jarvis-full-agent` that it holds no text from the
      call (a `waiting-on-mark` issue).

## 2. Gate
- [x] `[own_mail]` in `gate_rules.toml` (tools, fields, empty_fields, off_fields), parsed
      in `rules.py`; `own_mail` in the configuration, checked for the shape of an address.
- [x] `naming.py`: a call of an `[own_mail]` tool is nameable only as one mail to one own
      address alone; `[[tool_actions]]` are not consulted for those tools.
- [x] `[mcp]` words: `label_thread`, `unlabel`, `spam`, `apply_`.
- [x] Tests (`tests/test_agent_host_gate_own_mail.py`):
  - a mail and a reply to the own address are read back without subject or body, and
    become pending;
  - another recipient, the own address plus another, a display name, a cc, a bcc, a
    draft, an attachment, an unknown field, a reply without `to` and reply-all all need
    the desk and are never pending;
  - forward, trash, draft, label and spam tools are never nameable, and reads stay
    ungated;
  - without `own_mail`, every mail needs the desk;
  - a host-local `[[tool_actions]]` does not widen the tools.

## 3. Prompt and docs
- [x] `prompt.md`: one paragraph on mailing the driver.
- [x] `docs/how-to/agent-host.md`: `own_mail` in the configuration table, and a section
      "Mail to yourself".

## 4. Wiring (host)
- [ ] `own_mail = ["mark@westerweel.work"]` in `/etc/jarvis-agent/config.toml`, and the
      new rules and prompt installed with `install.sh`.
- [ ] By voice: "mail me the open proposals". The read-back is "Send a mail to
      mark@westerweel.work.", the mail arrives after the yes, and "mail it to someone
      else" is refused with the desk.
- [ ] Archive, after `jarvis-full-agent`.
