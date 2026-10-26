"""
Orchestrator v1 wire models for the Logs Agent HTTP adapter.

Keep these aligned with RAG-to-Riches-orchestrator/orchestrator/schemas.py
(AgentRequest, Evidence, AgentFinding). This copy lives here so the Logs
Agent does not import the orchestrator package.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

AgentName = Literal["logs", "docs", "code"]
SourceType = Literal["log", "code", "doc", "incident", "metric"]


class Evidence(BaseModel):
    """One piece of evidence. `ref` is the exact pointer the orchestrator dedupes on."""

    source_type: SourceType
    summary: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    link: Optional[str] = None
    snippet: Optional[str] = None
    agent: Optional[AgentName] = None

    @field_validator("summary", "ref", mode="before")
    @classmethod
    def strip_required_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class AgentFinding(BaseModel):
    """JSON object returned to HTTPAgent (no result/data envelope)."""

    agent: AgentName
    status: Literal["ok", "no_evidence", "error", "no_access"] = "ok"
    summary: str
    hypotheses: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    sources_searched: list[str] = Field(default_factory=list)
    sources_unavailable: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    latency_ms: int = Field(default=0, ge=0)


class AgentRequest(BaseModel):
    """Versioned HTTP request posted by HTTPAgent."""

    schema_version: Literal["1.0"] = "1.0"
    request_id: str
    question: str = Field(min_length=1, max_length=2000)
    task: str
    intent: str
    incident_id: Optional[str] = None
    conversation_id: Optional[str] = None
    evidence_bundle: dict = Field(default_factory=dict)
