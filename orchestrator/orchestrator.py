"""Selective routing, bounded specialist calls, and grounded answer generation."""
from __future__ import annotations

import asyncio
import logging
import time
from uuid import uuid4

import httpx
from agents import Agent, build_agents
from answer_synthesis import generate_answer
from failures import annotate_errors, normalize_failure
from router import RoutePlan
from query_analysis import analyze_question
from schemas import AgentFinding, AnswerMode, CopilotResponse, Evidence, RoutingMode, TraceEvent, PipelineError
from settings import Settings

log = logging.getLogger("orchestrator")
SETTINGS = Settings.from_env()


AGENTS: dict[str, Agent] = build_agents(SETTINGS)


def merge_findings(findings: list[AgentFinding]) -> dict:
    """Only successful, validated evidence crosses the second-stage boundary."""
    usable = [f for f in findings if f.status == "ok"]
    return {
        "summaries": {f.agent: f.summary for f in usable},
        "hypotheses": list(dict.fromkeys(h for f in usable for h in f.hypotheses)),
        "evidence": [e.model_dump() for f in usable for e in f.evidence],
        "sources_unavailable": list(dict.fromkeys(s for f in findings for s in f.sources_unavailable)),
        "agent_statuses": {f.agent: f.status for f in findings},
        "errors": [f.error.model_dump() for f in findings if f.error is not None],
    }


async def call_agent(name: str, registry: dict[str, Agent], question: str,
                     context: dict, timeout: float) -> AgentFinding:
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(registry[name].run(question, context), timeout=timeout)
        finding = AgentFinding.model_validate(result.model_dump() if isinstance(result, AgentFinding) else result)
        if finding.agent != name or any(e.agent not in (None, name) for e in finding.evidence):
            raise ValueError("Specialist identity mismatch")
        if finding.error is not None:
            if finding.error.agent not in (None, name):
                raise ValueError("Specialist error identity mismatch")
            finding.error.agent = name
        for evidence in finding.evidence:
            evidence.agent = name
        if finding.status == "ok" and not finding.evidence:
            finding = finding.model_copy(update={"status": "no_evidence", "confidence": 0.0})
    except (asyncio.TimeoutError, httpx.TimeoutException):
        finding = AgentFinding(agent=name, status="error", summary=f"{name} agent timed out.",
                               sources_unavailable=[f"{name} agent timed out"], error=PipelineError(
                                   code="AGENT_TIMEOUT", agent=name, stage="dispatch",
                                   message=f"The {name} agent exceeded its response deadline ({timeout:g} seconds).",
                                   resolution="Retry a narrower query or check the agent and its source latency.", retryable=True))
    except httpx.HTTPStatusError as exc:
        denied = exc.response.status_code in (401, 403)
        finding = AgentFinding(agent=name, status="no_access" if denied else "error",
                               summary=f"{name} agent returned HTTP {exc.response.status_code}.",
                               sources_unavailable=[f"{name} agent HTTP {exc.response.status_code}"], error=PipelineError(
                                   code="AGENT_ACCESS_DENIED" if denied else "AGENT_HTTP_ERROR",
                                   agent=name, stage="dispatch", http_status=exc.response.status_code,
                                   message=f"The {name} endpoint returned HTTP {exc.response.status_code}.",
                                   resolution="Check the service token and endpoint access." if denied
                                       else "Check the endpoint URL and agent service logs, then retry.",
                                   retryable=exc.response.status_code == 429 or exc.response.status_code >= 500))
    except httpx.RequestError:
        finding = AgentFinding(agent=name, status="error", summary=f"{name} agent could not be reached.",
                               sources_unavailable=[f"{name} agent connection failed"], error=PipelineError(
                                   code="AGENT_UNREACHABLE", agent=name, stage="dispatch",
                                   message=f"The {name} endpoint could not be reached.",
                                   resolution="Start the service and check its configured URL and network access.", retryable=True))
    except Exception as exc:
        # Never include an endpoint, credential, remote body, or raw exception in a user response.
        log.warning("request_id=%s agent=%s failure_type=%s", context["request_id"], name, type(exc).__name__)
        finding = AgentFinding(agent=name, status="error", summary=f"{name} agent failed or returned invalid JSON.",
                               sources_unavailable=[f"{name} agent failed or returned invalid JSON"], error=PipelineError(
                                   code="AGENT_INVALID_RESPONSE", agent=name, stage="validation",
                                   message=f"The {name} agent failed or returned an invalid handoff.",
                                   resolution="Check the agent logs and its shared request/response schema using the request ID."))
    finding = normalize_failure(finding)
    if finding.status in {"error", "no_access"} and not finding.sources_unavailable:
        finding.sources_unavailable = [f"{name} agent reported {finding.status}"]
    finding.latency_ms = max(0, round((time.perf_counter() - started) * 1000))
    log.info("request_id=%s agent=%s status=%s latency_ms=%s", context["request_id"], name,
             finding.status, finding.latency_ms)
    return finding


