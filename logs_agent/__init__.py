"""Logs Agent public API for orchestrator consumers."""

from __future__ import annotations

import sys
from pathlib import Path

# Internal modules historically import as top-level names (foundry_agent,
# log_tools, connectors, ...). Keep that working both when this directory
# is the cwd and when the parent package is imported as `logs_agent`.
_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from foundry_agent import ask_agent, investigate_logs  # noqa: E402
from investigation_result import LogInvestigationResult  # noqa: E402
from orchestrator_adapter import handle_agent_request, to_agent_finding  # noqa: E402
from orchestrator_contract import AgentFinding, AgentRequest  # noqa: E402

__all__ = [
    "investigate_logs",
    "ask_agent",
    "LogInvestigationResult",
    "AgentRequest",
    "AgentFinding",
    "handle_agent_request",
    "to_agent_finding",
]
