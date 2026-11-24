"""Planning fallback uses the question's own keywords (sprint story: "tool uses backup keywords").

Cause found: the orchestrator gave Azure planning LLM_TIMEOUT_SECONDS=10 s, including the Entra
token fetch. Planning with the team's deployment did not finish in time, so every question fell
back to keyword routing (the routing step lasted exactly 10 s) and each agent got the generic
TASKS text. These tests check the fix on the planted-bug questions B-01..B-05 from the test sheet
(orchestrator/incidents_catalog.json):
  - the keyword fallback's tasks and reason name the terms the user typed (GetAds, UNAVAILABLE, ...);
  - the warning says which failure happened (timeout with its limit, HTTP status, or error type);
  - the planning deadline defaults to 30 s, and the Entra token is reused between model calls.
Run from orchestrator/:  python -m pytest tests/test_planning_fallback.py -q
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

import azure_client
from failures import annotate_errors
from query_analysis import analyze_question
from router import question_terms
from schemas import CopilotResponse
from settings import Settings

CATALOG = json.loads((Path(__file__).resolve().parents[1] / "incidents_catalog.json").read_text(encoding="utf-8"))
SHEET = {incident["id"]: incident["question"] for incident in CATALOG["incidents"]}
# The wording used in the sprint demo run, which showed the PLANNING_FALLBACK error.
DEMO_B01 = "Why do some GetAds requests fail with UNAVAILABLE errors in the Ad Service?"


def configured(**kwargs):
    values = {"routing_mode": "llm", "answer_mode": "evidence", "llm_endpoint": "https://example.invalid/openai/v1",
              "llm_model": "test-deployment", "llm_api_key": "test-secret-key"}
    return Settings(**{**values, **kwargs})


def slow_transport(seconds=1.0):
    async def respond(request):
        await asyncio.sleep(seconds)
        return httpx.Response(200, json={"choices": []})
    return httpx.MockTransport(respond)


@pytest.mark.parametrize("question,expected", [
    (DEMO_B01, ["GetAds", "UNAVAILABLE", "Ad Service", "requests", "fail", "errors"]),
    (SHEET["B-01"], ["GetAds", "UNAVAILABLE", "Ad Service", "requests", "failing", "errors"]),
    (SHEET["B-02"], ["Ad Service", "targeted", "requests", "failing", "binoculars"]),
    (SHEET["B-03"], ["CPU", "Ad Service", "high", "usage"]),
    (SHEET["B-04"], ["AdResponse", "Ad Service", "returning", "empty", "advertisements"]),
    (SHEET["B-05"], ["GetAds", "Ad Service", "long", "pauses", "calls"]),
])
def test_terms_are_the_words_the_user_typed(question, expected):
    # Identifiers first, then the service, then content words; filler words (why, some, the) are dropped.
    assert question_terms(question) == expected


@pytest.mark.parametrize("question", [DEMO_B01, *SHEET.values()])
def test_timeout_fallback_gives_each_agent_the_questions_terms(question):
    result = asyncio.run(analyze_question(question, configured(llm_timeout_seconds=0.05),
                                          transport=slow_transport()))
    assert result.source == "keyword_fallback" and result.intent == "incident"
    assert result.stage1 == ["logs", "docs"] and result.stage2 == ["code"]
    assert "did not finish within LLM_TIMEOUT_SECONDS=0.05 s" in result.warning
    terms = question_terms(question)
    for assignment in result.assignments:
        assert assignment.question == question                 # the user's words, unchanged
        assert "in the Ad Service" in assignment.task          # scoped to the named service
        named = [t for t in terms if t != "Ad Service" and t in assignment.task]
        assert named == [t for t in terms if t != "Ad Service"]  # every other term is named
    assert "Search terms from the question: " + ", ".join(terms) in result.reason


def test_b01_tasks_are_specific_not_generic():
    from agents import TASKS
    result = asyncio.run(analyze_question(DEMO_B01, configured(llm_timeout_seconds=0.05),
                                          transport=slow_transport()))
    tasks = {a.agent: a.task for a in result.assignments}
    assert tasks["logs"].startswith("Search logs and traces in the Ad Service for GetAds, UNAVAILABLE, requests,")
    assert tasks["docs"].startswith("Search documentation and runbooks for GetAds, UNAVAILABLE,")
    assert tasks["code"].startswith("Using the earlier evidence, locate the code paths and configuration in the Ad Service")
    assert not set(tasks.values()) & set(TASKS.values())


def test_question_without_search_terms_keeps_the_generic_task():
    from agents import TASKS
    result = asyncio.run(analyze_question("Why?", Settings(routing_mode="keyword")))
    assert all(a.task == TASKS[a.agent] for a in result.assignments)


@pytest.mark.parametrize("status", [401, 429, 500])
def test_warning_names_the_http_status_without_the_body(status):
    result = asyncio.run(analyze_question(DEMO_B01, configured(), transport=httpx.MockTransport(
        lambda request: httpx.Response(status, text="private body test-secret-key"))))
    assert result.warning == (f"Azure planning returned HTTP {status}, so keyword routing was used with the "
                              "question's own terms.")
    assert "test-secret-key" not in result.model_dump_json()


def test_warning_names_the_error_type_for_invalid_plans():
    body = {"choices": [{"finish_reason": "stop", "message": {"content": "not JSON"}}]}
    result = asyncio.run(analyze_question(DEMO_B01, configured(), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=body))))
    assert result.warning.startswith("Azure planning failed (ValidationError)")


def test_planning_deadline_defaults_to_30_seconds(monkeypatch):
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
    assert Settings().llm_timeout_seconds == 30
    assert Settings.from_env().llm_timeout_seconds == 30
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "45")
    assert Settings.from_env().llm_timeout_seconds == 45


def test_planning_fallback_error_explains_the_timeout_and_the_fix():
    analysis = asyncio.run(analyze_question(DEMO_B01, configured(llm_timeout_seconds=0.05),
                                            transport=slow_transport()))
    response = annotate_errors(CopilotResponse(kind="diagnosis", question=DEMO_B01, answer="x", analysis=analysis))
    error = next(e for e in response.errors if e.code == "PLANNING_FALLBACK")
    assert error.message == analysis.warning
    assert "Raise LLM_TIMEOUT_SECONDS" in error.resolution


@pytest.fixture
def entra(monkeypatch):
    azure_client.clear_token_cache()
    credential = MagicMock()
    monkeypatch.setattr("azure.identity.DefaultAzureCredential", lambda: credential)
    yield credential.__enter__.return_value.get_token
    azure_client.clear_token_cache()


def test_entra_token_is_reused_until_close_to_expiry(entra):
    entra.return_value = MagicMock(token="token-1", expires_on=time.time() + 3600)
    first = asyncio.run(azure_client.model_headers("entra", ""))
    second = asyncio.run(azure_client.model_headers("entra", ""))
    assert first == second == {"Authorization": "Bearer token-1"}
    assert entra.call_count == 1


def test_entra_token_is_refreshed_when_about_to_expire(entra):
    entra.side_effect = [MagicMock(token="old", expires_on=time.time() + 60),
                         MagicMock(token="new", expires_on=time.time() + 3600)]
    assert asyncio.run(azure_client.model_headers("entra", ""))["Authorization"] == "Bearer old"
    assert asyncio.run(azure_client.model_headers("entra", ""))["Authorization"] == "Bearer new"
    assert entra.call_count == 2
