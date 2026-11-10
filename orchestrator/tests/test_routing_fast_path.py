import asyncio

import pytest
from fastapi.testclient import TestClient

import main
import orchestrator
import query_analysis
from router import fast_route
from schemas import AgentFinding, Evidence
from settings import Settings


def configured(**changes):
    return Settings(llm_endpoint="https://example.invalid/openai/v1", llm_model="deployment",
                    llm_api_key="test-secret", routing_mode="llm", answer_mode="evidence", **changes)


@pytest.mark.parametrize("question,agent", [
    ("Where is GetAds implemented?", "code"),
    ("Where is the retry function defined?", "code"),
    ("Locate the checkout method", "code"),
    ("Search the codebase for retries", "code"),
    ("Show ad service warning logs from the last hour.", "logs"),
    ("Please list checkout error logs", "logs"),
    ("Fetch request traces since noon", "logs"),
    ("Show documentation for configuring tracing.", "docs"),
    ("Find the runbook for checkout", "docs"),
    ("Where is the documentation for the retry function?", "docs"),
])
def test_clear_lookups_skip_model_and_preserve_question(monkeypatch, question, agent):
    async def forbidden(*args, **kwargs):
        pytest.fail("Clear lookup must not call the planning model")
    monkeypatch.setattr(query_analysis, "_model_analysis", forbidden)
    analysis = asyncio.run(query_analysis.analyze_question(question, configured()))
    assert analysis.fast_path and analysis.source == "keyword"
    assert analysis.stage1 == [agent] and analysis.stage2 == []
    assert analysis.assignments[0].question == question
    assert not analysis.warning


@pytest.mark.parametrize("question", [
    "The ad service is returning GetAds failures with status UNAVAILABLE. What could be causing this?",
    "Where is GetAds implemented, and what can make it return UNAVAILABLE?",
    "Show logs and find the code that emitted them",
    "Show documentation and source for retry behavior",
    "Show logs; investigate the failure",
    "Show logs\nthen explain the cause",
    "How do I restart checkout?",
    "Who owns retry behavior?",
    "How do I fix checkout timeouts?",
    "Show logs to explain why checkout fails",
    "Compare the code implementation with the documentation",
    "Explain GetAds implementation",
])
def test_complex_or_ambiguous_requests_keep_model_planning(monkeypatch, question):
    calls = []
    async def model(original, settings, transport):
        calls.append(original)
        return query_analysis.ModelAnalysis(intent="incident", summary="Investigate", reason="Need sources",
            assignments=[dict(agent="logs",stage=1,question="Focused log task",task="Find operational evidence"),
                         dict(agent="code",stage=2,question="Focused code task",task="Correlate source with logs")])
    monkeypatch.setattr(query_analysis, "_model_analysis", model)
    analysis = asyncio.run(query_analysis.analyze_question(question, configured()))
    assert calls == [question] and analysis.source == "llm" and not analysis.fast_path
    assert analysis.stage2 == ["code"] and analysis.assignments[0].question == "Focused log task"


@pytest.mark.parametrize("explicit,enabled", [(None,False),("llm",True)])
def test_switch_and_explicit_llm_override_allow_model_only_comparison(monkeypatch, explicit, enabled):
    calls = []
    async def model(question, settings, transport):
        calls.append(question)
        return query_analysis.ModelAnalysis(intent="code_lookup",summary="Locate symbol",reason="Inspect source",
            assignments=[dict(agent="code",stage=1,question=question,task="Find symbol and cite source")])
    monkeypatch.setattr(query_analysis,"_model_analysis",model)
    result = asyncio.run(query_analysis.analyze_question("Where is GetAds implemented?",
        configured(routing_fast_path=enabled),explicit))
    assert len(calls)==1 and result.source=="llm" and not result.fast_path


def test_fast_path_works_through_api_with_no_azure_calls(monkeypatch):
    config = configured()
    class Code:
        async def run(self, question, context):
            assert question == "Where is GetAds implemented?"
            return AgentFinding(agent="code",summary="Source found",evidence=[
                Evidence(agent="code",source_type="code",ref="ad.java:L1",summary="GetAds definition")])
    async def forbidden(*args, **kwargs):
        pytest.fail("No routing or synthesis model needed in this evidence-mode request")
    monkeypatch.setattr(query_analysis,"_model_analysis",forbidden)
    monkeypatch.setattr(main,"SETTINGS",config)
    monkeypatch.setattr(orchestrator,"SETTINGS",config)
    monkeypatch.setattr(orchestrator,"AGENTS",{"code":Code()})
    with TestClient(main.app) as client:
        response=client.post("/api/ask",json={"question":"Where is GetAds implemented?"})
    assert response.status_code==200
    body=response.json()
    assert body["analysis"]["fast_path"] and body["agents_used"]==["code"]
    assert body["accuracy_recorded"] and body["performance"]


def test_fast_path_setting_loads_without_modifying_model_settings(monkeypatch):
    monkeypatch.setenv("ROUTING_FAST_PATH","false")
    assert not Settings.from_env().routing_fast_path
    monkeypatch.setenv("ROUTING_FAST_PATH","true")
    assert Settings.from_env().routing_fast_path
    monkeypatch.setenv("ROUTING_FAST_PATH","invalid")
    with pytest.raises(ValueError,match="ROUTING_FAST_PATH"):
        Settings.from_env()
