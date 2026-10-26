"""Docs subset of orchestrator v1 wire models (reference: 3fbce0499cf5)."""
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class AgentRequest(BaseModel):
    schema_version: Literal['1.0'] = '1.0'
    request_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2000)
    task: str
    intent: str
    incident_id: Optional[str] = None
    conversation_id: Optional[str] = None
    evidence_bundle: dict = Field(default_factory=dict)

    @field_validator('question', 'request_id')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Must not be blank')
        return value


class Evidence(BaseModel):
    source_type: Literal['doc'] = 'doc'
    agent: Literal['docs'] = 'docs'
    summary: str
    ref: str
    link: Optional[str] = None
    snippet: Optional[str] = None


class AgentFinding(BaseModel):
    agent: Literal['docs'] = 'docs'
    status: Literal['ok', 'no_evidence', 'error', 'no_access'] = 'ok'
    summary: str
    hypotheses: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    sources_searched: list[str] = Field(default_factory=list)
    sources_unavailable: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    latency_ms: int = Field(default=0, ge=0)
