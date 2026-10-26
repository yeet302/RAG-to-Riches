"""Planning, wire communication, and final synthesis with mocked model/services."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import agent_service
import answer_synthesis
import azure_client
import main
import orchestrator
import query_analysis
from agents import HTTPAgent
from schemas import AgentFinding, AgentRequest
from settings import Settings


def assignment(agent="docs", stage=1, **changes):
    return {"agent": agent, "stage": stage,
            "question": f"Which {agent} sources explain checkout timeouts?",
            "task": f"Inspect checkout timeout {agent} evidence; cite exact sources.", **changes}


def plan(assignments=None):
    return {"intent": "incident", "summary": "Investigate checkout timeouts",
            "reason": "Compare observed errors with documentation and implementation",
            "assignments": [assignment("logs"), assignment("docs"), assignment("code", 2)]
                           if assignments is None else assignments}


def configured(**changes):
    return Settings(llm_endpoint="https://azure.invalid/openai/v1", llm_model="test-model",
                    llm_api_key="private-azure-key", **changes)


def completion(payload):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]}


@pytest.mark.parametrize("assignments", [
    [],
    [assignment(agent="shell")],
    [assignment(), assignment()],
    [assignment(stage=2)],
    [assignment(stage=3)],
    [assignment(task="   ")],
    [assignment(question=" ")],
    [assignment(question="x" * 2001)],
    [assignment(task="x" * 2001)],
    [assignment(endpoint="https://untrusted.invalid")],
    [assignment("logs"), assignment("docs"), assignment("code"), assignment("logs", 2)],
])
def test_invalid_plans_fall_back_to_complete_keyword_assignments(assignments):
    result = asyncio.run(query_analysis.analyze_question(
        "Checkout is down", configured(), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=completion(plan(assignments))))))
    assert result.source == "keyword_fallback"
    assert result.stage1 == ["logs", "docs"] and result.stage2 == ["code"]
    assert len(result.assignments) == 3
    assert all(a.question == "Checkout is down" and a.task for a in result.assignments)


def test_azure_can_choose_a_plan_outside_the_keyword_route():
    # Intent is metadata; the validated assignments determine the actual work.
    result = asyncio.run(query_analysis.analyze_question(
        "Checkout timeout", configured(), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=completion(plan([assignment("code")]))))))
    assert result.source == "llm" and result.intent == "incident"
    assert result.stage1 == ["code"] and result.stage2 == []
    assert result.assignments[0].question == assignment("code")["question"]


def test_json_plan_to_remote_specialists_to_final_answer(monkeypatch):
    captured = {}
    model_calls = []
    started = {name: asyncio.Event() for name in ("logs", "docs")}
    refs = {"logs": "events.log:L1", "docs": "runbook.md:L2", "code": "checkout.py:L3"}

    def model_response(request):
        body = json.loads(request.content)
        model_calls.append(body)
        assert request.headers["api-key"] == "private-azure-key"
        if len(model_calls) == 1:
            assert "assignments" in body["messages"][0]["content"]
            assert body["messages"][1]["content"] == "Why is checkout timing out?"
            return httpx.Response(200, json=completion(plan()))
        context = json.loads(body["messages"][1]["content"])
        assert context["question"] == "Why is checkout timing out?"
        assert context["agent_statuses"] == {"logs": "ok", "docs": "ok", "code": "ok"}
        assert {e["ref"] for e in context["evidence"]} == set(refs.values())
        evidence_id = context["evidence"][0]["id"]
        return httpx.Response(200, json=completion({
            "answer": f"Checkout has timeout evidence. [{evidence_id}]", "evidence_ids": [evidence_id]}))

    async def model_call(settings, prompt, content, **kwargs):
        return await azure_client.complete_json(
            settings, prompt, content, timeout_seconds=kwargs["timeout_seconds"],
            transport=httpx.MockTransport(model_response))

    async def specialist_response(request):
        name = request.url.host.split(".")[0]
        captured[name] = json.loads(request.content)
        if name in started:
            started[name].set()
            await started["docs" if name == "logs" else "logs"].wait()
            assert captured[name]["evidence_bundle"] == {}
        else:
            assert all(event.is_set() for event in started.values())
            assert len(captured[name]["evidence_bundle"]["evidence"]) == 2
        assert request.headers["authorization"] == f"Bearer {name}-token"
        assert request.headers["x-request-id"] == captured[name]["request_id"]
        expected = assignment(name, 2 if name == "code" else 1)
        assert captured[name]["question"] == expected["question"]
        assert captured[name]["task"] == expected["task"]
        assert captured[name]["original_question"] == "Why is checkout timing out?"
        return httpx.Response(200, json={
            "agent": name, "status": "ok", "summary": f"{name} timeout evidence",
            "evidence": [{"source_type": {"logs": "log", "docs": "doc", "code": "code"}[name],
                          "summary": "Checkout timeout observed in source", "ref": refs[name]}],
        })

    settings = configured(timeout_seconds=1)
    monkeypatch.setattr(query_analysis, "complete_json", model_call)
    monkeypatch.setattr(answer_synthesis, "complete_json", model_call)
    monkeypatch.setattr(main, "SETTINGS", settings)
    monkeypatch.setattr(orchestrator, "SETTINGS", settings)
    monkeypatch.setattr(orchestrator, "AGENTS", {
        name: HTTPAgent(name, f"https://{name}.invalid/run", token=f"{name}-token",
                        transport=httpx.MockTransport(specialist_response))
        for name in ("logs", "docs", "code")})
    with TestClient(main.app) as client:
        assert client.get("/api/health").json()["routing_mode"] == "llm"
        response = client.post("/api/ask", json={
            "question": "Why is checkout timing out?", "conversation_id": "conversation-1"})
    assert response.status_code == 200
    body = response.json()
    assert body["analysis"]["source"] == body["synthesis"]["source"] == "llm"
    assert body["analysis"]["assignments"] == plan()["assignments"]
    assert body["agents_used"] == ["logs", "docs", "code"] and not body["partial"]
    assert len(model_calls) == 2
    assert body["answer"].endswith("[events.log:L1]")
    assert all(message["request_id"] == body["request_id"] for message in captured.values())
    assert all(message["conversation_id"] == "conversation-1" for message in captured.values())
    assert "private-azure-key" not in json.dumps(captured) + response.text


def test_reference_service_passes_assignment_to_specialist_model(monkeypatch):
    captured = {}
    class ModelService:
        async def run(self, question, context):
            captured.update(question=question, **context)
            return AgentFinding(agent="docs", status="no_evidence", summary="No matching evidence")
    monkeypatch.setattr(agent_service, "SETTINGS", Settings())
    monkeypatch.setattr(agent_service, "AGENTS", {"docs": ModelService()})
    message = AgentRequest(request_id="request-1", question="Find the checkout runbook",
                           original_question="Why is checkout timing out?", task="Inspect timeout guidance",
                           intent="incident")
    with TestClient(agent_service.app) as client:
        response = client.post("/agents/docs/run", json=message.model_dump())
    assert response.status_code == 200
    assert captured["task"] == message.task
    assert captured["question"] == message.question
    assert captured["original_question"] == message.original_question


def test_default_modes_follow_azure_configuration():
    assert Settings().effective_routing_mode == "keyword"
    assert configured().effective_routing_mode == "llm"
    assert configured().effective_answer_mode == "llm"
    assert configured(mode="demo").effective_routing_mode == "keyword"
    assert configured(routing_mode="keyword").effective_routing_mode == "keyword"


def test_legacy_deployment_name_is_supported_but_model_takes_precedence(monkeypatch):
    monkeypatch.delenv("AZURE_AI_MODEL", raising=False)
    monkeypatch.setenv("AZURE_AI_DEPLOYMENT", "legacy-deployment")
    assert Settings.from_env().llm_model == "legacy-deployment"
    monkeypatch.setenv("AZURE_AI_MODEL", "preferred-deployment")
    assert Settings.from_env().llm_model == "preferred-deployment"
