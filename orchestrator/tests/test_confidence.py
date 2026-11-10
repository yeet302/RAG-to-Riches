"""Confidence score v2 (CONFIDENCE_DEFINITION.md): built only from what the sub-agents returned."""
from __future__ import annotations

import asyncio
import json

import pytest

import orchestrator
from confidence import (DEFAULT_CALIBRATION_PATH, DEFAULT_POLICY_PATH, Calibration, ConfidencePolicy,
                        Measurement, apply_confidence, key_terms, score_confidence, signals)
from schemas import AgentFinding, AnswerSynthesis, CopilotResponse, Evidence, PipelineError, QueryAnalysis, Step
from settings import Settings

POLICY = ConfidencePolicy()
UNMEASURED = Calibration()
QUESTION = "Why are GetAds requests failing with UNAVAILABLE errors in the Ad Service?"
LOG_REF = "OTelLogs/2026-11-09T05:16:05Z/ad/b0ef/1a2b"
LOG_LINE = "GetAds Failed with status UNAVAILABLE [inject:ads-unavailable]"
CODE_MATCH = 'Lane 0 throws Status.UNAVAILABLE.withDescription("inject:ads-unavailable") when adFailure is on'
CODE_OTHER = "CPULoad starts busy threads when adHighCpu is enabled"
DOC_MATCH = "adFailure: Generate an error for GetAds 1/10th of the time"


def finding(agent, text=None, status="ok", relevance=0.8, gaps=(), searched=(), ref=None):
    kind = {"logs": "log", "docs": "doc", "code": "code"}[agent]
    ref = ref or {"logs": LOG_REF, "docs": "https://opentelemetry.io/docs/demo/feature-flags/",
                  "code": "src/ad/src/main/java/oteldemo/AdService.java@4a70112:L258-L262"}[agent]
    items = [Evidence(source_type=kind, agent=agent, ref=ref, summary=text or f"{agent} evidence",
                      snippet=text if agent == "logs" else None)] if status == "ok" else []
    error = PipelineError(code="AGENT_TIMEOUT", agent=agent, stage="dispatch", message="timed out",
                          resolution="retry") if status == "error" else None
    return AgentFinding(agent=agent, status=status, summary=agent, evidence=items,
                        confidence=relevance if status == "ok" else 0, error=error,
                        sources_unavailable=list(gaps), sources_searched=list(searched))


def incident(logs=None, docs=None, code=None, synthesis="llm", cited=None, truncated=False,
             planning="llm", question=QUESTION, **kw):
    """A response built the way investigate() builds one."""
    findings = [f for f in (logs, docs, code) if f is not None]
    evidence = [e for f in findings for e in f.evidence]
    citations = cited if cited is not None else [e.ref for e in evidence]
    return CopilotResponse(
        kind="diagnosis", question=question, answer="a", evidence=evidence, findings=findings,
        synthesis=AnswerSynthesis(source=synthesis, citations=citations if synthesis == "llm" else [],
                                  context_truncated=truncated),
        analysis=QueryAnalysis(source=planning, intent="incident", summary="s", reason="r",
                               stage1=[f.agent for f in findings if f.agent != "code"], stage2=["code"]), **kw)


def agreeing(**kw):
    return incident(finding("logs", LOG_LINE), finding("docs", DOC_MATCH),
                    finding("code", CODE_MATCH, searched=[f"Upstream: {LOG_REF}"]), **kw)


def disagreeing(**kw):
    return incident(finding("logs", LOG_LINE), finding("docs", DOC_MATCH),
                    finding("code", CODE_OTHER, ref="src/ad/problempattern/CPULoad.java@4a70112:L10-L20"), **kw)


def score(response, calibration=UNMEASURED):
    return score_confidence(response, POLICY, calibration)


# ---- agreement between logs and code ---------------------------------------------------------

def test_logs_and_code_that_agree_score_high_and_explain_why():
    r = score(agreeing())
    assert r.components["agreement"] == 1.0 and r.band == "high"
    assert "tied its code to 1 log event" in r.reasons[0] and "inject:ads-unavailable" in r.reasons[0]


def test_logs_and_code_that_disagree_are_held_below_high_even_if_everything_else_looks_good():
    r = score(disagreeing(), Calibration({"logs": (Measurement("m", 20, 20),), "docs": (Measurement("m", 20, 20),),
                                         "code": (Measurement("m", 20, 20),)}))
    assert r.components["agreement"] == 0.1
    assert r.band != "high" and r.score <= POLICY.disagreement_cap
    assert any("can't be High" in x for x in r.reasons)


@pytest.mark.parametrize("code_text, linked, expected", [
    (CODE_MATCH, True, 1.0),                                        # explicit link + shared signal
    ("Unrelated helper method", True, 0.7),                         # explicit link only
    ('Throws withDescription("inject:ads-unavailable")', False, 0.5),  # one shared signal
    ('Throws withDescription("inject:ads-unavailable") via AdServiceImpl and adFailure', False, 0.5),
    ("Unrelated helper method", False, 0.1),                        # nothing links them
])
def test_agreement_levels(code_text, linked, expected):
    code = finding("code", code_text, searched=[f"Upstream: {LOG_REF}"] if linked else [])
    assert score(incident(finding("logs", LOG_LINE), None, code)).components["agreement"] == expected


