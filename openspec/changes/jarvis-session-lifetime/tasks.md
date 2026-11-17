# Tasks: jarvis-session-lifetime

## 1. Idle reset (#61)
- [x] 1.1 `session_idle_minutes` in `agent_host/config.py` (default 60, 0 off, a whole
  number of minutes) and in `config.example.toml`
- [x] 1.2 `agent_host/lifetime.py`: `TurnClock` (`sessions/last_turn.json`, a planted or
  future value read as unknown) and `choose` (discarded, new, closed, idle, rotated,
  resumed); the wrapper writes the time when a turn ended `ok`
- [x] 1.3 The notice `new_session` for `idle`, in the existing notice order; the record's
  `session` field

## 2. Closing the conversation
- [x] 2.1 `agent_host/prompt.md`: when to offer (once, a finished request, nothing pending,
  not in a back-and-forth, not again after a no), the words that close, the goodbye and the
  last line `SESSION: close`
- [x] 2.2 The wrapper: the marker only as the last line (or the end of a last summary
  line), no argument, cut off before the spoken line; drops the session of the request's
  project only; ignored while a call is pending and on a failed turn; recorded
- [x] 2.3 Real turns with the prompt (2026-11-17, Claude Code 2.1.322, outside the wrapper):
  the close in English and Dutch, the offer after a finished request, no offer after a
  single question; see docs/how-to/agent-host.md
- [ ] 2.4 After install: one close and one idle turn through the wrapper on the agent host,
  the records' `session` field checked

## 3. Tests and docs
- [x] 3.1 Unit tests and wrapper tests with the fake `Popen`, the confirmation harness and
  the real entrypoint with the fake `claude` of CI (`tests/test_agent_host_lifetime.py`)
- [x] 3.2 `docs/how-to/agent-host.md` (How long a session lives, the records),
  `docs/how-to/bridge-service.md`, `docs/index.md`, the roadmap
