"""Deadline, cancellation, and immutable retrieval caching regressions; no cloud calls."""
import asyncio
import base64
from collections import OrderedDict
from types import SimpleNamespace
import threading

import pytest

import code_agent.budget as budget_module
from code_agent.budget import InvestigationBudget, CodeDeadlineExceeded, perform, use_budget, network_timeout
from code_agent.agent import CodeAgent
from code_agent.config import Settings
from code_agent.retrieval import CodeRetriever

SHA = "a" * 40


def test_one_deadline_is_shared_and_prevents_later_operations(monkeypatch):
    clock = [0.]
    monkeypatch.setattr(budget_module.time, "monotonic", lambda: clock[0])
    budget = InvestigationBudget(10, "bounded")
    ran = []
    with use_budget(budget):
        assert network_timeout(30) == 10
        clock[0] = 8
        assert network_timeout(30) == 2
        def slow():
            clock[0] = 11
        with pytest.raises(CodeDeadlineExceeded):
            perform("foundry.response.1", slow)
        with pytest.raises(CodeDeadlineExceeded):
            perform("tool.read_source", lambda: ran.append(True))
    assert not ran


def test_async_deadline_returns_and_stops_worker_before_next_tool(monkeypatch):
    agent = CodeAgent.__new__(CodeAgent)
    agent.settings = SimpleNamespace(investigation_timeout_seconds=.05)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    later_calls = []
    def worker(question, context):
        budget = context["_budget"]
        with use_budget(budget):
            budget.stage = "foundry.response.1"
            started.set()
            release.wait(2)
            try:
                perform("tool.read_source", lambda: later_calls.append(True))
            except CodeDeadlineExceeded:
                pass
            finally:
                finished.set()
    monkeypatch.setattr(agent, "_investigate", worker)
    async def check():
        finding = await agent.run("private question", {"request_id":"deadline-test"})
        try:
            assert started.is_set()
            assert finding.error.code == "CODE_INVESTIGATION_TIMEOUT"
            assert "foundry.response.1" in finding.error.message
            assert finding.error.retryable
        finally:
            release.set()
        assert await asyncio.to_thread(finished.wait, 2)
    asyncio.run(check())
    assert not later_calls


def retriever():
    instance = CodeRetriever.__new__(CodeRetriever)
    instance.settings = SimpleNamespace(default_ref="main", request_timeout_seconds=30)
    instance._immutable_cache = OrderedDict()
    instance._cache_lock = threading.Lock()
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        if "/contents/" in url:
            payload = dict(type="file",size=20,content=base64.b64encode(b"line1\nline2\n").decode())
        elif "/git/trees/" in url:
            payload = dict(tree=[dict(type="blob",path="service.py")])
        else:
            payload = dict(sha=SHA)
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:payload)
    instance.session = SimpleNamespace(get=get)
    return instance, calls


def test_pinned_commit_and_repeated_tree_source_reads_avoid_duplicate_network_calls():
    instance, calls = retriever()
    assert instance.resolve_commit(SHA) == SHA and not calls
    for _ in range(2):
        assert instance.list_repository_files(ref=SHA)["files"] == ["service.py"]
        assert instance.read_source("service.py",1,2,ref=SHA)["lines"][0]["text"]=="line1"
    assert len(calls) == 2


def test_branch_resolution_stays_fresh_and_file_cache_is_commit_specific():
    instance, calls = retriever()
    instance.resolve_commit("main")
    instance.resolve_commit("main")
    instance.read_source("service.py",1,2,ref=SHA)
    instance.read_source("service.py",1,2,ref="b"*40)
    assert len(calls) == 4


def test_github_timeout_is_capped_by_remaining_budget(monkeypatch):
    clock = [0.]
    monkeypatch.setattr(budget_module.time,"monotonic",lambda:clock[0])
    instance, calls = retriever()
    budget = InvestigationBudget(10)
    clock[0]=8
    with use_budget(budget):
        instance.resolve_commit("main")
    assert calls[0][1]["timeout"] == 2


@pytest.mark.parametrize("value",[0,-1,float("nan"),float("inf")])
def test_invalid_timeout_configuration_is_rejected(value):
    with pytest.raises(ValueError,match="timeouts"):
        Settings("project","model","search","index","","yeet302/RAG-to-Riches","agent",
                 investigation_timeout_seconds=value)


def test_stage_logs_identify_latency_without_logging_arguments(caplog):
    budget = InvestigationBudget(10,"stage-check")
    with caplog.at_level("INFO",logger="code_agent"),use_budget(budget):
        perform("tool.read_source",lambda private: private,"private-source-and-token")
    assert "request_id=stage-check" in caplog.text
    assert "stage=tool.read_source" in caplog.text and "latency_ms=" in caplog.text
    assert "private-source-and-token" not in caplog.text
