"""Measured request timings and recommendations built from this request's own numbers.

Parallel specialist durations overlap, so the time a request actually waits is the critical path:
planning -> the slowest stage-1 agent -> each stage-2 agent -> answer generation. Recommendations
name the step, its seconds and share of the total, the limit that bounds it, and the setting or
log to look at, instead of generic advice.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Sequence

from schemas import PerformanceSummary, StepTiming

if TYPE_CHECKING:
    from schemas import AgentFinding, QueryAnalysis
    from settings import Settings

# Where each agent's time goes and which knob or log to look at (see each agent's README/.env.example).
AGENT_ADVICE = {
    "logs": ("The logs agent is a Foundry agent that runs model steps and Log Analytics queries one after "
             "another; the [Metrics] lines in diagnostics/local-run/<time>/logs.log show each step's latency and "
             "tools, so you can see which query or model step used the time."),
    "docs": ("The docs agent fetches GitHub and website pages and then calls its model; DOCS_MAX_GITHUB_DOCS "
             "(20) and DOCS_MAX_WEB_PAGES (16) in docs_agent/.env cap how much it reads."),
    "code": ("The code agent runs model rounds and tool calls (search_code, read_source) one after another "
             "inside one budget, CODE_INVESTIGATION_TIMEOUT_SECONDS in code_agent/.env (default 120, kept below "
             "the orchestrator's limit so it can still return a finding); each call is capped by "
             "CODE_REQUEST_TIMEOUT_SECONDS (30). The 'event=finished latency_ms=' lines in "
             "diagnostics/local-run/<time>/code.log show which round or tool call used the time."),
}
STAGE2_NOTE = ("It runs after stage 1 because it uses the logs and docs evidence, so all of its time is added to the "
               "total; a faster stage 1 does not shorten it.")


def _s(ms: float) -> str:
    return f"{ms / 1000:.1f} s"


def _pct(ms: float, total_ms: float) -> str:
    return f"{round(100 * ms / total_ms)}%" if total_ms > 0 else "0%"


def _stages(steps: Sequence[StepTiming], analysis: Optional[QueryAnalysis]) -> tuple[list[StepTiming], list[StepTiming]]:
    agents = [s for s in steps if s.name.startswith("agent:")]
    if analysis is not None:
        first, second = set(analysis.stage1), set(analysis.stage2)
        return ([s for s in agents if s.name[6:] in first], [s for s in agents if s.name[6:] in second])
    names = [s.name for s in steps]
    split = names.index("evidence_handoff") if "evidence_handoff" in names else len(names)
    return ([s for s in agents if names.index(s.name) < split], [s for s in agents if names.index(s.name) > split])


def _sources(findings: Optional[Sequence[AgentFinding]], agent: str) -> str:
    for finding in findings or []:
        if finding.agent == agent:
            count = len(finding.evidence)
            return f", returned {count} source{'s' if count != 1 else ''}"
    return ""


def _limit(settings: Optional[Settings], agent: str, ms: float) -> str:
    if settings is None:
        return ""
    limit = settings.timeout_for(agent)
    variable = f"{agent.upper()}_AGENT_TIMEOUT_SECONDS" if agent in settings.agent_timeouts else "AGENT_TIMEOUT_SECONDS"
    text = f" Its limit is {limit:g} s ({variable} in orchestrator/.env)"
    if ms >= 0.8 * limit * 1000:
        return text + "; it used most of it, so a slightly slower run would time out and lose its evidence."
    return text + "."


def summarize(total_ms: float, steps: list[StepTiming], *, analysis: Optional[QueryAnalysis] = None,
              settings: Optional[Settings] = None,
              findings: Optional[Sequence[AgentFinding]] = None) -> PerformanceSummary:
    slowest = max(steps, key=lambda step: step.latency_ms)
    by_name = {step.name: step for step in steps}
    stage1, stage2 = _stages(steps, analysis)
    routing = by_name.get("routing")
    answer = by_name.get("answer_generation")
    ideas: list[tuple[float, str]] = []   # (seconds this could save, sentence); highest first

    # The critical path, so the reader sees where the wall-clock time went.
    path = []
    if routing:
        path.append(f"planning {_s(routing.latency_ms)} ({_pct(routing.latency_ms, total_ms)})")
    if stage1:
        lead = max(stage1, key=lambda s: s.latency_ms)
        waited = f", waiting on {lead.name[6:]}" if len(stage1) > 1 else f" ({lead.name[6:]})"
        path.append(f"stage 1 {_s(lead.latency_ms)}{waited} ({_pct(lead.latency_ms, total_ms)})")
    for step in stage2:
        path.append(f"{step.name[6:]} {_s(step.latency_ms)} ({_pct(step.latency_ms, total_ms)})")
    if answer:
        path.append(f"answer {_s(answer.latency_ms)} ({_pct(answer.latency_ms, total_ms)})")
    summary = f"Where the {_s(total_ms)} went: " + " → ".join(path) + "." if path else ""

    # Planning that hit its deadline produced no plan: all of that time was lost.
    if routing and analysis is not None and analysis.source == "keyword_fallback":
        limit = settings.llm_timeout_seconds if settings else None
        hit_limit = limit is not None and routing.latency_ms >= 0.9 * limit * 1000
        if hit_limit:
            ideas.append((routing.latency_ms, (
                f"Planning ran for {_s(routing.latency_ms)}, its whole LLM_TIMEOUT_SECONDS={limit:g} s limit, then fell "
                "back to keyword routing, so that time produced no plan. Raise LLM_TIMEOUT_SECONDS in "
                "orchestrator/.env (default 30) so the plan can finish, or set ROUTING_MODE=keyword to skip "
                f"planning and save the {_s(routing.latency_ms)}.")))
        elif analysis.warning and "not configured" not in analysis.warning:
            ideas.append((routing.latency_ms, (
                f"Planning failed after {_s(routing.latency_ms)} and keyword routing was used: {analysis.warning}")))
    elif routing and total_ms > 0 and routing.latency_ms / total_ms >= 0.2:
        ideas.append((routing.latency_ms, (
            f"Planning took {_s(routing.latency_ms)} ({_pct(routing.latency_ms, total_ms)} of the request). Clear "
            "single-source lookups skip it (ROUTING_FAST_PATH); ROUTING_MODE=keyword skips it for every question.")))

    # Stage 1 runs in parallel, so it lasts as long as its slowest agent.
    if len(stage1) > 1:
        ordered = sorted(stage1, key=lambda s: s.latency_ms, reverse=True)
        lead, runner_up = ordered[0], ordered[1]
        gap = lead.latency_ms - runner_up.latency_ms
        agent = lead.name[6:]
        if gap >= 5000:
            ideas.append((gap, (
                f"Stage 1 waited {_s(gap)} for {agent} after {runner_up.name[6:]} finished in "
                f"{_s(runner_up.latency_ms)}: {agent} took {_s(lead.latency_ms)} "
                f"({_pct(lead.latency_ms, total_ms)} of the request{_sources(findings, agent)})."
                f"{_limit(settings, agent, lead.latency_ms)} {AGENT_ADVICE.get(agent, '')}".strip())))
    elif stage1 and total_ms > 0 and stage1[0].latency_ms / total_ms >= 0.2:
        step = stage1[0]
        agent = step.name[6:]
        ideas.append((step.latency_ms, (
            f"{agent.capitalize()} took {_s(step.latency_ms)} ({_pct(step.latency_ms, total_ms)} of the request"
            f"{_sources(findings, agent)}).{_limit(settings, agent, step.latency_ms)} "
            f"{AGENT_ADVICE.get(agent, '')}".strip())))

    # Stage 2 is sequential: every second it takes is added to the total.
    for step in stage2:
        agent = step.name[6:]
        if total_ms > 0 and step.latency_ms / total_ms >= 0.15:
            ideas.append((step.latency_ms, (
                f"{agent.capitalize()} took {_s(step.latency_ms)} ({_pct(step.latency_ms, total_ms)} of the request"
                f"{_sources(findings, agent)}). {STAGE2_NOTE}{_limit(settings, agent, step.latency_ms)} "
                f"{AGENT_ADVICE.get(agent, '')}".strip())))

    if answer and total_ms > 0 and answer.latency_ms / total_ms >= 0.2:
        limit = f" (limit SYNTHESIS_TIMEOUT_SECONDS={settings.synthesis_timeout_seconds:g} s)" if settings else ""
        ideas.append((answer.latency_ms, (
            f"Answer generation took {_s(answer.latency_ms)}{limit}, {_pct(answer.latency_ms, total_ms)} of the "
            "request. Its prompt grows with the amount of evidence; ANSWER_MODE=evidence skips it and shows the "
            "cited evidence only.")))

    local = sum(s.latency_ms for s in steps if s.name in {"setup", "evidence_handoff", "combine", "finalization"})
    if total_ms > 0 and local / total_ms >= 0.2:
        ideas.append((local, (
            f"Work inside the orchestrator (setup, evidence handoff, combine, finalization) took {_s(local)}; "
            "check for very large findings.")))

    recommendations = ([summary] if summary else []) + [text for _, text in sorted(ideas, key=lambda i: -i[0])]
    if len(recommendations) <= 1:
        recommendations.append(f"No single step dominates; the slowest was {slowest.name.replace('agent:', '')} "
                               f"at {_s(slowest.latency_ms)}.")
    return PerformanceSummary(total_ms=round(total_ms, 3), steps=steps,
                              slowest_step=slowest.name, recommendations=recommendations)
