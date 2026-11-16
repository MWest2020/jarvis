"""What the bridge and the agent host's wrapper share of the wire contract
(jarvis-full-agent D33, jarvis-general-conversation D51 and D53).

Standard library only: the agent host installs this module next to
`bridge/summary.py` (agent_host/install.sh), so both sides read one definition.
"""
from __future__ import annotations

import re

# The project names the agent host accepts, and so the only ones the bridge sends.
PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

# What one answer to the list request names at most. 500 names of at most 64
# characters stay well inside the 64 KB a reply may be.
MAX_LISTED = 500

# The optional `project` field of a reply (D53): what became of a repository that a
# confirmed `gh repo create` made. Exactly these keys; `reason` is None when `ready`.
PROJECT_FIELDS = frozenset({"name", "ready", "reason"})
# retrying: the clone failed or took too long; the wrapper tries again later.
# needs_desk: it will not be made a project here (another owner, an explicit
#   project list, an excluded name, or the retries are used up).
# name_taken: a different folder with that name is already there.
PROJECT_REASONS = frozenset({"retrying", "needs_desk", "name_taken"})
