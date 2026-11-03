from __future__ import annotations

import asyncio
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

import agent_service
import orchestrator
from agents import HTTPAgent, LocalSearchAgent, UnavailableAgent, build_agents
from main import app
from router import classify
from schemas import AgentFinding, Evidence
from settings import Settings


@pytest.mark.parametrize("question,stage1,stage2", [
    ("Why is checkout returning errors?", ["logs", "docs"], ["code"]),
    ("Checkout is down", ["logs", "docs"], ["code"]),
    ("How do I fix checkout errors?", ["logs", "docs"], ["code"]),
    ("How to resolve timeouts?", ["logs", "docs"], ["code"]),
    ("How can I repair a broken deployment?", ["logs", "docs"], ["code"]),
    ("The ad service has high CPU", ["logs", "docs"], ["code"]),
    ("Ads are not loading", ["logs", "docs"], ["code"]),
    ("How do I trigger a failure in the ad service?", ["docs"], []),
    ("How to simulate errors?", ["docs"], []),
    ("How does the orchestrator route requests?", ["docs"], []),
    ("How do I restart checkout?", ["docs"], []),
    ("What is the retry policy?", ["docs"], []),
    ("Show documentation for checkout", ["docs"], []),
    ("Where is the retry function defined?", ["docs"], ["code"]),
    ("Explain the retry function", ["docs"], ["code"]),
    ("Search the codebase for retries", ["docs"], ["code"]),
    ("Which commit changed checkout?", ["docs"], ["code"]),
    ("Show checkout logs since noon", ["logs"], []),
    ("Show request traces", ["logs"], []),
    ("Find loggerhead references", ["logs", "docs"], ["code"]),
    ("Investigate checkout", ["logs", "docs"], ["code"]),
])
def test_routing(question, stage1, stage2):
    plan = classify(question)
    assert plan.stage1 == stage1
    assert plan.stage2 == stage2


@pytest.fixture
def corpus(tmp_path):
    docs, logs, code = (tmp_path / name for name in ("docs", "logs", "code"))
    for root in (docs, logs, code):
        root.mkdir()
    (docs / "checkout.md").write_text(
        "# Checkout runbook\nCheckout timeout: check the upstream timeout setting before retrying.\n",
        encoding="utf-8")
    (logs / "checkout.log").write_text("ERROR checkout timeout upstream\n", encoding="utf-8")
    (code / "checkout.py").write_text("def checkout():\n    upstream_timeout = 1\n", encoding="utf-8")
    return {"docs": docs, "logs": logs, "code": code}


def local_registry(corpus):
    return {name: LocalSearchAgent(name, root) for name, root in corpus.items()}


def run(question, registry, timeout=2, **kwargs):
    return asyncio.run(orchestrator.investigate(question, agents=registry,
                                              settings=Settings(timeout_seconds=timeout), **kwargs))


def test_actual_files_through_api(corpus, monkeypatch):
    monkeypatch.setattr(orchestrator, "AGENTS", local_registry(corpus))
    with TestClient(app) as client:
        response = client.post("/api/ask", json={
            "question": "Why is checkout returning timeout errors?",
            "incident_id": "INC-42", "conversation_id": "conversation-7"})
    assert response.status_code == 200
    body = response.json()
    assert body["agents_used"] == ["logs", "docs", "code"]
    assert body["demo"] is False and body["partial"] is False
    assert body["conversation_id"] == "conversation-7" and body["request_id"]
    assert {e["source_type"] for e in body["evidence"]} == {"log", "doc", "code"}
    assert "upstream timeout setting" in body["answer"]
    assert "checkout.md:L2" in body["answer"]
    assert body["likely_cause"] is None and body["confidence"] is None


def test_docs_only_actual_repo_api():
    root = Path(__file__).resolve().parents[1]
    registry = {"docs": LocalSearchAgent("docs", root)}
    response = run("How does the orchestrator route requests?", registry)
    assert response.agents_used == ["docs"]
    assert response.evidence and all(e.source_type == "doc" for e in response.evidence)
    assert response.demo is False and response.partial is False


def test_stage1_concurrency_and_code_handoff():
    async def exercise():
        logs_started, docs_started = asyncio.Event(), asyncio.Event()
        captured = {}
        class Specialist:
            def __init__(self, name):
                self.name = name
            async def run(self, question, context):
                if self.name == "logs":
                    logs_started.set()
                    await docs_started.wait()
                elif self.name == "docs":
                    docs_started.set()
                    await logs_started.wait()
                else:
                    captured.update(context)
                return AgentFinding(agent=self.name, summary="Relevant match", evidence=[
                    Evidence(source_type="doc", summary="checkout upstream_timeout setting",
                             ref=f"{self.name}.md:L1")])
        response = await orchestrator.investigate(
            "Checkout error", agents={n: Specialist(n) for n in ("logs", "docs", "code")},
            settings=Settings(timeout_seconds=0.5), incident_id="INC-42", conversation_id="conv")
        assert all(f.status == "ok" for f in response.findings)
        assert captured["incident_id"] == "INC-42" and captured["conversation_id"] == "conv"
        assert captured["request_id"] == response.request_id
        assert len(captured["evidence_bundle"]["evidence"]) == 2
        assert captured["evidence_bundle"]["agent_statuses"] == {"logs": "ok", "docs": "ok"}
        assert [t.parallel for t in response.trace if t.stage == 1] == [True, True]
    asyncio.run(exercise())


