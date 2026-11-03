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
