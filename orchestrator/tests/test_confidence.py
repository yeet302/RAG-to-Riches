"""The confidence score: computed only from what the agents returned, and explained."""
from __future__ import annotations

import asyncio
import json

import pytest

import orchestrator
from confidence import ConfidencePolicy, apply_confidence, score_confidence
from schemas import AgentFinding, AnswerSynthesis, CopilotResponse, Evidence, PipelineError, QueryAnalysis, Step
from settings import Settings

POLICY = ConfidencePolicy()


def ev(agent, ref):
    kind = {"logs": "log", "docs": "doc", "code": "code"}[agent]
    return Evidence(source_type=kind, agent=agent, ref=ref, summary=f"{agent} evidence")


def response(statuses=None, relevance=0.8, synthesis="llm", cited=None, truncated=False,
             planning="llm", **kw):
    """A response built the way investigate() builds one from the agents' findings."""
    statuses = statuses or {"logs": "ok", "docs": "ok", "code": "ok"}
    findings, evidence = [], []
    for agent, status in statuses.items():
        items = [ev(agent, f"{agent}.ref")] if status == "ok" else []
        error = PipelineError(code="AGENT_TIMEOUT", agent=agent, stage="dispatch", message="timed out",
                              resolution="retry") if status == "error" else None
        findings.append(AgentFinding(agent=agent, status=status, summary=agent, evidence=items,
                                     confidence=relevance if status == "ok" else 0, error=error))
        evidence.extend(items)
    citations = cited if cited is not None else [e.ref for e in evidence]
    return CopilotResponse(
        kind="diagnosis", question="q", answer="a", evidence=evidence, findings=findings,
        synthesis=AnswerSynthesis(source=synthesis, citations=citations if synthesis == "llm" else [],
                                  context_truncated=truncated),
        analysis=QueryAnalysis(source=planning, intent="incident", summary="s", reason="r",
                               stage1=["logs", "docs"], stage2=["code"]), **kw)


def test_strong_corroborated_answer_scores_high_and_is_capped():
    r = score_confidence(response(relevance=1.0), POLICY)
    assert r.band == "high" and r.score == 95
    assert any("Capped at 95%" in x for x in r.reasons)


def test_no_evidence_scores_zero_and_says_why():
    r = score_confidence(response({"logs": "error", "docs": "no_evidence", "code": "no_access"}), POLICY)
    assert r.score == 0 and r.band == "low"
    assert "No supporting evidence" in r.reasons[0] and "logs (AGENT_TIMEOUT)" in r.reasons[1]


def test_an_unavailable_agent_lowers_the_score_and_is_named():
    full = score_confidence(response(), POLICY).score
    partial = score_confidence(response({"logs": "error", "docs": "ok", "code": "ok"}), POLICY)
    assert partial.score < full
    assert partial.reasons[0] == "2 of 3 agents returned usable evidence (logs: AGENT_TIMEOUT)."


def test_answer_resting_on_one_source_scores_lower_than_corroborated():
    one = score_confidence(response(cited=["docs.ref"]), POLICY)
    three = score_confidence(response(), POLICY)
    assert one.score < three.score
    assert any("Only the docs agent supports the answer" in x for x in one.reasons)


def test_agents_own_relevance_scores_move_the_score():
    weak = score_confidence(response(relevance=0.2), POLICY)
    strong = score_confidence(response(relevance=0.9), POLICY)
    assert weak.score < strong.score
    assert weak.components["relevance"] == 0.2
    assert "Agents rated their matches 20% relevant on average." in weak.reasons


def test_grounding_order_validated_ai_beats_evidence_list_beats_failed_ai():
    s = {src: score_confidence(response(synthesis=src), POLICY).components["grounding"]
         for src in ("llm", "evidence", "evidence_fallback")}
    assert s["llm"] > s["evidence"] > s["evidence_fallback"]
    assert score_confidence(response(truncated=True), POLICY).components["grounding"] < s["llm"]


def test_planning_fallback_is_a_small_penalty_with_a_reason():
    normal = score_confidence(response(relevance=0.6), POLICY)
    fallback = score_confidence(response(relevance=0.6, planning="keyword_fallback"), POLICY)
    assert fallback.score < normal.score
    assert any("keyword routing" in x for x in fallback.reasons)


def test_step_sources_count_as_support():
    r = response(cited=["docs.ref"], steps=[Step(title="t", why="w", evidence_refs=["code.ref"])])
    assert "2 independent agents" in " ".join(score_confidence(r, POLICY).reasons)


def test_apply_confidence_fills_every_response_field():
    r = apply_confidence(response(), POLICY)
    assert isinstance(r.confidence, int) and r.confidence_band and r.confidence_reasons
    assert set(r.confidence_components) == {"coverage", "corroboration", "relevance", "grounding"}


@pytest.mark.parametrize("bad", [
    {"weights": {"coverage": 1.0}},
    {"weights": {"coverage": 0.5, "corroboration": 0.5, "relevance": 0.5, "grounding": 0}},
    {"weights": {"coverage": -0.1, "corroboration": 0.5, "relevance": 0.3, "grounding": 0.3}},
    {"high_threshold": 40, "medium_threshold": 50},
])
def test_invalid_policies_are_rejected(bad):
    with pytest.raises(ValueError):
        ConfidencePolicy(**bad)


def test_policy_file_is_validated(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"high_threshold": 80}))
    assert ConfidencePolicy.from_file(path).high_threshold == 80
    path.write_text(json.dumps({"surprise": 1}))
    with pytest.raises(ValueError, match="unknown"):
        ConfidencePolicy.from_file(path)


def test_shipped_default_policy_file_is_valid():
    from confidence import DEFAULT_POLICY_PATH
    assert ConfidencePolicy.from_file(DEFAULT_POLICY_PATH) == ConfidencePolicy()


def test_every_investigation_answer_gets_a_confidence(tmp_path):
    (tmp_path / "runbook.md").write_text("# Checkout\nCheckout timeout: raise the upstream timeout.\n")
    config = Settings(mode="local", routing_mode="keyword", answer_mode="evidence",
                      docs_root=tmp_path, code_root=tmp_path, logs_root=tmp_path)
    for question in ("How do I fix the checkout timeout?", "Why is checkout timing out?"):
        r = asyncio.run(orchestrator.investigate(question, settings=config))
        assert r.confidence is not None and r.confidence_band and r.confidence_reasons
