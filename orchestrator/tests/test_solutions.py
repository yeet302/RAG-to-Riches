"""Suggested solutions (likely cause + steps) must be grounded in the collected evidence.

Tests answer_synthesis.validate_solutions, which runs inside generate_answer after the AI replies.

How these tests work:
  * diagnosis() builds a response with three pieces of evidence. The orchestrator numbers them
    E1 = LOG (log line), E2 = DOC (runbook that contains "flagd-cli set adFailure off"),
    E3 = CODE (the code line).
  * model_says(...) is a fake AI: httpx.MockTransport returns the JSON the test chooses instead
    of calling Azure, so the tests are fast, offline and give the same result every run.
  * run(...) calls the real generate_answer, so John's citation check and validate_solutions
    both run exactly as they do in the app.

Run: python -m pytest tests/test_solutions.py -v      (from the orchestrator folder)
"""
from __future__ import annotations

import asyncio
import json

import httpx

from answer_synthesis import SYSTEM_PROMPT, generate_answer
from schemas import AgentFinding, CopilotResponse, Evidence, TraceEvent
from settings import Settings

LOG = Evidence(source_type="log", agent="logs", ref="ad-service.log:L42",
               summary="GetAds failed with UNAVAILABLE about 1 in 10 requests since 14:02.",
               snippet="WARN GetAds Failed with status UNAVAILABLE")
DOC = Evidence(source_type="doc", agent="docs", ref="runbooks/ad.md:L3-L5",
               summary="Runbook: if adFailure is on, turn it off with the flag tool.",
               snippet="Run: flagd-cli set adFailure off\nErrors should stop within a minute.")
CODE = Evidence(source_type="code", agent="code", ref="AdService.java:L237-L240",
                summary="getAds throws UNAVAILABLE when the adFailure flag is enabled.",
                snippet="if (ffClient.getBooleanValue(AD_FAILURE, false, ctx) && random.nextInt(10) == 0)")


def configured():
    """Settings that make generate_answer use the (fake) AI instead of the evidence list."""
    return Settings(llm_endpoint="https://example.invalid/openai/v1", llm_model="m", llm_api_key="k")


def diagnosis(kind="diagnosis"):
    """An incident response with E1 (log), E2 (runbook), E3 (code); kind='answer' makes it a how-to."""
    findings = [AgentFinding(agent=e.agent, summary=e.summary, evidence=[e], confidence=0.7)
                for e in (LOG, DOC, CODE)]
    return CopilotResponse(kind=kind, question="Why are ads failing?", answer="evidence list",
                           evidence=[LOG, DOC, CODE], findings=findings, agents_used=["logs", "docs", "code"],
                           trace=[TraceEvent(stage=3, agent="orchestrator", action="combined")],
                           request_id="r1")


def model_says(**extra):
    """Fake AI reply: a valid answer citing E1-E3, plus any extra fields the test adds."""
    body = {"answer": "Ads fail because the adFailure flag is on [E1] [E3]. The runbook says to turn it off [E2].",
            "evidence_ids": ["E1", "E2", "E3"]}
    body.update(extra)
    content = json.dumps(body)
    return httpx.MockTransport(lambda request: httpx.Response(
        200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]}))


def run(response, transport):
    """Run the real generate_answer on `response`, with the fake AI behind `transport`."""
    return asyncio.run(generate_answer(response, configured(), transport=transport))


GOOD_STEP = {"title": "Turn off the adFailure flag", "why": "The runbook prescribes this for these errors.",
             "command": "flagd-cli set adFailure off", "expect": "Errors should stop within a minute.",
             "risk": "low", "kind": "mitigation", "evidence_ids": ["E2", "E3"]}


def test_grounded_cause_and_steps_are_returned_with_their_sources():
    """Normal case: a cited cause, one grounded step and an alternative are all kept. The command is kept
    because it appears word for word in E2, the step shows E2/E3's real refs, and there is no warning.
    """
    r = run(diagnosis(), model_says(
        likely_cause="The adFailure feature flag is enabled, so getAds rejects about 1 in 10 requests.",
        likely_cause_evidence_ids=["E1", "E3"], steps=[GOOD_STEP],
        not_ruled_out="A network problem between frontend and ad service.", not_ruled_out_evidence_ids=[]))
    assert r.synthesis.source == "llm"
    assert r.likely_cause.startswith("The adFailure feature flag is enabled")
    assert r.not_ruled_out == "A network problem between frontend and ad service."
    [step] = r.steps
    assert step.title == "Turn off the adFailure flag"
    assert step.command == "flagd-cli set adFailure off"          # appears verbatim in the runbook
    assert step.evidence_refs == ["runbooks/ad.md:L3-L5", "AdService.java:L237-L240"]
    assert r.synthesis.warning is None


