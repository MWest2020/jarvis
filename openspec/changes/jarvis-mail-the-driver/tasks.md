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
      in `rules.py`, from the repository's file only (a `rules.d` file that sets it is an
      error); `own_mail` in the configuration, checked for the shape of an address.
- [x] `naming.py`:
      - a call of the `[own_mail]` send tool is nameable only as one mail to one own
        address alone, with the raw value checked (plain ASCII, nothing around it)
        before case is ignored;
      - no other Gmail tool is nameable, `reply` included;
      - `[[tool_actions]]` are never consulted for a Gmail tool.
- [x] `decision.py`: `confirm = "off"` and `confirm_skip` never apply to a Gmail tool
      (security review of #91).
- [x] `[mcp]` words: `label_thread`, `unlabel`, `spam`, `apply_`.
- [x] Tests (`tests/test_agent_host_gate_own_mail.py`):
  - a mail to the own address, threaded or not, is read back without subject or body
    and becomes pending;
  - these all need the desk and are never pending: another recipient, the own address
    plus another, a display name, lookalikes (Kelvin sign, em or no-break space,
    surrounding space), a cc, a bcc, a draft, an attachment, an unknown field and the
    reply tool;
  - forward, trash, draft, label and spam tools are never nameable, and reads stay
    ungated;
  - with `confirm = "off"` or `confirm_skip = ["message"]`, a send to another address
    with an attachment, a forward, a draft send, a reply and a trash all stay refused;
  - a yes releases exactly the mail that was read back, once, and the same yes does not
    release a swapped `to` or an added bcc;
  - without `own_mail`, every mail needs the desk;
  - a host-local `[[tool_actions]]` or `[own_mail]` does not widen the tools.

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
