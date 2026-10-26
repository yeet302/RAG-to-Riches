from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import answer_synthesis
import main
import orchestrator
from answer_synthesis import evidence_context, generate_answer
from schemas import AgentFinding, CopilotResponse, Evidence, TraceEvent
from settings import Settings


def configured(**kwargs):
    values = {
        "llm_endpoint": "https://example.invalid/openai/v1",
        "llm_model": "test-deployment",
        "llm_api_key": "test-secret-key",
    }
    values.update(kwargs)
    return Settings(**values)


def result(**kwargs):
    evidence = Evidence(source_type="doc", summary="Checkout uses a configured upstream timeout.",
                        ref="runbooks/checkout.md:L12-L15",
                        snippet="Check upstream latency against the configured timeout.", agent="docs")
    values = {
        "kind": "answer", "question": "How do I investigate checkout timeouts?",
        "answer": "Original evidence-list fallback", "evidence": [evidence],
        "findings": [AgentFinding(agent="docs", summary="Timeout guidance", evidence=[evidence])],
        "agents_used": ["docs"],
        "trace": [TraceEvent(stage=3, agent="orchestrator", action="Combined evidence")],
        "request_id": "test-request",
    }
    values.update(kwargs)
    return CopilotResponse(**values)


def completion(content=None, finish_reason="stop", **message):
    if content is None:
        content = json.dumps({
            "answer": "Compare upstream latency with the configured timeout. [E1]",
            "evidence_ids": ["E1"],
        })
    return {"choices": [{"finish_reason": finish_reason, "message": {"content": content, **message}}]}


def test_grounded_answer_request_citations_and_metadata():
    captured = {}
    original = result()
    def respond(request):
        captured["body"] = json.loads(request.content)
        assert request.headers["api-key"] == "test-secret-key"
        return httpx.Response(200, json=completion())
    response = asyncio.run(generate_answer(
        original, configured(), transport=httpx.MockTransport(respond)))
    body = captured["body"]
    payload = json.loads(body["messages"][1]["content"])
    assert body["model"] == "test-deployment"
    assert body["response_format"] == {"type": "json_object"}
    assert payload["question"] == original.question
    assert payload["evidence"][0]["snippet"] == original.evidence[0].snippet
    assert payload["evidence"][0]["id"] == "E1"
    assert payload["agent_statuses"] == {"docs": "ok"}
    assert "untrusted data" in body["messages"][0]["content"]
    assert response.answer.endswith("[runbooks/checkout.md:L12-L15]")
    assert response.synthesis.source == "llm"
    assert response.synthesis.citations == ["runbooks/checkout.md:L12-L15"]
    assert response.trace[-1].stage == 4
    assert original.answer == "Original evidence-list fallback"
    assert len(original.trace) == 1
    assert response.evidence == original.evidence
    assert response.likely_cause is None and response.confidence is None


@pytest.mark.parametrize("generated", [
    {"answer": "Invented finding [E99]", "evidence_ids": ["E99"]},
    {"answer": "Missing citation", "evidence_ids": ["E1"]},
    {"answer": "Undeclared citation [E1]", "evidence_ids": []},
    {"answer": "Unknown citation [made-up.md]", "evidence_ids": ["E1"]},
    {"answer": "Repeated ID [E1]", "evidence_ids": ["E1", "E1"]},
    {"answer": "A finding [E1] [E2]", "evidence_ids": ["E1"]},
    {"answer": " ", "evidence_ids": ["E1"]},
    {"answer": "A finding [E1]", "evidence_ids": ["E1"], "likely_cause": "unsupported"},
])
def test_invalid_or_fabricated_citations_preserve_fallback(generated):
    original = result()
    response = asyncio.run(generate_answer(
        original, configured(),
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json=completion(json.dumps(generated))))))
    assert response.answer == original.answer
    assert response.evidence == original.evidence
    assert response.synthesis.source == "evidence_fallback"
    assert response.synthesis.warning


@pytest.mark.parametrize("body", [
    completion("not JSON"),
    completion(finish_reason="length"),
    completion(refusal="Cannot answer"),
    {"choices": []},
])
def test_invalid_provider_response_preserves_fallback(body):
    response = asyncio.run(generate_answer(
        result(), configured(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))))
    assert response.synthesis.source == "evidence_fallback"
    assert response.answer == "Original evidence-list fallback"


@pytest.mark.parametrize("status", [401, 429, 500, 302])
def test_provider_error_does_not_expose_body_or_key(status, caplog):
    response = asyncio.run(generate_answer(
        result(), configured(),
        transport=httpx.MockTransport(lambda request: httpx.Response(
            status, text="private provider body test-secret-key",
            headers={"location": "https://other.invalid"}))))
    assert response.synthesis.source == "evidence_fallback"
    assert "test-secret-key" not in response.model_dump_json() + caplog.text
    assert "private provider body" not in response.model_dump_json() + caplog.text


def test_synthesis_timeout_preserves_evidence():
    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=completion())
    response = asyncio.run(generate_answer(
        result(), configured(synthesis_timeout_seconds=0.01),
        transport=httpx.MockTransport(slow)))
    assert response.synthesis.source == "evidence_fallback"
    assert response.evidence and response.answer == "Original evidence-list fallback"