def test_two_shared_signals_without_a_link_count_more_than_one():
    logs = finding("logs", "GetAds failed [inject:ads-unavailable] in AdServiceImpl")
    code = finding("code", 'AdServiceImpl throws withDescription("inject:ads-unavailable")')
    assert score(incident(logs, None, code)).components["agreement"] == 0.7


def test_words_from_the_question_do_not_count_as_agreement():
    logs = finding("logs", "GetAds UNAVAILABLE")
    code = finding("code", "GetAds returns UNAVAILABLE")
    r = score(incident(logs, None, code))
    assert r.components["agreement"] == 0.1          # both only repeat the user's own words


def test_agreement_is_zero_when_one_side_has_no_evidence():
    r = score(incident(finding("logs", LOG_LINE), None, finding("code", status="no_evidence")))
    assert r.components["agreement"] == 0.0
    assert "code returned no usable evidence" in r.reasons[0]


def test_agreement_does_not_apply_when_logs_and_code_were_not_both_asked():
    r = score(incident(None, finding("docs", DOC_MATCH), None))
    assert "agreement" not in r.components
    assert any("agreement: not applicable" in x for x in r.reasons)
    assert 0 < r.score <= 95


# ---- docs relevance ----------------------------------------------------------------------------

def test_relevant_docs_score_higher_than_unrelated_docs():
    good = score(incident(None, finding("docs", DOC_MATCH), None)).components["docs_relevance"]
    bad = score(incident(None, finding("docs", "Recommendation service cache walkthrough"), None))
    assert good > bad.components["docs_relevance"]


def test_docs_that_mention_what_logs_or_code_found_get_credit():
    plain = incident(finding("logs", LOG_LINE), finding("docs", "GetAds errors in the Ad Service"),
                     finding("code", CODE_MATCH))
    naming = incident(finding("logs", LOG_LINE), finding("docs", "adFailure: GetAds errors in the Ad Service"),
                      finding("code", CODE_MATCH))
    assert score(naming).components["docs_relevance"] > score(plain).components["docs_relevance"]
    assert "mentions adFailure" in " ".join(score(naming).reasons)


# ---- sub-agent context -------------------------------------------------------------------------

def test_failed_agents_lower_the_context_and_are_named_with_their_error():
    r = score(incident(finding("logs", status="error"), finding("docs", DOC_MATCH), finding("code", CODE_MATCH)))
    full = score(agreeing())
    assert r.components["agent_context"] < full.components["agent_context"]
    assert "logs: AGENT_TIMEOUT" in " ".join(r.reasons)


def test_an_agent_that_reports_a_coverage_gap_counts_less():
    gap = incident(None, finding("docs", DOC_MATCH, gaps=["Official website page limit reached"]), None)
    full = incident(None, finding("docs", DOC_MATCH), None)
    assert score(gap).components["agent_context"] < score(full).components["agent_context"]
    assert "partial coverage (Official website page limit reached)" in " ".join(score(gap).reasons)


def test_agents_own_relevance_rating_moves_the_context():
    weak = score(incident(None, finding("docs", DOC_MATCH, relevance=0.1), None))
    strong = score(incident(None, finding("docs", DOC_MATCH, relevance=0.9), None))
    assert weak.components["agent_context"] < strong.components["agent_context"]


# ---- measured accuracy (team numbers) ----------------------------------------------------------

def test_measured_accuracy_uses_the_team_numbers():
    measured = Calibration({"docs": (Measurement("D01-D06", 4, 6), Measurement("planted", 2, 2))})
    assert measured.reliability("docs") == pytest.approx(0.7)       # (6 + 1) / (8 + 2)
    assert measured.reliability("logs") == 0.5                      # not measured yet
    r = score(incident(None, finding("docs", DOC_MATCH), None), measured)
    assert r.components["measured_accuracy"] == 0.7
    assert "docs 70% (6/8 team test cases)" in " ".join(r.reasons)


def test_better_measured_agents_raise_the_score():
    poor = Calibration({a: (Measurement("m", 1, 10),) for a in ("logs", "docs", "code")})
    good = Calibration({a: (Measurement("m", 10, 10),) for a in ("logs", "docs", "code")})
    assert score(agreeing(), poor).score < score(agreeing(), good).score


def test_only_agents_the_answer_relies_on_count_for_measured_accuracy():
    cal = Calibration({"docs": (Measurement("m", 0, 10),), "logs": (Measurement("m", 10, 10),)})
    only_logs = agreeing(cited=[LOG_REF])
    assert score(only_logs, cal).components["measured_accuracy"] == pytest.approx(11 / 12, abs=1e-3)


