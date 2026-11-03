"""
Data contracts shared by the front end, the orchestrator and the agents.

Every agent (logs, docs, code) returns an AgentFinding as JSON. The orchestrator
merges findings into a CopilotResponse, which is exactly what the front end renders.
Keep these in sync with frontend/src/types.ts.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Support the documented launch command from this service directory.
from pathlib import Path
import sys

_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agent_contracts import AgentName, SourceType, Evidence, AgentFinding, AgentRequest, PipelineError


class Step(BaseModel):
    title: str
    why: str                                      # rationale, shown next to the step
    command: Optional[str] = None
    expect: Optional[str] = None                  # what you should see if it worked
    risk: Literal["low", "medium", "high", "other_team"] = "low"
    kind: Literal["mitigation", "fix", "verify", "escalate"] = "mitigation"
    link: Optional[str] = None


class TraceEvent(BaseModel):
    """What the orchestrator did, so the UI can show how the answer was reached."""
    stage: int
    agent: str
    action: str
    parallel: bool = False
    latency_ms: int = Field(default=0, ge=0)


RoutingMode = Literal["keyword", "llm"]
AnswerMode = Literal["evidence", "llm"]
QueryIntent = Literal["incident", "how_to", "code_lookup", "logs_lookup", "unknown"]


class AgentAssignment(BaseModel):
    """A bounded, question-specific assignment; never an endpoint or tool command."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    agent: AgentName
    stage: Literal[1, 2]
    question: str = Field(min_length=1, max_length=2000)
    task: str = Field(min_length=1, max_length=2000)


class QueryAnalysis(BaseModel):
    source: Literal["keyword", "llm", "keyword_fallback"]
    intent: QueryIntent
    summary: str
    reason: str
    stage1: list[AgentName]
    stage2: list[AgentName]
    assignments: list[AgentAssignment] = Field(default_factory=list)
    warning: Optional[str] = None


class AnswerSynthesis(BaseModel):
    source: Literal["evidence", "llm", "evidence_fallback"]
    citations: list[str] = Field(default_factory=list)
    warning: Optional[str] = None
    context_truncated: bool = False
    latency_ms: int = Field(default=0, ge=0)


class AskRequest(BaseModel):
    question: str
    routing_mode: Optional[RoutingMode] = None
    answer_mode: Optional[AnswerMode] = None
    incident_id: Optional[str] = None
    conversation_id: Optional[str] = None




class CopilotResponse(BaseModel):
    kind: Literal["diagnosis", "answer"]
    question: str
    answer: str                                   # plain-language answer / headline
    likely_cause: Optional[str] = None
    confidence: Optional[int] = None              # percent
    confidence_reasons: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    not_ruled_out: Optional[str] = None
    not_ruled_out_pct: Optional[int] = None
    sources_unavailable: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    agents_used: list[AgentName] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    request_id: Optional[str] = None
    conversation_id: Optional[str] = None
    findings: list[AgentFinding] = Field(default_factory=list)
    partial: bool = False
    pipeline_status: Literal["ok", "partial", "failed", "no_evidence"] = "ok"
    errors: list[PipelineError] = Field(default_factory=list)
    demo: Literal[False] = False                   # retained for response compatibility; prepared answers are disabled
    analysis: Optional[QueryAnalysis] = None
    synthesis: Optional[AnswerSynthesis] = None


class Incident(BaseModel):
    id: str
    title: str
    severity: Literal["SEV1", "SEV2", "SEV3"]
    status: Literal["Open", "Investigating", "Resolved"]
    service: str
    team: str
    started: str
    assignee: Optional[str] = None
    summary_label: str                            # "Likely cause (90%)" / "Fix worked"
    summary: str
    other_team: bool = False
    question: str                                 # what "See diagnosis" asks the orchestrator


class Feedback(BaseModel):
    conversation_id: Optional[str] = None
    question: str
    step_index: int
    worked: bool
