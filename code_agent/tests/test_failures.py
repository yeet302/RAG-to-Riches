"""Exercise error handoff JSON without creating or calling cloud agents."""
from types import SimpleNamespace

import httpx
import pytest
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from fastapi.testclient import TestClient

from code_agent.agent import CodeAgent
from code_agent.service import create_app


@pytest.mark.parametrize('host,source', [
    ('foundry.invalid', 'Azure Foundry'),
    ('search.invalid', 'Azure AI Search'),
    ('api.github.com', 'GitHub'),
])
@pytest.mark.parametrize('status', [401, 403, 429])
def test_source_failure_is_classified_in_handoff(monkeypatch, caplog, host, source, status):
    agent = CodeAgent.__new__(CodeAgent)
    agent.settings = SimpleNamespace(project_endpoint='https://foundry.invalid/api/projects/project',
                                     search_endpoint='https://search.invalid')
    def fail(*args):
        if source == 'GitHub':
            response = httpx.Response(status, request=httpx.Request('GET', 'https://' + host + '/private'))
            response.raise_for_status()
        response = SimpleNamespace(status_code=status, reason='Denied', headers={},
                                   request=SimpleNamespace(url='https://' + host + '/private'))
        raise HttpResponseError(message='private-secret-provider-body', response=response)
    monkeypatch.setattr(agent, '_investigate', fail)
    monkeypatch.delenv('CODE_AGENT_TOKEN', raising=False)
    with TestClient(create_app(agent)) as client:
        response = client.post('/agents/code/run', json={
            'request_id': 'permission-check', 'question': 'Find GetAds implementation',
            'task': 'Inspect source and cite exact lines', 'intent': 'code_lookup'})
    assert response.status_code == 200
    finding = response.json()
    assert finding['status'] == ('no_access' if status in (401, 403) else 'error')
    assert finding['evidence'] == []
    prefix = {'Azure Foundry':'CODE_FOUNDRY', 'Azure AI Search':'CODE_SEARCH', 'GitHub':'CODE_GITHUB'}[source]
    assert finding['error']['code'] == prefix + ('_ACCESS_DENIED' if status in (401,403) else '_FAILED')
    assert finding['error']['http_status'] == status
    assert finding['error']['resolution']
    assert source in finding['sources_unavailable'][0]
    assert str(status) in finding['sources_unavailable'][0]
    assert 'private-secret-provider-body' not in response.text + caplog.text
    assert host not in response.text + caplog.text
    assert response.headers['X-Request-ID'] == 'permission-check'


def test_identity_failure_is_not_reported_as_bad_model_json(monkeypatch):
    agent = CodeAgent.__new__(CodeAgent)
    agent.settings = SimpleNamespace(project_endpoint='https://foundry.invalid', search_endpoint='https://search.invalid')
    def fail(*args):
        raise ClientAuthenticationError(message='private credential chain details')
    monkeypatch.setattr(agent, '_investigate', fail)
    monkeypatch.delenv('CODE_AGENT_TOKEN', raising=False)
    with TestClient(create_app(agent)) as client:
        response = client.post('/agents/code/run', json={
            'request_id': 'identity-check', 'question': 'Find source', 'task': 'Read source', 'intent': 'code_lookup'})
    assert response.json()['status'] == 'no_access'
    assert response.json()['sources_unavailable'] == ['Azure identity access denied']
    assert 'private credential chain details' not in response.text