def test_step_sources_count_as_reliance():
    r = agreeing(cited=[LOG_REF], steps=[Step(title="t", why="w",
                                             evidence_refs=["https://opentelemetry.io/docs/demo/feature-flags/"])])
    assert "docs" in score(r).reasons[3] and "logs" in score(r).reasons[3]


@pytest.mark.parametrize("bad", [
    {"agents": {"docs": [{"name": "x", "correct": 5, "total": 4}]}},
    {"agents": {"docs": [{"name": "x", "correct": 1, "total": 0}]}},
    {"agents": {"metrics": []}},
    {"agents": {"docs": [{"name": "x", "correct": 1, "total": 2, "surprise": 1}]}},
])
def test_invalid_calibration_files_are_rejected(tmp_path, bad):
    path = tmp_path / "cal.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        Calibration.from_file(path)


def test_shipped_calibration_file_is_valid_and_has_the_docs_results():
    cal = Calibration.from_file(DEFAULT_CALIBRATION_PATH)
    assert cal.reliability("docs") == pytest.approx(0.7)


# ---- grounding, penalties, caps ----------------------------------------------------------------

def test_grounding_order_validated_ai_beats_evidence_list_beats_failed_ai():
    g = {src: score(agreeing(synthesis=src)).components["grounding"] for src in ("llm", "evidence", "evidence_fallback")}
    assert g["llm"] > g["evidence"] > g["evidence_fallback"]
    assert score(agreeing(truncated=True)).components["grounding"] < g["llm"]


def test_planning_fallback_is_a_small_penalty_with_a_reason():
    assert score(agreeing(planning="keyword_fallback")).score < score(agreeing()).score
    assert any("keyword routing" in x for x in score(agreeing(planning="keyword_fallback")).reasons)


def test_no_evidence_scores_zero_and_says_why():
    r = score(incident(finding("logs", status="error"), finding("docs", status="no_evidence"),
                       finding("code", status="no_access")))
    assert r.score == 0 and r.band == "low"
    assert "No supporting evidence" in r.reasons[0] and "logs (AGENT_TIMEOUT)" in r.reasons[1]


def test_never_above_the_cap():
    perfect = Calibration({a: (Measurement("m", 1000, 1000),) for a in ("logs", "docs", "code")})
    r = score_confidence(agreeing(), ConfidencePolicy(max_score=90), perfect)
    assert r.score == 90 and any("Capped at 90%" in x for x in r.reasons)


def test_disagreement_cap_holds_even_if_the_team_reweights_the_score():
    light = ConfidencePolicy(weights={"agreement": 0.05, "docs_relevance": 0.05, "agent_context": 0.3,
                                      "measured_accuracy": 0.3, "grounding": 0.3})
    perfect = Calibration({a: (Measurement("m", 1000, 1000),) for a in ("logs", "docs", "code")})
    r = score_confidence(disagreeing(), light, perfect)
    assert r.score == light.disagreement_cap and r.band == "medium"


def test_apply_confidence_fills_every_response_field():
    r = apply_confidence(agreeing(), POLICY, UNMEASURED)
    assert isinstance(r.confidence, int) and r.confidence_band and r.confidence_reasons
    assert set(r.confidence_components) == {"agreement", "docs_relevance", "agent_context",
                                            "measured_accuracy", "grounding"}


# ---- signal extraction -------------------------------------------------------------------------

def test_signals_match_across_spellings_and_ignore_plain_words():
    assert "adfailure" in signals("AD_FAILURE") and "adfailure" in signals("flag adFailure is on")
    assert "adservice" in signals("src/ad/AdService.java")
    assert signals("The service is failing with errors and warnings") == set()
    assert "unavailable" in signals("status UNAVAILABLE") and not signals("WARNING ERROR")


def test_key_terms_are_stemmed_content_words():
    assert {"getad", "fail", "unavailable", "error", "ad", "service"} <= key_terms(QUESTION)
    assert "why" not in key_terms(QUESTION)


# ---- policy ------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    {"weights": {"agreement": 1.0}},
    {"weights": {"agreement": 0.5, "docs_relevance": 0.5, "agent_context": 0.5, "measured_accuracy": 0,
                 "grounding": 0}},
    {"weights": {"agreement": -0.1, "docs_relevance": 0.3, "agent_context": 0.3, "measured_accuracy": 0.3,
                 "grounding": 0.2}},
    {"high_threshold": 40, "medium_threshold": 50},
    {"disagreement_cap": 99},
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
    assert ConfidencePolicy.from_file(DEFAULT_POLICY_PATH) == ConfidencePolicy()


def test_every_investigation_answer_gets_a_confidence(tmp_path):
    (tmp_path / "runbook.md").write_text("# Checkout\nCheckout timeout: raise the upstream timeout.\n")
    config = Settings(mode="local", routing_mode="keyword", answer_mode="evidence",
                      docs_root=tmp_path, code_root=tmp_path, logs_root=tmp_path)
    for question in ("How do I fix the checkout timeout?", "Why is checkout timing out?"):
        r = asyncio.run(orchestrator.investigate(question, settings=config))
        assert r.confidence is not None and r.confidence_band and r.confidence_reasons
