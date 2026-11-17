"""Confidence score v2 (CONFIDENCE_DEFINITION.md): built only from what the sub-agents returned.

Tests confidence.py: the five parts (logs-code agreement, docs relevance, sub-agent context,
measured accuracy, grounding), how they combine, the caps, and the policy/calibration files.

How these tests work:
  * finding(...) builds one agent's result (ok / error / no evidence, its relevance rating,
    coverage gaps, and the code agent's "Upstream: <log ref>" link).
  * incident(...) wraps findings into a CopilotResponse the way investigate() builds one.
  * agreeing() = logs, docs and code all point to inject:ads-unavailable, and the code agent
    links to the log event. disagreeing() = the code points to an unrelated CPULoad file.
  * score(...) uses the default weights (POLICY) and UNMEASURED calibration (every agent 50%),
    so editing the JSON files can't change these results; tests pass their own Calibration
    when they need measured numbers.

Run: python -m pytest tests/test_confidence.py -v     (from the orchestrator folder)
"""
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
    """One agent's result. Evidence is added only when status is 'ok'; 'error' adds an AGENT_TIMEOUT."""
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
    """Logs, docs and code all point to inject:ads-unavailable, and the code agent links to the log."""
    return incident(finding("logs", LOG_LINE), finding("docs", DOC_MATCH),
                    finding("code", CODE_MATCH, searched=[f"Upstream: {LOG_REF}"]), **kw)


def disagreeing(**kw):
    """Same logs and docs, but the code agent points to an unrelated CPULoad file and has no link."""
    return incident(finding("logs", LOG_LINE), finding("docs", DOC_MATCH),
                    finding("code", CODE_OTHER, ref="src/ad/problempattern/CPULoad.java@4a70112:L10-L20"), **kw)


def score(response, calibration=UNMEASURED):
    """Score with the default weights and the given calibration (default: every agent 50%)."""
    return score_confidence(response, POLICY, calibration)


# ---- agreement between logs and code ---------------------------------------------------------

def test_logs_and_code_that_agree_score_high_and_explain_why():
    """Agreeing logs and code: agreement is 1.0, the band is High, and the reason names the shared signal.

    """
    r = score(agreeing())
    assert r.components["agreement"] == 1.0 and r.band == "high"
    assert "tied its code to 1 log event" in r.reasons[0] and "inject:ads-unavailable" in r.reasons[0]


def test_logs_and_code_that_disagree_are_held_below_high_even_if_everything_else_looks_good():
    """Code points somewhere else: agreement is 0.1 and, even with perfect measured accuracy, the score is at
    most 74 (not High) with a 'can't be High' reason.
    """
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
    """Each agreement level: link + shared signal 1.0, link only 0.7, one shared signal 0.5 (extra names the
    logs don't mention don't add), nothing connecting them 0.1.
    """
    code = finding("code", code_text, searched=[f"Upstream: {LOG_REF}"] if linked else [])
    assert score(incident(finding("logs", LOG_LINE), None, code)).components["agreement"] == expected


def test_two_shared_signals_without_a_link_count_more_than_one():
    """Two shared signals and no link give 0.7, more than one signal (0.5)."""
    logs = finding("logs", "GetAds failed [inject:ads-unavailable] in AdServiceImpl")
    code = finding("code", 'AdServiceImpl throws withDescription("inject:ads-unavailable")')
    assert score(incident(logs, None, code)).components["agreement"] == 0.7


def test_words_from_the_question_do_not_count_as_agreement():
    """Both agents only repeat words from the question (GetAds, UNAVAILABLE), so agreement stays 0.1."""
    logs = finding("logs", "GetAds UNAVAILABLE")
    code = finding("code", "GetAds returns UNAVAILABLE")
    r = score(incident(logs, None, code))
    assert r.components["agreement"] == 0.1          # both only repeat the user's own words


def test_agreement_is_zero_when_one_side_has_no_evidence():
    """Code returned no evidence: agreement is 0 and the reason names the code agent."""
    r = score(incident(finding("logs", LOG_LINE), None, finding("code", status="no_evidence")))
    assert r.components["agreement"] == 0.0
    assert "code returned no usable evidence" in r.reasons[0]


def test_agreement_does_not_apply_when_logs_and_code_were_not_both_asked():
    """Docs-only question: there is no agreement part (it is left out and re-weighted), and the score is still
    between 0 and 95.
    """
    r = score(incident(None, finding("docs", DOC_MATCH), None))
    assert "agreement" not in r.components
    assert any("agreement: not applicable" in x for x in r.reasons)
    assert 0 < r.score <= 95


# ---- docs relevance ----------------------------------------------------------------------------

def test_relevant_docs_score_higher_than_unrelated_docs():
    """A docs excerpt about adFailure/GetAds scores higher docs relevance than an unrelated excerpt."""
    good = score(incident(None, finding("docs", DOC_MATCH), None)).components["docs_relevance"]
    bad = score(incident(None, finding("docs", "Recommendation service cache walkthrough"), None))
    assert good > bad.components["docs_relevance"]