def test_step_without_valid_evidence_is_removed_but_answer_is_kept():
    """A step citing a fake ID (E9) and a step citing nothing are removed; the grounded step stays, the AI
    answer is still used, and the warning says 2 steps were removed.
    """
    bad = {**GOOD_STEP, "title": "Restart everything", "evidence_ids": ["E9"]}
    none = {**GOOD_STEP, "title": "Scale up", "evidence_ids": []}
    r = run(diagnosis(), model_says(steps=[bad, none, GOOD_STEP]))
    assert [s.title for s in r.steps] == ["Turn off the adFailure flag"]
    assert r.synthesis.source == "llm"
    assert "2 suggested step(s) were removed" in r.synthesis.warning


def test_invented_command_is_stripped_from_an_otherwise_grounded_step():
    """A command that appears in no evidence (kubectl delete ...) is removed, but the step itself is kept and
    the warning says a command was removed.
    """
    step = {**GOOD_STEP, "command": "kubectl delete pod ad-0 --force"}
    r = run(diagnosis(), model_says(steps=[step]))
    assert r.steps[0].command is None and r.steps[0].title == GOOD_STEP["title"]
    assert "command(s) were removed" in r.synthesis.warning


def test_command_must_come_from_the_steps_own_cited_evidence():
    """Borrowed command: the command is real (it is in the runbook, E2) but this step only cites the log line
    (E1), so the command must be removed. Fails if commands are checked against all the evidence.
    """
    # The command exists in the runbook (E2) but this step only cites the log line (E1).
    step = {**GOOD_STEP, "evidence_ids": ["E1"]}
    assert run(diagnosis(), model_says(steps=[step])).steps[0].command is None


def test_uncited_likely_cause_is_dropped():
    """A likely cause with no evidence IDs is removed, and the warning says so."""
    r = run(diagnosis(), model_says(likely_cause="Cosmic rays.", likely_cause_evidence_ids=[]))
    assert r.likely_cause is None
    assert "likely cause was removed" in r.synthesis.warning


def test_cause_and_alternatives_only_for_incident_questions():
    """For a non-incident (how-to) answer, the likely cause and 'not ruled out' are dropped, but a grounded
    step is still kept.
    """
    r = run(diagnosis(kind="answer"), model_says(
        likely_cause="Flag is on.", likely_cause_evidence_ids=["E1"], not_ruled_out="Network.", steps=[GOOD_STEP]))
    assert r.likely_cause is None and r.not_ruled_out is None
    assert len(r.steps) == 1                      # how-to answers can still have grounded steps


def test_at_most_five_steps_are_kept():
    """The AI sends 8 grounded steps; only the first 5 are kept (MAX_STEPS)."""
    steps = [{**GOOD_STEP, "title": f"Step {i}"} for i in range(8)]
    assert len(run(diagnosis(), model_says(steps=steps)).steps) == 5


def test_citation_markers_are_removed_from_solution_text():
    """Stray [E1]-style markers are removed from the cause and the step text (sources are shown separately).

    """
    r = run(diagnosis(), model_says(likely_cause="Flag is on [E1].", likely_cause_evidence_ids=["E1"],
                                    steps=[{**GOOD_STEP, "why": "Runbook says so [E2]."}]))
    assert r.likely_cause == "Flag is on." and r.steps[0].why == "Runbook says so."


def test_model_may_not_add_a_confidence_number():
    """If the AI adds its own 'confidence' key, the whole AI answer is rejected (extra keys are forbidden) and
    the evidence list is shown instead, with no steps or cause.
    """
    # Unknown keys are still rejected outright, so the model can't sneak in its own score.
    r = run(diagnosis(), model_says(confidence=99))
    assert r.synthesis.source == "evidence_fallback" and r.steps == [] and r.likely_cause is None


def test_prompt_requires_grounded_solutions_and_forbids_confidence():
    """The system prompt still contains the grounding rules and forbids a confidence number."""
    assert "Every step must be" in SYSTEM_PROMPT and "evidence_ids" in SYSTEM_PROMPT
    assert "Never output a confidence number" in SYSTEM_PROMPT
    assert "ONLY a command copied exactly from the evidence" in SYSTEM_PROMPT


def test_evidence_mode_never_invents_solutions():
    """With answer_mode='evidence' (no AI), no steps or cause are produced."""
    r = asyncio.run(generate_answer(diagnosis(), Settings(), "evidence"))
    assert r.steps == [] and r.likely_cause is None and r.synthesis.source == "evidence"
