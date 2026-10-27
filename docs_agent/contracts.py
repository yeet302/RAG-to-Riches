"""Shared v1 contract with docs-specific identity defaults for existing callers."""
from typing import Literal
from pydantic import Field

# Support the documented launch command from this service directory.
from pathlib import Path
import sys

_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agent_contracts import AgentRequest
from agent_contracts import AgentFinding as SharedAgentFinding, Evidence as SharedEvidence


class Evidence(SharedEvidence):
    source_type: Literal['doc'] = 'doc'
    agent: Literal['docs'] = 'docs'


class AgentFinding(SharedAgentFinding):
    agent: Literal['docs'] = 'docs'
    evidence: list[Evidence] = Field(default_factory=list)
