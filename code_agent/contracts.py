"""Compatible subset of orchestrator/schemas.py v1.0.
Keep field names synchronized with the orchestrator's AgentRequest/AgentFinding.
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field


class Evidence(BaseModel):
    source_type: Literal['log', 'code', 'doc', 'incident', 'metric']
    summary: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    link: Optional[str] = None
    snippet: Optional[str] = None
    agent: Optional[Literal['logs', 'docs', 'code']] = None


class AgentFinding(BaseModel):
    agent: Literal['code'] = 'code'
    status: Literal['ok', 'no_evidence', 'error', 'no_access'] = 'ok'
    summary: str
    hypotheses: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    sources_searched: list[str] = Field(default_factory=list)
    sources_unavailable: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    latency_ms: int = Field(default=0, ge=0)


class AgentRequest(BaseModel):
    schema_version: Literal['1.0'] = '1.0'
    request_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2000)
    original_question: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    task: str = Field(min_length=1, max_length=2000)
    intent: str
    incident_id: Optional[str] = None
    conversation_id: Optional[str] = None
    evidence_bundle: dict = Field(default_factory=dict)
