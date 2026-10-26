"""Optional real-reference integration tests; set ORCHESTRATOR_ROOT to its source directory.

Only documentation/model network calls are mocked. Routing, JSON adapter, schema
validation, HTTP endpoints, and evidence handoff use the reference implementation.
"""
import asyncio
from dataclasses import replace
import importlib
import os
from pathlib import Path
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from docs_agent.service import create_app
from .support import CASES, offline_agent

ROOT = os.getenv('ORCHESTRATOR_ROOT')
pytestmark = pytest.mark.skipif(not ROOT, reason='Set ORCHESTRATOR_ROOT for reference integration tests')


def reference(monkeypatch, retrieval_only=False):
    assert Path(ROOT, 'agents.py').is_file()
    monkeypatch.syspath_prepend(ROOT)
    agents = importlib.import_module('agents')
    pipeline = importlib.import_module('orchestrator')
    settings = importlib.import_module('settings')
    schemas = importlib.import_module('schemas')
    hosted = offline_agent(token='test-token')
    if retrieval_only:
        hosted.settings = replace(hosted.settings, api_key='')
        def unexpected(_):
            pytest.fail('Retrieval-only integration must not call the LLM')
        hosted.llm_transport = httpx.MockTransport(unexpected)
    docs = agents.HTTPAgent('docs', 'http://docs.test/agents/docs/run', token='test-token',
        transport=httpx.ASGITransport(app=create_app(hosted)))
    config = settings.Settings(mode='local', routing_mode='keyword', answer_mode='evidence')
    return docs, pipeline, config, schemas


@pytest.mark.parametrize('retrieval_only', [False, True])
def test_actual_http_adapter(monkeypatch, retrieval_only):
    docs, _, _, schemas = reference(monkeypatch, retrieval_only)
    finding = asyncio.run(docs.run(CASES[0]['question'], {
        'request_id': 'integration-1', 'intent': 'how_to', 'incident_id': 'INC-1',
        'conversation_id': 'chat-1', 'evidence_bundle': {}}))
    assert isinstance(finding, schemas.AgentFinding)
    assert finding.status == 'ok' and finding.agent == 'docs'
    assert ('Retrieval-only' if retrieval_only else 'JDK 21') in finding.summary
    assert all(item.agent == 'docs' for item in finding.evidence)


@pytest.mark.parametrize('retrieval_only', [False, True])
def test_actual_api_ask(monkeypatch, retrieval_only):
    docs, pipeline, settings, _ = reference(monkeypatch, retrieval_only)
    monkeypatch.setattr(pipeline, 'AGENTS', {'docs': docs})
    monkeypatch.setattr(pipeline, 'SETTINGS', settings)
    api = importlib.import_module('main')
    with TestClient(api.app) as client:
        response = client.post('/api/ask', json={'question': CASES[0]['question'],
            'routing_mode': 'keyword', 'answer_mode': 'evidence', 'conversation_id': 'integration-chat'})
    assert response.status_code == 200
    result = response.json()
    assert result['agents_used'] == ['docs']
    assert result['demo'] is False and result['partial'] is False
    assert result['conversation_id'] == 'integration-chat'
    assert result['findings'][0]['status'] == 'ok'
    assert ('Retrieval-only' if retrieval_only else 'JDK 21') in result['findings'][0]['summary']
    assert result['evidence']


@pytest.mark.parametrize('retrieval_only', [False, True])
def test_docs_evidence_reaches_code_stage(monkeypatch, retrieval_only):
    docs, pipeline, settings, schemas = reference(monkeypatch, retrieval_only)
    received = []
    class CodeProbe:
        async def run(self, question, context):
            received.append(context['evidence_bundle'])
            return schemas.AgentFinding(agent='code', status='no_evidence', summary='Test probe only')
    result = asyncio.run(pipeline.investigate(
        'Where is the Ad Service build configuration in the code?',
        agents={'docs': docs, 'code': CodeProbe()}, settings=settings))
    assert result.agents_used == ['docs', 'code']
    assert received and received[0]['agent_statuses']['docs'] == 'ok'
    assert received[0]['evidence'] and received[0]['evidence'][0]['agent'] == 'docs'
    assert ('Retrieval-only' if retrieval_only else 'JDK 21') in received[0]['summaries']['docs']
