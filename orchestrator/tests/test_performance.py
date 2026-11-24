"""Timing boundaries and question-only selective dispatch regressions."""
import asyncio

import pytest

import orchestrator
from performance import summarize
from schemas import AgentFinding, Evidence, StepTiming
from settings import Settings


@pytest.mark.parametrize("question,expected", [
    ("Where is the retry function defined?", ["code"]),
    ("Search the codebase for retries", ["code"]),
    ("How do I configure tracing?", ["docs"]),
    ("Show checkout error logs from the last hour", ["logs"]),
    ("Checkout keeps timing out. Why?", ["logs", "docs", "code"]),
    ("How do I fix checkout errors?", ["logs", "docs", "code"]),
])
def test_plain_question_dispatches_only_selected_roles(question, expected):
    calls = []
    class Specialist:
        def __init__(self, name):
            self.name = name
        async def run(self, question, context):
            calls.append(self.name)
            return AgentFinding(agent=self.name, summary="Verified match", evidence=[
                Evidence(source_type="code" if self.name == "code" else "doc",
                         summary="Verified match", ref=f"{self.name}:L1")])
    response = asyncio.run(orchestrator.investigate(question,
        agents={name: Specialist(name) for name in ("docs", "logs", "code")},
        settings=Settings(routing_mode="keyword", answer_mode="evidence")))
    assert calls == expected == response.agents_used
    assert response.performance and response.performance.total_ms >= 0
    assert {step.name for step in response.performance.steps} >= {
        "routing", "combine", "answer_generation", *(f"agent:{name}" for name in expected)}


@pytest.mark.parametrize("slow_step", ["routing", "answer_generation"])
def test_timings_include_planning_and_answer_and_combine_excludes_dispatch(monkeypatch, slow_step):
    clock = [0.0]
    monkeypatch.setattr(orchestrator.time, "perf_counter", lambda: clock[0])
    original_analysis = orchestrator.analyze_question
    original_answer = orchestrator.generate_answer
    async def analyze(*args, **kwargs):
        clock[0] += 0.2 if slow_step == "routing" else 0.01
        return await original_analysis(*args, **kwargs)
    async def answer(*args, **kwargs):
        clock[0] += 0.2 if slow_step == "answer_generation" else 0.01
        return await original_answer(*args, **kwargs)
    class Specialist:
        async def run(self, question, context):
            clock[0] += 0.03
            return AgentFinding(agent="docs", status="no_evidence", summary="No match")
    monkeypatch.setattr(orchestrator, "analyze_question", analyze)
    monkeypatch.setattr(orchestrator, "generate_answer", answer)
    response = asyncio.run(orchestrator.investigate("How do I configure tracing?",
        agents={"docs": Specialist()}, settings=Settings(answer_mode="evidence")))
    report = response.performance
    steps = {step.name: step.latency_ms for step in report.steps}
    assert report.total_ms == pytest.approx(240)
    assert steps[slow_step] == pytest.approx(200)
    assert report.slowest_step == slow_step
    assert next(t for t in response.trace if t.stage == 3).latency_ms == 0
    assert next(t for t in response.trace if t.stage == 0).latency_ms == round(steps["routing"])
    assert report.recommendations


def test_parallel_specialist_time_is_not_summed_as_wall_time():
    steps = [StepTiming(name="agent:logs", latency_ms=100, parallel=True),
             StepTiming(name="agent:docs", latency_ms=80, parallel=True)]
    report = summarize(105, steps)
    assert report.total_ms == 105 and report.slowest_step == "agent:logs"
    assert sum(step.latency_ms for step in report.steps) > report.total_ms


def test_symbol_lookup_prefers_definition_to_question_examples_and_imports(tmp_path):
    from agents import LocalSearchAgent
    (tmp_path / "examples.py").write_text('question = "Where is the investigate function defined?"\n')
    (tmp_path / "prompt.py").write_text('function defined? is code_lookup: investigate function defined\n')
    (tmp_path / "caller.py").write_text('from implementation import investigate\n')
    (tmp_path / "implementation.py").write_text('async def investigate(question: str):\n    return question\n')
    finding = asyncio.run(LocalSearchAgent("code", tmp_path).run(
        "Where is the investigate function defined?", {}))
    assert finding.evidence[0].ref.startswith("implementation.py:L1")
    assert finding.evidence[0].summary.startswith("async def investigate")


