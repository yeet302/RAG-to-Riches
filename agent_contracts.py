"""Canonical v1 JSON contracts shared by orchestrator and specialist services.

Unknown additive fields are ignored for v1 compatibility. Keep this module in
its repository-root location when deploying services from this checkout.
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator

AgentName = Literal["logs", "docs", "code"]
SourceType = Literal["log", "code", "doc", "incident", "metric"]


class Evidence(BaseModel):
    """One piece of evidence an agent found. `ref` is the exact pointer (file:line, log query, doc section)."""
    source_type: SourceType
    summary: str = Field(min_length=1)
    ref: str = Field(min_length=1)    # e.g. "src/ad/.../AdService.java:L238-L239"
    link: Optional[str] = None        # deep link (GitHub line, Jaeger, doc URL)
    snippet: Optional[str] = None     # raw log lines / code excerpt
    agent: Optional[AgentName] = None

    @field_validator("summary", "ref", mode="before")
    @classmethod
    def strip_required_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class PipelineError(BaseModel):
    """A stable application error code and a safe, actionable explanation."""
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$", max_length=80)
    agent: Optional[AgentName] = None
    stage: Literal["planning", "dispatch", "retrieval", "model", "validation", "synthesis"]
    message: str = Field(min_length=1, max_length=500)
    resolution: str = Field(min_length=1, max_length=1000)
    retryable: bool = False
    http_status: Optional[int] = Field(default=None, ge=100, le=599)


class AgentFinding(BaseModel):
    """JSON contract every specialist agent returns to the orchestrator."""
    agent: AgentName
    status: Literal["ok", "no_evidence", "error", "no_access"] = "ok"
    summary: str                                  # 1-2 sentence takeaway
    hypotheses: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    sources_searched: list[str] = Field(default_factory=list)
    sources_unavailable: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)  # retrieval relevance, not cause probability
    latency_ms: int = Field(default=0, ge=0)
    error: Optional[PipelineError] = None


class AgentRequest(BaseModel):
    """Versioned HTTP request sent to a specialist's configured endpoint."""
    schema_version: Literal["1.0"] = "1.0"
    request_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2000)
    original_question: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    task: str = Field(min_length=1, max_length=2000)
    intent: str
    incident_id: Optional[str] = None
    conversation_id: Optional[str] = None
    evidence_bundle: dict = Field(default_factory=dict)


    @field_validator("question", "request_id", "task", "original_question")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Must not be blank")
        return value
