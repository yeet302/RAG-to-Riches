"""Selective routing, bounded specialist calls, and grounded answer generation."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from uuid import uuid4
from typing import Optional

import httpx
from agents import Agent, build_agents
from answer_synthesis import generate_answer
from demo_data import SCENARIOS, SUGGESTED_QUESTIONS
from router import RoutePlan
from query_analysis import analyze_question
from schemas import AgentFinding, AnswerMode, CopilotResponse, Evidence, RoutingMode, TraceEvent
from settings import Settings, NAMES

log = logging.getLogger("orchestrator")
SETTINGS = Settings.from_env()


class DemoAgent:
    """Stand-in for a real agent: returns the canned finding for the matched scenario."""

    def __init__(self, name: str):
        self.name = name

    async def run(self, question: str, context: dict) -> AgentFinding:
        scenario = context.get("scenario")
        finding = scenario["findings"].get(self.name) if scenario else None
        await asyncio.sleep(0.25)  # small delay so the UI shows the investigating state
        if finding is None:
            return AgentFinding(agent=self.name, status="no_evidence",
                                summary=f"{self.name} agent: no demo data for this question.")
        return finding.model_copy(deep=True)

AGENTS: dict[str, Agent] = ({name: DemoAgent(name) for name in NAMES}
                          if SETTINGS.mode == "demo" else build_agents(SETTINGS))


def match_scenario(question: str, plan: RoutePlan) -> Optional[dict]:
    """Pick the demo scenario whose keywords best match the question."""
    q = question.lower()
    if plan.intent == "how_to":
        return next(s for s in SCENARIOS if s["id"] == "how-to-trigger")
    mentions_ads = bool(re.search(r"\bads?\b|advert|ad service|adservice|getads", q))
    if not mentions_ads:
        return None
    best, best_score = None, 0
    for s in SCENARIOS:
        if s["id"] == "how-to-trigger":
            continue
        score = sum(1 for k in s["keywords"] if re.search(rf"\b{re.escape(k)}\b", q))
        if score > best_score:
            best, best_score = s, score
    return best


def merge_findings(findings: list[AgentFinding]) -> dict:
    """Only successful, validated evidence crosses the second-stage boundary."""
    usable = [f for f in findings if f.status == "ok"]
    return {
        "summaries": {f.agent: f.summary for f in usable},
        "hypotheses": list(dict.fromkeys(h for f in usable for h in f.hypotheses)),
        "evidence": [e.model_dump() for f in usable for e in f.evidence],
        "sources_unavailable": list(dict.fromkeys(s for f in findings for s in f.sources_unavailable)),
        "agent_statuses": {f.agent: f.status for f in findings},
    }


async def call_agent(name: str, registry: dict[str, Agent], question: str,
                     context: dict, timeout: float) -> AgentFinding:
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(registry[name].run(question, context), timeout=timeout)
        finding = AgentFinding.model_validate(result.model_dump() if isinstance(result, AgentFinding) else result)
        if finding.agent != name or any(e.agent not in (None, name) for e in finding.evidence):
            raise ValueError("Specialist identity mismatch")
        for evidence in finding.evidence:
            evidence.agent = name
        if finding.status == "ok" and not finding.evidence:
            finding = finding.model_copy(update={"status": "no_evidence", "confidence": 0.0})
    except asyncio.TimeoutError:
        finding = AgentFinding(agent=name, status="error", summary=f"{name} agent timed out.",
                               sources_unavailable=[f"{name} agent timed out"])
    except httpx.HTTPStatusError as exc:
        denied = exc.response.status_code in (401, 403)
        finding = AgentFinding(agent=name, status="no_access" if denied else "error",
                               summary=f"{name} agent returned HTTP {exc.response.status_code}.",
                               sources_unavailable=[f"{name} agent HTTP {exc.response.status_code}"])
    except Exception as exc:
        # Never include an endpoint, credential, remote body, or raw exception in a user response.
        log.warning("request_id=%s agent=%s failure_type=%s", context["request_id"], name, type(exc).__name__)
        finding = AgentFinding(agent=name, status="error", summary=f"{name} agent failed or returned invalid JSON.",
                               sources_unavailable=[f"{name} agent failed or returned invalid JSON"])
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
    registry = agents if agents is not None else AGENTS
    analysis = await analyze_question(question, settings, routing_mode)
    plan = RoutePlan(analysis.intent, analysis.stage1, analysis.stage2, analysis.reason)
    context = {"request_id": str(uuid4()), "intent": plan.intent,
               "incident_id": incident_id, "conversation_id": conversation_id}
    scenario = match_scenario(question, plan) if settings.mode == "demo" else None
    if settings.mode == "demo":
        context["scenario"] = scenario
    route_action = f"Routed with {analysis.source} as '{plan.intent}': {plan.reason}"
    if analysis.warning:
        route_action += f" {analysis.warning}"
    trace = [TraceEvent(stage=0, agent="orchestrator", action=route_action)]
    started = time.perf_counter()
    findings = []
    for stage, names in ((1, plan.stage1), (2, plan.stage2)):
        if stage == 2:
            context["evidence_bundle"] = merge_findings(findings)
        results = await asyncio.gather(*(call_agent(name, registry, question, context,
                                                  settings.timeout_seconds) for name in names))
        findings.extend(results)
        trace.extend(TraceEvent(stage=stage, agent=f.agent, action=f.summary,
                                parallel=len(names) > 1, latency_ms=f.latency_ms) for f in results)
    trace.append(TraceEvent(stage=3, agent="orchestrator", action="Combined validated evidence with source references",
                            latency_ms=round((time.perf_counter() - started) * 1000)))
    if settings.mode == "demo":
        response = demo_response(question, plan, scenario, findings, trace)
        response.demo = True
        response.request_id = context["request_id"]
        response.conversation_id = conversation_id
        response.findings = findings
        response.analysis = analysis
        return await generate_answer(response, settings, answer_mode)
    response = synthesize(question, plan, findings, trace, context)
    response.analysis = analysis
    return await generate_answer(response, settings, answer_mode)


def demo_response(question: str, plan: RoutePlan, scenario: dict | None,
                  findings: list[AgentFinding], trace: list[TraceEvent]) -> CopilotResponse:
    agents_used = [f.agent for f in findings]
    findings = [f for f in findings if f.status == "ok"]
    if scenario is None:
        return CopilotResponse(
            kind="answer", question=question, agents_used=agents_used, trace=trace,
            answer=("Demo mode: the orchestrator routed this question, but the agents only have demo data for the "
                    "ad service scenarios so far. Try one of the questions below."),
            suggestions=SUGGESTED_QUESTIONS,
        )

    syn = scenario["synthesis"]
    # evidence order: code first (most specific), then logs/metrics, then docs/incidents
    order = {"code": 0, "log": 1, "metric": 2, "incident": 3, "doc": 4}
    evidence = sorted((e for f in findings for e in f.evidence), key=lambda e: order[e.source_type])
    if plan.intent == "how_to":
        return CopilotResponse(kind="answer", question=question, answer=syn["answer"], evidence=evidence,
                               agents_used=agents_used, trace=trace, steps=syn.get("steps", []))
    return CopilotResponse(
        kind="diagnosis", question=question, answer=syn["answer"], likely_cause=syn["likely_cause"],
        confidence=syn["confidence"], confidence_reasons=syn["confidence_reasons"], evidence=evidence,
        not_ruled_out=syn["not_ruled_out"], not_ruled_out_pct=syn["not_ruled_out_pct"],
        sources_unavailable=syn["sources_unavailable"], steps=syn["steps"],
        agents_used=agents_used, trace=trace,
    )
