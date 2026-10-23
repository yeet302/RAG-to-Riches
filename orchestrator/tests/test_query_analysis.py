from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import main
import orchestrator
import query_analysis
from query_analysis import ModelAnalysis, analyze_question, completion_url
from schemas import AgentFinding, Evidence
from settings import Settings


def configured(**kwargs):
    values = {
        "routing_mode": "llm",
        "answer_mode": "evidence",
        "llm_endpoint": "https://example.invalid/openai/v1",
        "llm_model": "test-deployment",
        "llm_api_key": "test-secret-key",
    }
    values.update(kwargs)
    return Settings(**values)


def completion(content=None, finish_reason="stop", **message):
    if content is None:
        content = json.dumps({
            "intent": "code_lookup",
            "summary": "Find the implementation responsible for retry behavior.",
            "reason": "Implementation inspection needs documentation context and code.",
        })
    return {"choices": [{"finish_reason": finish_reason, "message": {"content": content, **message}}]}


def test_keyword_override_makes_no_model_request():
    def forbidden(request):
        pytest.fail("Keyword routing must not send a model request")
    result = asyncio.run(analyze_question(
        "Checkout is down", configured(), "keyword", transport=httpx.MockTransport(forbidden)))
    assert result.source == "keyword"
    assert result.stage1 == ["logs", "docs"] and result.stage2 == ["code"]


def test_foundry_request_and_semantic_route():
    captured = {}
    def respond(request):
        captured["request"] = request
        return httpx.Response(200, json=completion())
    question = "Who owns retry behavior?"
    result = asyncio.run(analyze_question(
        question, configured(), transport=httpx.MockTransport(respond)))
    request = captured["request"]
    body = json.loads(request.content)
    assert str(request.url) == "https://example.invalid/openai/v1/chat/completions"
    assert request.headers["api-key"] == "test-secret-key"
    assert body["model"] == "test-deployment"
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1] == {"role": "user", "content": question}
    assert body["response_format"] == {"type": "json_object"}
    assert result.source == "llm" and result.intent == "code_lookup"
    assert result.stage1 == ["docs"] and result.stage2 == ["code"]
    assert result.warning is None
    assert "test-secret-key" not in result.model_dump_json()


@pytest.mark.parametrize("intent,stage1,stage2", [
    ("how_to", ["docs"], []),
    ("logs_lookup", ["logs"], []),
    ("code_lookup", ["docs"], ["code"]),
    ("incident", ["logs", "docs"], ["code"]),
    ("unknown", ["logs", "docs"], ["code"]),
])
def test_model_can_only_select_supported_routes(intent, stage1, stage2):
    response = completion(json.dumps({"intent": intent, "summary": "Question summary", "reason": "Route reason"}))
    result = asyncio.run(analyze_question(
        "A question", configured(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))))
    assert result.source == "llm"
    assert result.stage1 == stage1 and result.stage2 == stage2


@pytest.mark.parametrize("response", [
    completion("not JSON"),
    completion('{"intent":"shell","summary":"x","reason":"x"}'),
    completion('{"intent":"how_to","summary":" ","reason":"x"}'),
    completion('{"intent":"how_to","summary":"x","reason":"x","endpoint":"https://untrusted.invalid"}'),
    completion(finish_reason="length"),
    completion(refusal="Cannot classify"),
    {"choices": []},
    {"choices": [{"finish_reason": "stop", "message": {"content": None}}]},
])
def test_invalid_model_output_falls_back(response):
    result = asyncio.run(analyze_question(
        "Checkout is down", configured(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))))
    assert result.source == "keyword_fallback"
    assert result.intent == "incident"
    assert result.warning


@pytest.mark.parametrize("status", [401, 403, 429, 500, 302])
def test_provider_errors_fall_back_without_exposing_body_or_key(status, caplog):
    result = asyncio.run(analyze_question(
        "How do I restart checkout?", configured(),
        transport=httpx.MockTransport(lambda request: httpx.Response(
            status, text="private provider body test-secret-key",
            headers={"location": "https://other.invalid"}))))
    assert result.source == "keyword_fallback"
    assert result.stage1 == ["docs"]
    assert "test-secret-key" not in result.model_dump_json() + caplog.text
    assert "private provider body" not in result.model_dump_json() + caplog.text


def test_timeout_falls_back():
    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=completion())
    result = asyncio.run(analyze_question(
        "Checkout is down", configured(llm_timeout_seconds=0.01),
        transport=httpx.MockTransport(slow)))
    assert result.source == "keyword_fallback" and result.intent == "incident"