def synthesize(question: str, plan: RoutePlan, findings: list[AgentFinding],
               trace: list[TraceEvent], context: dict) -> CopilotResponse:
    """Collect usable evidence and build the deterministic fallback before generation."""
    evidence: list[Evidence] = []
    seen = set()
    # Preserve first-stage evidence before the codebase follow-up.
    for finding in findings:
        if finding.status != "ok":
            continue
        for item in finding.evidence:
            key = (item.source_type, item.ref, item.link)
            if key not in seen:
                seen.add(key)
                evidence.append(item)
    unavailable = list(dict.fromkeys(s for f in findings for s in f.sources_unavailable))
    partial = bool(unavailable) or any(f.status in {"error", "no_access"} for f in findings)
    if evidence:
        answer = "I found the following relevant sources:\n\n" + "\n".join(
            f"- {e.summary} [{e.ref}]" for e in evidence)
        if plan.intent == "incident":
            answer += "\n\nThese matches are investigation evidence; they do not yet establish a root cause."
        if partial:
            answer += "\n\nSome sources were unavailable; this answer is incomplete."
    else:
        answer = "I could not find supporting evidence in the available sources."
        if partial:
            answer += " Some agents or sources were unavailable."
    no_evidence = [f.agent for f in findings if f.status == "no_evidence"]
    if no_evidence:
        answer += "\n\nNo matching evidence returned by: " + ", ".join(no_evidence) + "."
    # Scores indicate retrieval relevance supplied by specialists, never root-cause probability.
    # Keep diagnosis confidence unset until a validated diagnosis/synthesis policy exists.
    return CopilotResponse(
        kind="diagnosis" if plan.intent == "incident" else "answer", question=question,
        answer=answer, evidence=evidence, sources_unavailable=unavailable,
        agents_used=[f.agent for f in findings], trace=trace, findings=findings, partial=partial,
        request_id=context["request_id"], conversation_id=context.get("conversation_id"), demo=False,
    )


async def investigate(question: str, *, incident_id: str | None = None,
                      conversation_id: str | None = None, agents: dict[str, Agent] | None = None,
                      settings: Settings | None = None,
                      routing_mode: RoutingMode | None = None,
                      answer_mode: AnswerMode | None = None) -> CopilotResponse:
    question = question.strip()
    if not question or len(question) > 2000:
        raise ValueError("question must contain 1 to 2000 characters")
    settings = settings or SETTINGS
    registry = agents if agents is not None else (AGENTS if settings is SETTINGS else build_agents(settings))
    analysis = await analyze_question(question, settings, routing_mode)
    plan = RoutePlan(analysis.intent, analysis.stage1, analysis.stage2, analysis.reason)
    assignments = {assignment.agent: assignment for assignment in analysis.assignments}
    context = {"request_id": str(uuid4()), "intent": plan.intent,
               "original_question": question,
               "incident_id": incident_id, "conversation_id": conversation_id}
    route_action = f"Routed with {analysis.source} as '{plan.intent}': {plan.reason}"
    if analysis.warning:
        route_action += f" {analysis.warning}"
    trace = [TraceEvent(stage=0, agent="orchestrator", action=route_action)]
    started = time.perf_counter()
    findings = []
    for stage, names in ((1, plan.stage1), (2, plan.stage2)):
        if stage == 2:
            context["evidence_bundle"] = merge_findings(findings)
        # Give each concurrent specialist its own context and its generated assignment.
        results = await asyncio.gather(*(call_agent(
            name, registry, assignments[name].question,
            {**context, "task": assignments[name].task}, settings.timeout_for(name),
        ) for name in names))
        findings.extend(results)
        trace.extend(TraceEvent(stage=stage, agent=f.agent, action=f.summary,
                                parallel=len(names) > 1, latency_ms=f.latency_ms) for f in results)
    trace.append(TraceEvent(stage=3, agent="orchestrator", action="Combined validated evidence with source references",
                            latency_ms=round((time.perf_counter() - started) * 1000)))
    response = synthesize(question, plan, findings, trace, context)
    response.analysis = analysis
    return annotate_errors(await generate_answer(response, settings, answer_mode))
