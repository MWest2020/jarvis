# AGENTS.md — jarvis

**What this is:** a voice bridge from an Android phone to a coding agent (Claude Code first)
on the user's own machine. Machine side ships as a Claude Code plugin; the phone client is
separate (HTTP Shortcuts for the first test, a native Android app later).

**Working method:** OpenSpec. A change that alters what the bridge promises (who can reach it,
what it runs, what it speaks) goes propose → apply → archive, and no code is written before
the change is approved by Mark. Everything else is a GitHub issue.

**Hard rules**
- Reachable only over a private network (Tailscale by default) plus a shared token. Never public.
- No unattended permission bypass: headless agent runs use an explicit tool allow-list.
- Sovereign by default: local STT (faster-whisper) and TTS (Kokoro); hosted speech only as an
  opt-in setting. No Telegram, no agent platforms in the path. Not Piper.
- Nothing of Mark's own setup (habitat, factory, machine names) in the plugin.
- Secrets (the token) never in git.

**Documentation:** everything an agent needs is in `docs/` (start at `docs/index.md`); the
handbook MCP (`.mcp.json`) serves it, so don't scan the whole repo. On every change, check
whether `docs/` needs updating, and update it in the same commit. **No push before that check
is done.** CI (`docs-gates`) fails a PR that changes code without touching `docs/`.

**Commit identity:** MWest2020 <mwest2020@hotmail.com>.