def test_missing_config_and_demo_do_not_call_azure():
    def forbidden(request):
        pytest.fail("No model request should be sent")
    transport = httpx.MockTransport(forbidden)
    missing = asyncio.run(analyze_question("Checkout is down", Settings(), "llm", transport=transport))
    demo = asyncio.run(analyze_question("Checkout is down", configured(mode="demo"), transport=transport))
    assert missing.source == demo.source == "keyword_fallback"
    assert "not configured" in missing.warning
    assert "Demo mode" in demo.warning


def test_inference_compatibility_request():
    def respond(request):
        assert request.url.path == "/models/chat/completions"
        assert request.url.params["api-version"] == "2024-05-01-preview"
        return httpx.Response(200, json=completion())
    result = asyncio.run(analyze_question(
        "Who owns retries?", configured(llm_endpoint="https://example.invalid/models",
                                        llm_api_style="inference"),
        transport=httpx.MockTransport(respond)))
    assert result.source == "llm"


@pytest.mark.parametrize("endpoint,style,expected", [
    ("https://example.invalid", "openai_v1", "https://example.invalid/openai/v1/chat/completions"),
    ("https://example.invalid/openai/v1/", "openai_v1", "https://example.invalid/openai/v1/chat/completions"),
    ("https://example.invalid/openai/v1/chat/completions", "openai_v1", "https://example.invalid/openai/v1/chat/completions"),
    ("https://example.invalid", "inference", "https://example.invalid/models/chat/completions"),
])
def test_completion_url(endpoint, style, expected):
    assert completion_url(configured(llm_endpoint=endpoint, llm_api_style=style)) == expected


@pytest.mark.parametrize("values", [
    {"routing_mode": "invalid"},
    {"llm_api_style": "invalid"},
    {"llm_timeout_seconds": 0},
    {"llm_timeout_seconds": float("nan")},
    {"llm_endpoint": "http://example.invalid"},
    {"llm_endpoint": "https://user:password@example.invalid"},
    {"llm_endpoint": "https://example.invalid?api-key=secret"},
    {"llm_endpoint": "https://example.invalid/api/projects/project"},
])
def test_invalid_llm_configuration(values):
    with pytest.raises(ValueError):
        configured(**values)


def test_secret_is_excluded_from_settings_repr_and_environment_is_loaded(monkeypatch):
    monkeypatch.setenv("AZURE_AI_ENDPOINT", "https://example.invalid/openai/v1/")
    monkeypatch.setenv("AZURE_AI_MODEL", "test-deployment")
    monkeypatch.setenv("AZURE_AI_API_KEY", "test-secret-key")
    monkeypatch.setenv("ROUTING_MODE", "llm")
    settings = Settings.from_env()
    assert settings.llm_configured and settings.routing_mode == "llm"
    assert settings.llm_endpoint == "https://example.invalid/openai/v1"
    assert "test-secret-key" not in repr(settings)


def test_api_mode_selection_route_and_ask_use_the_same_analysis(monkeypatch):
    calls = []
    async def fake_model(question, settings, transport):
        calls.append(question)
        return ModelAnalysis(intent="code_lookup", summary="Locate retry handling", reason="Inspect implementation")
    class Specialist:
        def __init__(self, name):
            self.name = name
        async def run(self, question, context):
            return AgentFinding(agent=self.name, summary="Relevant finding", evidence=[
                Evidence(source_type="code" if self.name == "code" else "doc",
                         summary="Retry handling", ref=f"{self.name}:L1")])
    settings = configured(routing_mode="keyword")
    monkeypatch.setattr(query_analysis, "_model_analysis", fake_model)
    monkeypatch.setattr(main, "SETTINGS", settings)
    monkeypatch.setattr(orchestrator, "SETTINGS", settings)
    monkeypatch.setattr(orchestrator, "AGENTS", {name: Specialist(name) for name in ("docs", "code")})
    with TestClient(main.app) as client:
        health = client.get("/api/health").json()
        assert health["llm_configured"] and health["routing_mode"] == "keyword"
        assert "test-secret-key" not in json.dumps(health)
        route = client.get("/api/route", params={"q": "Who owns retries?", "routing_mode": "llm"}).json()
        body = client.post("/api/ask", json={
            "question": "Who owns retries?", "routing_mode": "llm", "conversation_id": "analysis-test"
        }).json()
        assert route["source"] == body["analysis"]["source"] == "llm"
        assert route["stage1_parallel"] == ["docs"] and route["stage2"] == ["code"]
        assert body["agents_used"] == ["docs", "code"] and body["evidence"]
        assert body["conversation_id"] == "analysis-test"
        assert "llm" in body["trace"][0]["action"]
        assert client.post("/api/ask", json={"question": "Hello", "routing_mode": "bad"}).status_code == 422
        assert client.get("/api/route", params={"q": " "}).status_code == 400
    assert len(calls) == 2
