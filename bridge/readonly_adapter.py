"""The `readonly` adapter: what the bridge did before there were modes (D30).

Headless Claude Code next to the bridge, the fixed read-only tool list, one
session per project in the bridge's own session file. The command line, the
environment and the session handling are bridge.claude_adapter and
bridge.sessions, unchanged.
"""
from __future__ import annotations

import logging

from bridge import claude_adapter
from bridge.agent import AgentTurn, ConfirmAnswer
from bridge.config import ProjectConfig
from bridge.logging_setup import FILE_ONLY
from bridge.sessions import SessionStore
from bridge.summary import extract_summary

# The log lines of a read-only turn keep the logger they always had.
logger = logging.getLogger("bridge.server")


class ReadonlyAdapter:
    name = "readonly"

    def __init__(self, *, sessions: SessionStore, allowed_tools: tuple[str, ...],
                 timeout_s: int) -> None:
        self._sessions = sessions
        self._allowed_tools = allowed_tools
        self._timeout_s = timeout_s

    def run_turn(self, *, project: ProjectConfig, prompt: str,
                 confirm: ConfirmAnswer | None = None) -> AgentTurn:
        if confirm is not None:
            # Nothing is ever pending here: this adapter cannot act.
            raise ValueError("the readonly adapter takes no confirmation")
        session_id = self._sessions.get(project.name)
        reply_text, new_session_id = claude_adapter.run_turn(
            prompt=prompt,
            working_dir=project.working_dir,
            session_id=session_id,
            allowed_tools=self._allowed_tools,
            timeout_s=self._timeout_s,
        )
        self._sessions.set(project.name, new_session_id)
        logger.info(
            "project=%s session=%s full_reply=%r", project.name, new_session_id, reply_text,
            extra=FILE_ONLY,
        )
        return AgentTurn(spoken=extract_summary(reply_text), ref=new_session_id)