def test_docs_that_mention_what_logs_or_code_found_get_credit():
    """Docs that mention a signal the logs/code found (adFailure) score higher, and the reason says so."""
    plain = incident(finding("logs", LOG_LINE), finding("docs", "GetAds errors in the Ad Service"),
                     finding("code", CODE_MATCH))
    naming = incident(finding("logs", LOG_LINE), finding("docs", "adFailure: GetAds errors in the Ad Service"),
                      finding("code", CODE_MATCH))
    assert score(naming).components["docs_relevance"] > score(plain).components["docs_relevance"]
    assert "mentions adFailure" in " ".join(score(naming).reasons)


# ---- sub-agent context -------------------------------------------------------------------------

def test_failed_agents_lower_the_context_and_are_named_with_their_error():
    """A timed-out logs agent lowers sub-agent context, and the reason names its error code."""
    r = score(incident(finding("logs", status="error"), finding("docs", DOC_MATCH), finding("code", CODE_MATCH)))
    full = score(agreeing())
    assert r.components["agent_context"] < full.components["agent_context"]
    assert "logs: AGENT_TIMEOUT" in " ".join(r.reasons)


def test_an_agent_that_reports_a_coverage_gap_counts_less():
    """An agent that reports part of its source was unreachable counts less (x0.8), and the gap is quoted.

    """
    gap = incident(None, finding("docs", DOC_MATCH, gaps=["Official website page limit reached"]), None)
    full = incident(None, finding("docs", DOC_MATCH), None)
    assert score(gap).components["agent_context"] < score(full).components["agent_context"]
    assert "partial coverage (Official website page limit reached)" in " ".join(score(gap).reasons)


def test_agents_own_relevance_rating_moves_the_context():
    """An agent's own relevance rating moves sub-agent context: 0.1 scores lower than 0.9."""
    weak = score(incident(None, finding("docs", DOC_MATCH, relevance=0.1), None))
    strong = score(incident(None, finding("docs", DOC_MATCH, relevance=0.9), None))
    assert weak.components["agent_context"] < strong.components["agent_context"]


# ---- measured accuracy (team numbers) ----------------------------------------------------------

def test_measured_accuracy_uses_the_team_numbers():
    """Docs measured 4/6 + 2/2 gives (6+1)/(8+2) = 70%; an unmeasured agent is 50%; the reason shows 6/8."""
    measured = Calibration({"docs": (Measurement("D01-D06", 4, 6), Measurement("planted", 2, 2))})
    assert measured.reliability("docs") == pytest.approx(0.7)       # (6 + 1) / (8 + 2)
    assert measured.reliability("logs") == 0.5                      # not measured yet
    r = score(incident(None, finding("docs", DOC_MATCH), None), measured)
    assert r.components["measured_accuracy"] == 0.7
    assert "docs 70% (6/8 team test cases)" in " ".join(r.reasons)


def test_better_measured_agents_raise_the_score():
    """Agents measured 10/10 give a higher overall score than agents measured 1/10."""
    poor = Calibration({a: (Measurement("m", 1, 10),) for a in ("logs", "docs", "code")})
    good = Calibration({a: (Measurement("m", 10, 10),) for a in ("logs", "docs", "code")})
    assert score(agreeing(), poor).score < score(agreeing(), good).score


def test_only_agents_the_answer_relies_on_count_for_measured_accuracy():
    """Only cited agents count: the answer cites only the log, so docs' poor 0/10 record is ignored and
    measured accuracy is logs' (10+1)/(10+2).
    """
    cal = Calibration({"docs": (Measurement("m", 0, 10),), "logs": (Measurement("m", 10, 10),)})
    only_logs = agreeing(cited=[LOG_REF])
    assert score(only_logs, cal).components["measured_accuracy"] == pytest.approx(11 / 12, abs=1e-3)


def test_step_sources_count_as_reliance():
    """Evidence cited by a step counts as relied on: a step citing the docs URL brings docs into the reason.

    """
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
    """Bad calibration files are refused: correct > total, total 0, an unknown agent, an unknown field."""
    path = tmp_path / "cal.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        Calibration.from_file(path)


def test_shipped_calibration_file_is_valid_and_has_the_docs_results():
    """The real confidence_calibration.json loads and gives docs 70%."""
    cal = Calibration.from_file(DEFAULT_CALIBRATION_PATH)
    assert cal.reliability("docs") == pytest.approx(0.7)


# ---- grounding, penalties, caps ----------------------------------------------------------------

def test_grounding_order_validated_ai_beats_evidence_list_beats_failed_ai():
    """Grounding: validated AI answer > evidence list > failed AI answer, and truncated context lowers it.

    """
    g = {src: score(agreeing(synthesis=src)).components["grounding"] for src in ("llm", "evidence", "evidence_fallback")}
    assert g["llm"] > g["evidence"] > g["evidence_fallback"]
    assert score(agreeing(truncated=True)).components["grounding"] < g["llm"]


