"""Value-change recommendations retain exact source support and explicit unknowns."""
import asyncio
import json

import httpx
import pytest

from answer_synthesis import generate_answer
from schemas import CopilotResponse, Evidence, Step
from settings import Settings


CHANGE = dict(
    target="adFailure", current_value="true", recommended_value="false",
    current_value_excerpt="adFailure = true",
    evidence_excerpt="Set adFailure to false to disable injected failures.",
    evidence_ids=["E1", "E2"], location_evidence_id="E1",
)


def recommend(change=None, *, step_ids=None, code="adFailure = true",
              doc="Set adFailure to false to disable injected failures."):
    """Exercise JSON parsing, citation validation, and solution validation together."""
    response = CopilotResponse(
        kind="diagnosis", question="How should I fix the failure flag?", answer="Evidence",
        evidence=[
            Evidence(agent="code", source_type="code", ref="flags/ad.conf:L10", summary="Source configuration", snippet=code),
            Evidence(agent="docs", source_type="doc", ref="runbook.md:L20", summary="Documented remedy", snippet=doc),
        ],
    )
    proposal = {**CHANGE, **(change or {})}
    body = dict(answer="The configuration is shown [E1]. Follow the documented remedy [E2].",
                evidence_ids=["E1", "E2"],
                steps=[dict(title="Disable the injected failure flag", why="The runbook describes the remedy.",
                            kind="fix", evidence_ids=["E1", "E2"] if step_ids is None else step_ids,
                            changes=[proposal])])
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(body)}}]}))
    settings = Settings(llm_endpoint="https://example.invalid/openai/v1", llm_model="test", llm_api_key="test")
    return asyncio.run(generate_answer(response, settings, transport=transport))


def test_explicit_value_change_roundtrips_with_real_location_and_sources():
    """A documented replacement, source assignment and code location survive serialization."""
    response = recommend()
    assert response.synthesis.source == "llm"
    [change] = response.steps[0].changes
    assert change.target == "adFailure"
    assert change.current_value == "true" and change.recommended_value == "false"
    assert change.location == "flags/ad.conf:L10"
    assert change.evidence_refs == ["flags/ad.conf:L10", "runbook.md:L20"]
    assert change.evidence_excerpt == CHANGE["evidence_excerpt"]
    assert CopilotResponse.model_validate_json(response.model_dump_json()).steps[0].changes == [change]
    assert response.synthesis.warning is None


@pytest.mark.parametrize("patch", [
    {"target": "madeUpFlag"},
    {"recommended_value": "False"},
    {"recommended_value": "999"},
    {"evidence_excerpt": "Set adFailure to false; this wording is invented."},
    {"evidence_excerpt": "adFailure = true Set adFailure to false to disable injected failures."},
    {"evidence_ids": ["E99"]},
    {"evidence_ids": []},
])
def test_unsupported_change_is_removed_without_losing_the_answer_or_action(patch):
    """Reject invented targets, values, quotes, stitched sources and invalid citations individually."""
    response = recommend(patch)
    assert response.synthesis.source == "llm"
    assert len(response.steps) == 1 and response.steps[0].changes == []
    assert "proposed value change(s) were removed" in response.synthesis.warning


def test_change_cannot_borrow_sources_not_cited_by_its_step():
    """A valid runbook is insufficient when its parent action cites only code."""
    response = recommend(step_ids=["E1"])
    assert response.steps[0].changes == []


@pytest.mark.parametrize("patch", [
    {"current_value": None, "current_value_excerpt": None},
    {"current_value": "enabled", "current_value_excerpt": "adFailure = true"},
    {"current_value": "true", "current_value_excerpt": None},
    {"current_value": "true", "current_value_excerpt": "adFailure = true (invented)"},
])
def test_unconfirmed_source_value_remains_unknown_without_losing_supported_replacement(patch):
    """A missing or unsupported old value must not become an asserted current runtime value."""
    [change] = recommend(patch).steps[0].changes
    assert change.current_value is None and change.recommended_value == "false"


@pytest.mark.parametrize("location", ["E99", "E2", "invented/config.yaml:L123"])
def test_edit_location_must_resolve_to_cited_code(location):
    """A docs citation or fabricated path cannot masquerade as the file to edit."""
    [change] = recommend({"location_evidence_id": location}).steps[0].changes
    assert change.location is None and change.recommended_value == "false"


@pytest.mark.parametrize(("target", "value", "excerpt"), [
    ("port", "false", "Set transport to false."),
    ("timeoutMs", "10", "Set timeoutMs to 100."),
    ("logFormat", '"A B"', 'Set logFormat to "A  B".'),
])
def test_literals_cannot_be_borrowed_from_other_names_numbers_or_rewritten_strings(target, value, excerpt):
    """Substring and whitespace normalization must not manufacture a supported replacement."""
    response = recommend(dict(target=target, recommended_value=value, evidence_excerpt=excerpt), doc=excerpt)
    assert response.steps[0].changes == []


def test_older_steps_default_to_no_value_changes():
    """Stored responses and older clients remain valid without the new optional field."""
    assert Step(title="Review sources", why="No exact edit established").changes == []