def test_timeout_returns_other_evidence(corpus):
    class Slow:
        name = "logs"
        async def run(self, question, context):
            await asyncio.sleep(2)
    registry = local_registry(corpus)
    registry["logs"] = Slow()
    response = run("Checkout timeout errors", registry, timeout=0.02)
    assert response.partial and response.evidence
    assert response.findings[0].status == "error"
    assert any("timed out" in gap for gap in response.sources_unavailable)
    assert response.findings[-1].status == "ok"


@pytest.mark.parametrize("kind", ["exception", "invalid", "wrong_agent", "wrong_evidence", "empty"])
def test_bad_agent_does_not_break_pipeline(corpus, kind):
    class Bad:
        name = "logs"
        async def run(self, question, context):
            if kind == "exception":
                raise RuntimeError("secret-token-must-not-leak")
            if kind == "invalid":
                return {"agent": "logs", "summary": "Invalid", "confidence": 5}
            return AgentFinding(agent="code" if kind == "wrong_agent" else "logs", summary="Unsupported cause",
                                evidence=[] if kind == "empty" else [Evidence(source_type="log", summary="An event",
                                    ref="events:L1", agent="docs" if kind == "wrong_evidence" else "logs")])
    registry = local_registry(corpus)
    registry["logs"] = Bad()
    response = run("Checkout errors", registry)
    assert response.findings[0].status == ("no_evidence" if kind == "empty" else "error")
    assert response.evidence and response.findings[-1].status == "ok"
    assert "secret-token" not in response.model_dump_json()
    assert "Unsupported cause" not in response.answer


def test_missing_configuration_is_explicit():
    registry = build_agents(Settings(mode="remote"))
    response = run("Checkout errors", registry)
    assert response.partial and not response.evidence and not response.demo
    assert all(f.status == "no_access" for f in response.findings)
    assert "adFailure" not in response.answer


def test_empty_search_is_not_access_failure(tmp_path):
    response = run("What is quuxzyx?", {"docs": LocalSearchAgent("docs", tmp_path)})
    assert response.findings[0].status == "no_evidence"
    assert not response.partial and not response.evidence


def test_hidden_demo_symlink_and_large_files_are_excluded(tmp_path):
    (tmp_path / ".env.txt").write_text("needle secret")
    (tmp_path / "demo_data.py").write_text("needle canned")
    (tmp_path / "big.txt").write_bytes(b"needle " * 150_000)
    (tmp_path / "actual.md").write_text("needle real result", encoding="utf-8")
    try:
        (tmp_path / "linked.txt").symlink_to(tmp_path / "actual.md")
    except OSError:
        pass  # Windows may restrict creating symlinks; the Linux run covers this too.
    response = run("What is needle?", {"docs": LocalSearchAgent("docs", tmp_path)})
    assert len(response.evidence) == 1 and response.evidence[0].ref.startswith("actual.md:")
    assert response.partial and any("1 MB" in s for s in response.sources_unavailable)


@pytest.mark.parametrize("status", [401, 403, 500])
def test_http_errors_are_partial(status):
    transport = httpx.MockTransport(lambda req: httpx.Response(status, text="private response"))
    agent = HTTPAgent("docs", "https://example.invalid/run", transport=transport)
    response = run("What is checkout?", {"docs": agent})
    assert response.partial
    assert response.findings[0].status == ("no_access" if status in (401, 403) else "error")
    assert "private response" not in response.model_dump_json()


@pytest.mark.parametrize("body", ["not json", '{"agent":"docs"}',
                                   '{"agent":"code","summary":"wrong"}'])
def test_http_invalid_payload(body):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=body))
    response = run("What is checkout?", {"docs": HTTPAgent("docs", "https://example.invalid/run", transport=transport)})
    assert response.partial and response.findings[0].status == "error"


def test_evidence_deduplication():
    class SameEvidence:
        def __init__(self, name):
            self.name = name
        async def run(self, question, context):
            return AgentFinding(agent=self.name, summary="Match", evidence=[
                Evidence(source_type="doc", summary="Same source", ref="runbook.md:L1")])
    response = run("Checkout errors", {n: SameEvidence(n) for n in ("logs", "docs", "code")})
    assert len(response.evidence) == 1


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout_rejected(timeout):
    with pytest.raises(ValueError):
        Settings(timeout_seconds=timeout)


def test_request_validation_and_non_demo_incidents():
    with TestClient(app) as client:
        assert client.post("/api/ask", json={"question": " "}).status_code == 400
        assert client.post("/api/ask", json={"question": "x" * 2001}).status_code == 400
        assert client.post("/api/ask", json={}).status_code == 422
        assert client.get("/api/incidents").json() == []


