"""Shared v1 contract with docs-specific identity defaults for existing callers."""
from typing import Literal
from pydantic import BaseModel, Field, field_validator

# Support the documented launch command from this service directory.
from pathlib import Path
import sys

_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agent_contracts import AgentRequest
from agent_contracts import AgentFinding as SharedAgentFinding, Evidence as SharedEvidence


class DocsHandoff(BaseModel):
    """A documentation gap and an investigation lead, not a root-cause claim."""

    unexplained_behavior: str = Field(min_length=1, max_length=500)
    next_check: str = Field(min_length=1, max_length=500)

    @field_validator("unexplained_behavior", "next_check", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class Evidence(SharedEvidence):
    source_type: Literal['doc'] = 'doc'
    agent: Literal['docs'] = 'docs'


class AgentFinding(SharedAgentFinding):
    agent: Literal['docs'] = 'docs'
    evidence: list[Evidence] = Field(default_factory=list)
    docs_handoff: DocsHandoff | None = None
