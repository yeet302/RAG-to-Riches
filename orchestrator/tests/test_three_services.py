"""Real API/HTTP adapters and all specialist hosts; external models/sources mocked."""
import importlib
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import main
import orchestrator
import query_analysis
import answer_synthesis
import azure_client
from agents import HTTPAgent, build_agents
from settings import Settings
from code_agent.service import create_app as code_app
from code_agent.contracts import AgentFinding as CodeFinding, Evidence
from docs_agent.service import create_app as docs_app
from docs_agent.tests.support import offline_agent


@pytest.mark.parametrize('authenticated', [True, False])
@pytest.mark.parametrize('logs_available', [True, False])
def test_frontend_request_through_three_actual_service_hosts(monkeypatch, logs_available, authenticated):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'logs_agent'))
    logs = importlib.import_module('orchestrator_service')
    foundry = importlib.import_module('foundry_agent')
    results = importlib.import_module('investigation_result')
    seen = {}
    token = 'integration-token' if authenticated else ''
    for key in ('LOGS_AGENT_TOKEN', 'DOCS_AGENT_TOKEN', 'CODE_AGENT_TOKEN'):
        monkeypatch.setenv(key, token)
    monkeypatch.setenv('LOG_CONNECTOR', 'local')
    monkeypatch.setenv('FOUNDRY_PROJECT_ENDPOINT', 'https://example.invalid/api/projects/test')
    monkeypatch.setenv('FOUNDRY_AGENT_NAME', 'test')

    def investigate(prompt):
        seen['logs'] = json.loads(prompt)
        return results.LogInvestigationResult(
            status=results.InvestigationStatus.EVIDENCE_FOUND,
            summary='Ad Service build failed', confidence=0.6,
            evidence=[results.EvidenceItem(service='ad', severity='ERROR',
                message='build failed', timestamp='2026-10-17T12:00:00Z')])
    monkeypatch.setattr(foundry, 'investigate_logs', investigate)
    if not logs_available:
        adapter = importlib.import_module('orchestrator_adapter')
        monkeypatch.setattr(adapter, 'preflight_finding', lambda: adapter.AgentFinding(
            agent='logs', status='no_access', summary='Logs unavailable',
            sources_unavailable=['Azure Log Analytics']))

    class CodeProbe:
        async def run(self, question, context):
            seen['code'] = {'question': question, **context}
            return CodeFinding(summary='Build configuration found', evidence=[
                Evidence(agent='code', source_type='code', summary='JDK 21 required',
                         ref='build.gradle:L1')])

    docs = offline_agent(token=token)
    original_model_response = docs.llm_transport
    async def docs_model(request):
        seen['docs'] = json.loads(json.loads(request.content)['messages'][1]['content'])
        return await original_model_response.handle_async_request(request)
    docs.llm_transport = httpx.MockTransport(docs_model)
    apps = {'logs': logs.app, 'docs': docs_app(docs), 'code': code_app(CodeProbe())}
    config = Settings(mode='remote', llm_endpoint='https://azure.invalid/openai/v1',
        llm_model='test', llm_api_key='test-key',
        agent_urls={name: f'http://{name}.test/agents/{name}/run' for name in apps},
        agent_tokens={name: token for name in apps} if authenticated else {})
    class CheckedTransport(httpx.AsyncBaseTransport):
        def __init__(self, app):
            self.inner = httpx.ASGITransport(app=app)

        async def handle_async_request(self, request):
            if authenticated:
                assert request.headers['authorization'] == f'Bearer {token}'
            else:
                assert 'authorization' not in request.headers
                assert 'api-key' not in request.headers
            return await self.inner.handle_async_request(request)

    registry = build_agents(config)
    for name, agent in registry.items():
        assert isinstance(agent, HTTPAgent)
        agent.transport = CheckedTransport(apps[name])
    question = 'Why is the Ad Service build failing?'
    plan = dict(intent='incident', summary='Investigate build failure', reason='Correlate sources',
        assignments=[dict(agent=name, stage=2 if name == 'code' else 1,
            question='How do I build the Ad Service locally?' if name == 'docs' else 'Find Ad Service build failures',
            task=f'Inspect {name} evidence and cite exact sources') for name in apps])
    model_inputs = []
    def model_response(request):
        content = json.loads(request.content)['messages'][1]['content']
        model_inputs.append(content)
        if len(model_inputs) == 1:
            assert content == question
            result = plan
        else:
            payload = json.loads(content)
            assert payload['agent_statuses'] == {'logs': 'ok' if logs_available else 'no_access', 'docs': 'ok', 'code': 'ok'}
            expected_agents = {'docs', 'code'} | ({'logs'} if logs_available else set())
            assert {f['agent'] for f in payload['findings']} == expected_agents
            assert next(f for f in payload['findings'] if f['agent'] == 'code')['summary'] == 'Build configuration found'
            evidence_ids = {e['id'] for e in payload['evidence']}
            assert all(f['evidence_ids'] and set(f['evidence_ids']) <= evidence_ids for f in payload['findings'])
            ids = [e['id'] for e in payload['evidence']]
            result = {'answer': 'Build evidence: ' + ' '.join(f'[{i}]' for i in ids), 'evidence_ids': ids}
        return httpx.Response(200, json={'choices':[{'finish_reason':'stop', 'message':{'content':json.dumps(result)}}]})
    async def complete(settings, prompt, content, **kwargs):
        return await azure_client.complete_json(settings, prompt, content,
            timeout_seconds=kwargs['timeout_seconds'], transport=httpx.MockTransport(model_response))
    monkeypatch.setattr(query_analysis, 'complete_json', complete)
    monkeypatch.setattr(answer_synthesis, 'complete_json', complete)
    monkeypatch.setattr(orchestrator, 'SETTINGS', config)
    monkeypatch.setattr(orchestrator, 'AGENTS', registry)
    with TestClient(main.app) as client:
        response = client.post('/api/ask', json={'question':question,
            'routing_mode':'llm', 'answer_mode':'llm', 'conversation_id':'three-services'})
    assert response.status_code == 200
    body = response.json()
    assert body['analysis']['source'] == body['synthesis']['source'] == 'llm'
    assert body['agents_used'] == ['logs', 'docs', 'code']
    assert body['partial'] is (not logs_available)
    assert len(model_inputs) == 2
    assert seen['docs']['original_question'] == question
    assert seen['docs']['task'] == plan['assignments'][1]['task']
    if logs_available:
        assert seen['logs']['original_question'] == question
        assert seen['logs']['task'] == plan['assignments'][0]['task']
    bundle = seen['code']['evidence_bundle']
    assert bundle['agent_statuses']['logs'] == ('ok' if logs_available else 'no_access')
    assert {e['agent'] for e in bundle['evidence']} == ({'logs','docs'} if logs_available else {'docs'})
    assert seen['code']['request_id'] == body['request_id']
    assert seen['code']['conversation_id'] == 'three-services'
    assert body['synthesis']['citations']
    assert 'integration-token' not in response.text