def test_planning_fallback_is_a_small_penalty_with_a_reason():
    """When AI planning falls back to keyword routing, the score drops (x0.95) and a reason says why."""
    assert score(agreeing(planning="keyword_fallback")).score < score(agreeing()).score
    assert any("keyword routing" in x for x in score(agreeing(planning="keyword_fallback")).reasons)


def test_no_evidence_scores_zero_and_says_why():
    """No agent returned evidence: the score is 0, Low, and the reasons list each failed agent."""
    r = score(incident(finding("logs", status="error"), finding("docs", status="no_evidence"),
                       finding("code", status="no_access")))
    assert r.score == 0 and r.band == "low"
    assert "No supporting evidence" in r.reasons[0] and "logs (AGENT_TIMEOUT)" in r.reasons[1]


def test_never_above_the_cap():
    """With a max of 90 and perfect data, the score is exactly 90 with a 'Capped at 90%' reason."""
    perfect = Calibration({a: (Measurement("m", 1000, 1000),) for a in ("logs", "docs", "code")})
    r = score_confidence(agreeing(), ConfidencePolicy(max_score=90), perfect)
    assert r.score == 90 and any("Capped at 90%" in x for x in r.reasons)


def test_disagreement_cap_holds_even_if_the_team_reweights_the_score():
    """Even if agreement's weight is cut to 5%, a disagreeing answer with perfect everything else is capped at
    exactly 74 (Medium). Fails if the cap is removed.
    """
    light = ConfidencePolicy(weights={"agreement": 0.05, "docs_relevance": 0.05, "agent_context": 0.3,
                                      "measured_accuracy": 0.3, "grounding": 0.3})
    perfect = Calibration({a: (Measurement("m", 1000, 1000),) for a in ("logs", "docs", "code")})
    r = score_confidence(disagreeing(), light, perfect)
    assert r.score == light.disagreement_cap and r.band == "medium"


def test_apply_confidence_fills_every_response_field():
    """apply_confidence sets the score, band, reasons and exactly the five part values on the response."""
    r = apply_confidence(agreeing(), POLICY, UNMEASURED)
    assert isinstance(r.confidence, int) and r.confidence_band and r.confidence_reasons
    assert set(r.confidence_components) == {"agreement", "docs_relevance", "agent_context",
                                            "measured_accuracy", "grounding"}


# ---- signal extraction -------------------------------------------------------------------------

def test_signals_match_across_spellings_and_ignore_plain_words():
    """Signal matching: AD_FAILURE matches adFailure, AdService.java gives 'adservice', plain English gives
    nothing, UNAVAILABLE counts but WARNING/ERROR don't.
    """
    assert "adfailure" in signals("AD_FAILURE") and "adfailure" in signals("flag adFailure is on")
    assert "adservice" in signals("src/ad/AdService.java")
    assert signals("The service is failing with errors and warnings") == set()
    assert "unavailable" in signals("status UNAVAILABLE") and not signals("WARNING ERROR")


def test_key_terms_are_stemmed_content_words():
    """Key terms of the question are content words, stemmed ('failing' -> 'fail'); 'why' is dropped."""
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
    """Bad policies are refused: missing weights, weights not adding to 1, a negative weight, thresholds in the
    wrong order, a cap above the max.
    """
    with pytest.raises(ValueError):
        ConfidencePolicy(**bad)


def test_policy_file_is_validated(tmp_path):
    """A policy file with one change loads; a file with an unknown key is refused."""
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"high_threshold": 80}))
    assert ConfidencePolicy.from_file(path).high_threshold == 80
    path.write_text(json.dumps({"surprise": 1}))
    with pytest.raises(ValueError, match="unknown"):
        ConfidencePolicy.from_file(path)


def test_shipped_default_policy_file_is_valid():
    """The real confidence_policy.json matches the defaults in code."""
    assert ConfidencePolicy.from_file(DEFAULT_POLICY_PATH) == ConfidencePolicy()


def test_every_investigation_answer_gets_a_confidence(tmp_path):
    """End to end with the real investigate() on temporary files (no mocks): a how-to and an incident question
    both get a score, a band and reasons.
    """
    (tmp_path / "runbook.md").write_text("# Checkout\nCheckout timeout: raise the upstream timeout.\n")
    config = Settings(mode="local", routing_mode="keyword", answer_mode="evidence",
                      docs_root=tmp_path, code_root=tmp_path, logs_root=tmp_path)
    for question in ("How do I fix the checkout timeout?", "Why is checkout timing out?"):
        r = asyncio.run(orchestrator.investigate(question, settings=config))
        assert r.confidence is not None and r.confidence_band and r.confidence_reasons