def test_three_agents_over_real_http(corpus, monkeypatch):
    captured = {}
    class RecordingCode(LocalSearchAgent):
        async def run(self, question, context):
            captured.update(context)
            return await super().run(question, context)
    registry = local_registry(corpus)
    registry["code"] = RecordingCode("code", corpus["code"])
    monkeypatch.setattr(agent_service, "AGENTS", registry)
    monkeypatch.setattr(agent_service, "SETTINGS", Settings(agent_tokens={n: "test-token" for n in registry}))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(agent_service.app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        clients = {n: HTTPAgent(n, f"{base}/agents/{n}/run", token="test-token") for n in registry}
        response = run("Why is checkout returning timeout errors?", clients,
                       incident_id="INC-42", conversation_id="http-conversation")
        assert all(f.status == "ok" for f in response.findings)
        assert {e.source_type for e in response.evidence} == {"log", "doc", "code"}
        assert response.demo is False and response.partial is False
        assert len(captured["evidence_bundle"]["evidence"]) == 2
        assert captured["request_id"] == response.request_id
        assert captured["incident_id"] == "INC-42"
        denied = run("What is checkout?", {"docs": HTTPAgent("docs", f"{base}/agents/docs/run", token="wrong")})
        assert denied.findings[0].status == "no_access"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
    assert not thread.is_alive()


@pytest.mark.parametrize("status", ["error", "no_access"])
def test_agent_reported_failure_cannot_contribute_claims(corpus, status):
    class ReportedFailure:
        name = "logs"
        async def run(self, question, context):
            return AgentFinding(agent="logs", status=status, summary="No reliable access",
                                evidence=[Evidence(source_type="log", summary="Unsupported cause", ref="events:L1")])
    registry = local_registry(corpus)
    registry["logs"] = ReportedFailure()
    response = run("Checkout errors", registry)
    assert response.partial and response.sources_unavailable
    assert "Unsupported cause" not in response.answer
    assert response.findings[-1].status == "ok"


@pytest.mark.parametrize("field", ["summary", "ref"])
def test_empty_evidence_citation_is_invalid(field):
    from pydantic import ValidationError
    payload = {"source_type": "doc", "summary": "Useful excerpt", "ref": "runbook.md:L1"}
    payload[field] = "   "
    with pytest.raises(ValidationError):
        Evidence.model_validate(payload)


def test_retrieval_prefers_guidance_over_question_echo(tmp_path):
    (tmp_path / "guide.md").write_text(
        'Example: {"question": "How does the orchestrator route requests?"}\n'
        'The orchestrator routes requests selectively to the required specialists.\n', encoding="utf-8")
    response = run("How does the orchestrator route requests?", {"docs": LocalSearchAgent("docs", tmp_path)})
    assert len(response.evidence) == 1
    assert response.evidence[0].ref.startswith("guide.md:L2")
    assert "selectively" in response.answer



def test_prepared_demo_configuration_is_rejected():
    with pytest.raises(ValueError, match="prepared demo answers are disabled"):
        Settings(mode="demo")


def test_remote_subagents_are_the_default(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCHESTRATOR_MODE", raising=False)
    assert Settings.from_env().mode == "remote"
    # An operator's configured local corpus must not be used implicitly.
    (tmp_path / "runbook.md").write_text("adFailure is on; use this prepared fix")
    registry = build_agents(Settings(docs_root=tmp_path, code_root=tmp_path, logs_root=tmp_path))
    assert all(isinstance(agent, UnavailableAgent) for agent in registry.values())


@pytest.mark.parametrize("question", [
    "Ads aren't loading on product pages and the ad service is returning errors. What's wrong?",
    "The ad service is slow and CPU is spiking. Why?",
    "How do I trigger a failure in the ad service for testing?",
])
def test_previous_demo_questions_require_subagent_evidence(monkeypatch, tmp_path, question):
    import answer_synthesis

    async def forbidden(*args, **kwargs):
        pytest.fail("A final model answer must not be generated without subagent evidence")

    monkeypatch.setattr(answer_synthesis, "complete_json", forbidden)
    (tmp_path / "prepared.md").write_text("The adFailure feature flag is on. Turn it off.")
    config = Settings(mode="remote", routing_mode="keyword", answer_mode="llm",
                      llm_endpoint="https://example.invalid", llm_model="deployment", llm_api_key="test-key",
                      docs_root=tmp_path, code_root=tmp_path, logs_root=tmp_path,
                      agent_urls={name: f"https://example.invalid/{name}" for name in ("docs", "logs", "code")})
    registry = build_agents(config)
    for agent in registry.values():
        agent.transport = httpx.MockTransport(lambda req: httpx.Response(403))
    response = asyncio.run(orchestrator.investigate(question, agents=registry, settings=config))
    assert response.partial and not response.evidence
    assert response.demo is False
    assert all(f.status == "no_access" for f in response.findings)
    assert response.likely_cause is None and response.confidence is None and response.steps == []
    assert "could not find supporting evidence" in response.answer
    assert "adFailure" not in response.answer
    assert response.synthesis.source == "evidence"


def test_home_endpoints_never_return_synthetic_incidents_or_suggestions():
    with TestClient(app) as client:
        assert client.get("/api/incidents").json() == []
        assert client.get("/api/suggestions").json() == []