def test_evidence_selection_demo_and_no_evidence_make_no_model_request():
    def forbidden(request):
        pytest.fail("This mode must not send a synthesis request")
    transport = httpx.MockTransport(forbidden)
    explicit = asyncio.run(generate_answer(result(), configured(), "evidence", transport=transport))
    demo = asyncio.run(generate_answer(result(demo=True), configured(mode="demo"), "llm", transport=transport))
    empty = asyncio.run(generate_answer(result(evidence=[]), configured(), "llm", transport=transport))
    missing = asyncio.run(generate_answer(result(), Settings(), "llm", transport=transport))
    assert explicit.synthesis.source == "evidence"
    assert demo.synthesis.source == "demo"
    assert empty.synthesis.source == "evidence" and empty.synthesis.warning
    assert missing.synthesis.source == "evidence_fallback"


def test_default_answer_mode_is_independent_of_routing():
    assert configured(routing_mode="keyword").effective_answer_mode == "llm"
    assert configured(routing_mode="llm", answer_mode="evidence").effective_answer_mode == "evidence"
    assert Settings().effective_answer_mode == "evidence"
    assert configured(mode="demo").effective_answer_mode == "evidence"


@pytest.mark.parametrize("values", [
    {"answer_mode": "invalid"},
    {"synthesis_timeout_seconds": 0},
    {"synthesis_timeout_seconds": float("nan")},
])
def test_synthesis_configuration_validation(values):
    with pytest.raises(ValueError):
        configured(**values)


def test_context_is_bounded_and_all_evidence_is_preserved():
    evidence = [Evidence(source_type="doc", summary="x" * 1000, snippet="y" * 5000,
                         ref=f"file-{index}.md:L1", agent="docs") for index in range(30)]
    original = result(evidence=evidence)
    payload, references, truncated = evidence_context(original)
    assert truncated and payload["context_truncated"]
    assert len(references) <= 12
    assert len(json.dumps(payload, ensure_ascii=False)) <= 32_000
    assert len(payload["evidence"][0]["summary"]) <= 800
    assert len(payload["evidence"][0]["snippet"]) <= 1800
    response = asyncio.run(generate_answer(
        original, configured(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=completion()))))
    assert response.synthesis.source == "llm" and response.synthesis.context_truncated
    assert response.synthesis.warning
    assert len(response.evidence) == 30 and original.evidence[0].snippet == "y" * 5000


def test_partial_result_keeps_coverage_gap_even_if_model_omits_it():
    response = asyncio.run(generate_answer(
        result(partial=True, sources_unavailable=["logs agent timed out"]),
        configured(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=completion()))))
    assert response.synthesis.source == "llm" and response.partial
    assert "logs agent timed out" in response.answer
    assert response.sources_unavailable == ["logs agent timed out"]


def test_api_defaults_to_ai_answer_and_allows_evidence_override(monkeypatch):
    calls = []
    async def fake_completion(settings, prompt, user_content, **kwargs):
        calls.append(json.loads(user_content))
        return json.dumps({"answer": "Compare upstream latency and timeout. [E1]", "evidence_ids": ["E1"]})
    class Docs:
        name = "docs"
        async def run(self, question, context):
            return result().findings[0]
    settings = configured(routing_mode="keyword")
    monkeypatch.setattr(main, "SETTINGS", settings)
    monkeypatch.setattr(orchestrator, "SETTINGS", settings)
    monkeypatch.setattr(orchestrator, "AGENTS", {"docs": Docs()})
    monkeypatch.setattr(answer_synthesis, "complete_json", fake_completion)
    with TestClient(main.app) as client:
        assert client.get("/api/health").json()["answer_mode"] == "llm"
        body = client.post("/api/ask", json={
            "question": "How do I restart checkout?", "routing_mode": "keyword"
        }).json()
        assert body["analysis"]["source"] == "keyword"
        assert body["synthesis"]["source"] == "llm"
        assert body["answer"].endswith("[runbooks/checkout.md:L12-L15]")
        assert body["evidence"] and body["trace"][-1]["stage"] == 4
        evidence = client.post("/api/ask", json={
            "question": "How do I restart checkout?", "answer_mode": "evidence"
        }).json()
        assert evidence["synthesis"]["source"] == "evidence"
        assert len(calls) == 1
        assert client.post("/api/ask", json={"question": "Hello", "answer_mode": "bad"}).status_code == 422


def test_failed_findings_never_reach_synthesis(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("No usable evidence means no synthesis request")
    class FailedDocs:
        name = "docs"
        async def run(self, question, context):
            return AgentFinding(agent="docs", status="error", summary="Failed search",
                                evidence=[Evidence(source_type="doc", summary="Untrusted claim", ref="bad:L1")])
    monkeypatch.setattr(answer_synthesis, "complete_json", forbidden)
    response = asyncio.run(orchestrator.investigate(
        "How do I restart checkout?", agents={"docs": FailedDocs()}, settings=configured(routing_mode="keyword")))
    assert not response.evidence and response.partial
    assert response.synthesis.source == "evidence"
    assert "Untrusted claim" not in response.answer