def test_docs_permission_denial_reaches_code_as_gap_without_failed_evidence(monkeypatch):
    received = {}
    class CodeProbe:
        async def run(self, question, context):
            received.update(question=question, **context)
            return CodeFinding(status='no_evidence', summary='No verified code evidence returned')

    docs = offline_agent()
    docs.llm_transport = httpx.MockTransport(lambda request: httpx.Response(
        403, text='private provider body'))
    monkeypatch.delenv('CODE_AGENT_TOKEN', raising=False)
    config = Settings(mode='remote', routing_mode='keyword', answer_mode='evidence',
                      agent_urls={'docs':'http://docs.test/agents/docs/run',
                                  'code':'http://code.test/agents/code/run'})
    registry = build_agents(config)
    registry['docs'].transport = httpx.ASGITransport(app=docs_app(docs))
    registry['code'].transport = httpx.ASGITransport(app=code_app(CodeProbe()))
    monkeypatch.setattr(orchestrator, 'SETTINGS', config)
    monkeypatch.setattr(orchestrator, 'AGENTS', registry)
    with TestClient(main.app) as client:
        response = client.post('/api/ask', json={
            'question':'Where is the Ad Service build function defined?',
            'conversation_id':'denied-docs-handoff'})
    assert response.status_code == 200
    result = response.json()
    assert result['agents_used'] == ['docs', 'code']
    assert result['findings'][0]['status'] == 'no_access'
    assert result['findings'][0]['evidence']
    bundle = received['evidence_bundle']
    assert bundle['agent_statuses'] == {'docs':'no_access'}
    assert bundle['summaries'] == {} and bundle['evidence'] == []
    assert 'Docs Azure model access denied (HTTP 403)' in bundle['sources_unavailable']
    assert bundle['errors'][0]['code'] == 'DOCS_MODEL_ACCESS_DENIED'
    assert result['errors'][0]['code'] == 'DOCS_MODEL_ACCESS_DENIED'
    assert result['pipeline_status'] == 'partial'
    assert received['request_id'] == result['request_id']
    assert received['conversation_id'] == 'denied-docs-handoff'
    assert not result['evidence'] and result['partial']
    assert 'private provider body' not in response.text
