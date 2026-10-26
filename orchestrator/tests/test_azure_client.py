from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from azure_client import complete_json, completion_url
from query_analysis import analyze_question
from answer_synthesis import generate_answer
from schemas import AgentFinding, CopilotResponse, Evidence
from settings import Settings


def settings():
    return Settings(llm_endpoint="https://example.invalid/openai/v1/responses",
                    llm_model="configured-deployment", llm_api_key="test-secret")


def response_body(text='{"status":"ok"}', **changes):
    return {"status": "completed", "error": None, "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": text}]}], **changes}


def test_full_responses_endpoint_and_json_wire_format():
    def respond(request):
        assert str(request.url) == "https://example.invalid/openai/v1/responses"
        assert request.headers["api-key"] == "test-secret"
        body = json.loads(request.content)
        assert body == {"model": "configured-deployment", "store": False,
                        "input": [{"role": "system", "content": "Return JSON"},
                                  {"role": "user", "content": "Synthetic question"}],
                        "text": {"format": {"type": "json_object"}}}
        return httpx.Response(200, json=response_body())
    assert completion_url(settings()) == settings().llm_endpoint
    content = asyncio.run(complete_json(settings(), "Return JSON", "Synthetic question",
                          timeout_seconds=1, transport=httpx.MockTransport(respond)))
    assert json.loads(content) == {"status": "ok"}


@pytest.mark.parametrize("body", [
    response_body(status="incomplete"),
    response_body(error={"code": "failed"}),
    response_body(output=[]),
    response_body(text=" "),
    response_body(text=42),
    response_body(output=[{"type": "message", "role": "assistant", "status": "incomplete",
                          "content": [{"type": "output_text", "text": "{}"}]}]),
    response_body(output=[{"type": "message", "role": "assistant", "status": "completed",
                          "content": [{"type": "refusal", "refusal": "Cannot comply"}]}]),
])
def test_responses_rejects_incomplete_refused_and_missing_text(body):
    with pytest.raises(ValueError):
        asyncio.run(complete_json(settings(), "Return JSON", "Synthetic question",
                    timeout_seconds=1, transport=httpx.MockTransport(
                        lambda request: httpx.Response(200, json=body))))


def test_responses_endpoint_supports_planning_and_cited_synthesis():
    plan = {"intent": "how_to", "summary": "Find guidance", "reason": "Docs can answer",
            "assignments": [{"agent": "docs", "stage": 1, "question": "Find guidance",
                             "task": "Find the relevant runbook and cite it"}]}
    analysis = asyncio.run(analyze_question("How do I investigate?", settings(), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=response_body(json.dumps(plan))))))
    assert analysis.source == "llm" and analysis.stage1 == ["docs"]
    evidence = Evidence(source_type="doc", summary="Synthetic guidance", ref="synthetic:1", agent="docs")
    result = CopilotResponse(kind="answer", question="How do I investigate?", answer="Fallback",
                            analysis=analysis, evidence=[evidence], findings=[AgentFinding(
                                agent="docs", summary="Synthetic guidance", evidence=[evidence])])
    synthesized = asyncio.run(generate_answer(result, settings(), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=response_body(json.dumps({
            "answer": "Use the guidance. [E1]", "evidence_ids": ["E1"]}))))))
    assert synthesized.synthesis.source == "llm"
    assert synthesized.answer == "Use the guidance. [synthetic:1]"