def test_docs_example_question_does_not_displace_an_explanation(tmp_path):
    from agents import LocalSearchAgent
    (tmp_path / "walkthrough.md").write_text('Click "How does the orchestrator route requests?" to start.\n')
    (tmp_path / "guide.md").write_text('The orchestrator routes requests selectively to required specialists.\n')
    finding = asyncio.run(LocalSearchAgent("docs", tmp_path).run(
        "How does the orchestrator route requests?", {}))
    assert finding.evidence[0].ref.startswith("guide.md:L1")


# ---- Recommendations come from this request's numbers (sprint: "fix this recommended advice") ----
# The run that showed generic advice: planning hit its 10 s limit, logs 63.5 s (9 sources),
# docs 13.4 s (2), code 44.0 s (3), answer 13.1 s, total 130.6 s.
def demo_run():
    from schemas import QueryAnalysis
    steps = [StepTiming(name="setup", latency_ms=0.5), StepTiming(name="routing", latency_ms=10041),
             StepTiming(name="agent:docs", latency_ms=13427, parallel=True),
             StepTiming(name="agent:logs", latency_ms=63470, parallel=True),
             StepTiming(name="evidence_handoff", latency_ms=1), StepTiming(name="agent:code", latency_ms=43957),
             StepTiming(name="combine", latency_ms=3), StepTiming(name="answer_generation", latency_ms=13100),
             StepTiming(name="finalization", latency_ms=58)]
    analysis = QueryAnalysis(source="keyword_fallback", intent="incident", summary="q", reason="r",
                             stage1=["logs", "docs"], stage2=["code"], warning="Azure planning did not finish.")
    findings = [AgentFinding(agent=name, summary="s", evidence=[
        Evidence(source_type="log", summary="x", ref=f"{name}:{i}") for i in range(count)])
        for name, count in (("logs", 9), ("docs", 2), ("code", 3))]
    settings = Settings(llm_timeout_seconds=10, timeout_seconds=60, agent_timeouts={"logs": 180, "code": 180})
    return summarize(130632, steps, analysis=analysis, settings=settings, findings=findings)


def test_recommendations_state_where_the_time_went():
    ideas = demo_run().recommendations
    assert ideas[0] == ("Where the 130.6 s went: planning 10.0 s (8%) → stage 1 63.5 s, waiting on logs (49%) "
                        "→ code 44.0 s (34%) → answer 13.1 s (10%).")


def test_recommendations_are_specific_and_ordered_by_time_saved():
    ideas = demo_run().recommendations[1:]
    assert ideas[0].startswith("Stage 1 waited 50.0 s for logs after docs finished in 13.4 s: logs took 63.5 s "
                               "(49% of the request, returned 9 sources). Its limit is 180 s (LOGS_AGENT_TIMEOUT_SECONDS")
    assert "logs.log" in ideas[0]
    assert ideas[1].startswith("Code took 44.0 s (34% of the request, returned 3 sources). It runs after stage 1")
    assert "CODE_INVESTIGATION_TIMEOUT_SECONDS" in ideas[1] and "code.log" in ideas[1]
    assert ideas[2].startswith("Planning ran for 10.0 s, its whole LLM_TIMEOUT_SECONDS=10 s limit, then fell back")
    assert len(ideas) == 3   # answer generation (10%) and local work are too small to mention
    generic = ("Profile retrieval and model calls", "Narrow retrieval to the service", "Keep independent specialists")
    assert not any(text in idea for idea in ideas for text in generic)


def test_agent_close_to_its_limit_is_flagged():
    steps = [StepTiming(name="routing", latency_ms=100), StepTiming(name="agent:docs", latency_ms=55000)]
    report = summarize(55200, steps, settings=Settings(timeout_seconds=60))
    assert "Its limit is 60 s (AGENT_TIMEOUT_SECONDS in orchestrator/.env); it used most of it" in report.recommendations[1]


def test_slow_answer_generation_names_its_setting():
    steps = [StepTiming(name="routing", latency_ms=100), StepTiming(name="agent:docs", latency_ms=1000),
             StepTiming(name="answer_generation", latency_ms=9000)]
    report = summarize(10200, steps, settings=Settings())
    assert any(i.startswith("Answer generation took 9.0 s (limit SYNTHESIS_TIMEOUT_SECONDS=30 s)")
               for i in report.recommendations)


def test_fast_balanced_run_says_no_single_step_dominates():
    steps = [StepTiming(name="routing", latency_ms=100), StepTiming(name="agent:docs", latency_ms=150, parallel=True),
             StepTiming(name="agent:logs", latency_ms=140, parallel=True),
             StepTiming(name="answer_generation", latency_ms=120)]
    report = summarize(1000, steps)
    assert report.recommendations[-1] == "No single step dominates; the slowest was docs at 0.1 s."
