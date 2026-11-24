"""Header agent status: health check plus each agent's last run."""
from __future__ import annotations

import asyncio

import httpx
from fastapi.testclient import TestClient

import agent_status
from agent_contracts import AgentFinding, PipelineError
from schemas import CopilotResponse


class Remote:
    def __init__(self, name, port):
        self.name, self.url = name, f"http://127.0.0.1:{port}/agents/{name}/run"


AGENTS = {"logs": Remote("logs", 8101), "docs": Remote("docs", 8102), "code": Remote("code", 8103)}


def transport(down=()):
    def handler(request: httpx.Request):
        assert request.url.path == "/health"
        if request.url.port in down:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"status": "ok"})
    return httpx.MockTransport(handler)


def run(agents=AGENTS, down=()):
    return asyncio.run(agent_status.check(agents, transport=transport(down)))


def setup_function():
    agent_status.LAST_RUNS.clear()


def test_health_url_is_derived_from_the_run_url():
    assert agent_status.health_url("http://127.0.0.1:8101/agents/logs/run") == "http://127.0.0.1:8101/health"


def test_all_up_and_never_run_is_ready():
    report = run()
    assert report.ready == report.total == 3 and {a.state for a in report.agents} == {"ready"}


def test_offline_service_is_reported():
    report = run(down={8103})
    code = next(a for a in report.agents if a.name == "code")
    assert code.state == "offline" and "run_local.py" in code.detail and report.ready == 2


def test_up_but_last_question_failed_is_degraded_with_the_error():
    agent_status.remember(CopilotResponse(kind="answer", question="q", answer="a", findings=[
        AgentFinding(agent="docs", status="no_access", summary="Docs model access was denied.",
                     error=PipelineError(code="DOCS_MODEL_ACCESS_DENIED", agent="docs", stage="model",
                                         message="Azure denied access to the docs model.", resolution="r")),
        AgentFinding(agent="logs", status="ok", summary="found"),
    ]))
    by = {a.name: a for a in run().agents}
    assert by["docs"].state == "degraded" and "DOCS_MODEL_ACCESS_DENIED" in by["docs"].detail
    assert by["docs"].last_run.message == "Azure denied access to the docs model."
    assert by["logs"].state == "ready" and "returned evidence" in by["logs"].detail


def test_local_agents_are_labelled_local():
    class Local:
        name = "logs"
    report = run({"logs": Local()})
    assert report.agents[0].state == "local" and report.ready == 1


def test_endpoint_and_health_banner():
    import main
    with TestClient(main.app) as client:
        body = client.get("/api/agents/status").json()
        assert body["total"] == 3 and {a["name"] for a in body["agents"]} == {"logs", "docs", "code"}
        health = client.get("/api/health").json()
    if health["mode"] == "remote":
        assert health["source_note"] is None          # no banner when the live agents answer
