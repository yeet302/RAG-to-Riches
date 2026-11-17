"""Confidence v2 checked on Navya's code-agent fixture cases (code_agent/accuracy_tests).

These are her offline code-agent cases (BUG-01..03). Her orchestrator test cases (Round 1: B-01..B-04
in the Orchestrator tests tab) need the live services; ask them with
`python compare_confidence.py run --only B-01,B-02,B-03,B-04 --csv results/orchestrator_tests.csv`.

Each recorded output in code_agent/accuracy_tests/outputs/BUG-XX.json is a real AgentFinding that the
code agent returned for that case's question. These tests score it the way investigate() would
(code agent only, AI answer citing its evidence) and check that the score and its reasons are
consistent with what the agent actually reported. New outputs added by Navya are picked up
automatically.

Run: python -m pytest tests/test_confidence_navya_cases.py -v      (from the orchestrator folder)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from confidence import Calibration, ConfidencePolicy, score_confidence
from schemas import AgentFinding, AnswerSynthesis, CopilotResponse, QueryAnalysis

CASES_DIR = Path(__file__).resolve().parents[2] / "code_agent" / "accuracy_tests"
CASES = {c["id"]: c for c in json.loads((CASES_DIR / "cases.json").read_text(encoding="utf-8"))}
OUTPUTS = sorted((CASES_DIR / "outputs").glob("BUG-*.json"))


def scored(output: Path):
    """Score one recorded code-agent finding as a code-only incident answer."""
    finding = AgentFinding.model_validate(json.loads(output.read_text(encoding="utf-8")))
    response = CopilotResponse(
        kind="diagnosis", question=CASES[output.stem]["question"], answer="a", evidence=finding.evidence,
        findings=[finding], synthesis=AnswerSynthesis(source="llm", citations=[e.ref for e in finding.evidence]),
        analysis=QueryAnalysis(source="llm", intent="incident", summary="s", reason="r", stage1=[], stage2=["code"]))
    return finding, score_confidence(response, ConfidencePolicy(), Calibration())


def test_there_is_at_least_one_recorded_case():
    """Navya's outputs folder has at least one recorded code-agent answer to check against."""
    assert OUTPUTS, f"no recorded outputs in {CASES_DIR / 'outputs'}"


@pytest.mark.parametrize("output", OUTPUTS, ids=lambda p: p.stem)
def test_recorded_case_gets_a_score_with_its_calculation_and_reasons(output):
    """Every recorded case gets a 0-95 score, a band, the calculation as the first reason, and one reason
    per part. Logs and docs weren't asked, so agreement and docs relevance are listed as not applicable.
    """
    finding, result = scored(output)
    assert 0 <= result.score <= 95 and result.band in {"high", "medium", "low"}
    assert result.reasons[0].startswith(f"How {result.score}% is calculated:")
    assert set(result.components) == {"agent_context", "measured_accuracy", "grounding"}
    assert any("agreement: not applicable" in r for r in result.reasons)
    if finding.sources_unavailable:   # the agent said its search was limited, so the score must say so too
        assert any("code: partial coverage" in r for r in result.reasons)
        assert result.components["agent_context"] < 0.6 + 0.4 * finding.confidence


def test_bug_01_score_matches_the_formula():
    """BUG-01 by hand: context (0.6 + 0.4 x 0.95) x 0.8 for the reported limits = 0.784; measured
    accuracy 0.5 (code not measured yet); grounding 1.0; each 1/3 after re-weighting -> 76%.
    """
    _, result = scored(CASES_DIR / "outputs" / "BUG-01.json")
    assert result.components == {"agent_context": 0.784, "measured_accuracy": 0.5, "grounding": 1.0}
    assert result.score == 76
