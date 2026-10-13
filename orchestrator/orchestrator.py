"""
The orchestrator: question in, grounded response out.

Flow (matches the architecture diagram, Option 1 selective routing):
  1. classify the question -> RoutePlan (router.py)
  2. stage 1: call logs + docs agents in parallel (asyncio.gather)
  3. merge their JSON findings into one evidence bundle
  4. stage 2: call the code agent with that bundle as its prompt object
  5. synthesize: likely cause, confidence, evidence with sources, steps

DEMO MODE: agents are stubs that return hard-coded findings from demo_data.py
for the 3 demo scenarios. To connect a real agent, implement the Agent protocol
(async run(question, context) -> AgentFinding) and register it in AGENTS.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional, Protocol

from demo_data import SCENARIOS, SUGGESTED_QUESTIONS
from router import RoutePlan, classify
from schemas import AgentFinding, CopilotResponse, TraceEvent


# ---------------------------------------------------------------- agents --

class Agent(Protocol):
    name: str

    async def run(self, question: str, context: dict) -> AgentFinding: ...


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


# Swap DemoAgent for the real implementations as they land, e.g.
#   from agents.logs_agent import LogsAgent   (Lantian, Foundry + log search)
#   from agents.code_agent import CodeAgent   (Navya, git/code search)
AGENTS: dict[str, Agent] = {name: DemoAgent(name) for name in ("logs", "docs", "code")}


# ------------------------------------------------------------- matching --

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
    """Stage-1 evidence bundle that becomes the code agent's prompt object."""
    return {
        "summaries": {f.agent: f.summary for f in findings},
        "hypotheses": [h for f in findings for h in f.hypotheses],
        "evidence": [e.model_dump() for f in findings for e in f.evidence],
        "sources_unavailable": [s for f in findings for s in f.sources_unavailable],
        "instructions": ("Using the evidence above, find the code path, config or recent change that explains "
                         "the symptoms. Return file paths with line ranges and commit ids."),
    }


# ----------------------------------------------------------- orchestrate --

async def investigate(question: str) -> CopilotResponse:
    plan = classify(question)
    scenario = match_scenario(question, plan)
    context: dict = {"scenario": scenario, "plan": plan}
    trace: list[TraceEvent] = [TraceEvent(stage=0, agent="orchestrator", action=f"Routed as '{plan.intent}': {plan.reason}")]

    # Stage 1: parallel
    t0 = time.perf_counter()
    stage1 = await asyncio.gather(*(AGENTS[a].run(question, context) for a in plan.stage1))
    for f in stage1:
        trace.append(TraceEvent(stage=1, agent=f.agent, parallel=len(plan.stage1) > 1,
                                action=f.summary, latency_ms=f.latency_ms))

    # Stage 2: code agent gets stage-1 evidence as its prompt object
    stage2: list[AgentFinding] = []
    if plan.stage2:
        context["evidence_bundle"] = merge_findings(stage1)
        stage2 = list(await asyncio.gather(*(AGENTS[a].run(question, context) for a in plan.stage2)))
        for f in stage2:
            trace.append(TraceEvent(stage=2, agent=f.agent, action=f.summary, latency_ms=f.latency_ms))

    findings = [f for f in [*stage1, *stage2] if f.status == "ok"]
    trace.append(TraceEvent(stage=3, agent="orchestrator", action="Combined and ranked evidence, wrote response",
                            latency_ms=int((time.perf_counter() - t0) * 1000)))
    agents_used = [f.agent for f in [*stage1, *stage2]]

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
