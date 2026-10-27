"""User-visible codes survive HTTP handoffs without losing partial evidence."""
import asyncio
import httpx
import pytest
from fastapi.testclient import TestClient
import main
import orchestrator
from agents import HTTPAgent, build_agents
from schemas import AgentFinding, Evidence, PipelineError
from settings import Settings


@pytest.mark.parametrize('case,expected,http_status', [
    ('denied','AGENT_ACCESS_DENIED',403),
    ('server','AGENT_HTTP_ERROR',503),
    ('unreachable','AGENT_UNREACHABLE',None),
    ('timeout','AGENT_TIMEOUT',None),
    ('http_timeout','AGENT_TIMEOUT',None),
    ('invalid','AGENT_INVALID_RESPONSE',None),
])
def test_failed_dependency_returns_actionable_code_through_api(monkeypatch, case, expected, http_status):
    async def respond(request):
        if case == 'denied':return httpx.Response(403, text='private-provider-body')
        if case == 'server':return httpx.Response(503, text='private-provider-body')
        if case == 'unreachable':raise httpx.ConnectError('private-provider-body', request=request)
        if case == 'http_timeout':raise httpx.ReadTimeout('private-provider-body', request=request)
        if case == 'timeout':
            await asyncio.sleep(1)
        return httpx.Response(200, text='private-provider-body')
    config = Settings(routing_mode='keyword', answer_mode='evidence', timeout_seconds=.05)
    monkeypatch.setattr(orchestrator,'SETTINGS',config)
    monkeypatch.setattr(orchestrator,'AGENTS',{'docs':HTTPAgent('docs','https://private.invalid/run',
        transport=httpx.MockTransport(respond))})
    with TestClient(main.app) as client:
        response=client.post('/api/ask',json={'question':'How do I build the service?'})
    assert response.status_code == 200
    result=response.json()
    assert result['pipeline_status'] == 'failed'
    assert result['partial'] and not result['evidence']
    error=result['errors'][0]
    assert error['code']==expected and error['agent']=='docs'
    assert error['http_status']==http_status
    assert error['message'] and error['resolution']
    assert result['findings'][0]['error']==error
    assert 'private-provider-body' not in response.text
    assert 'private.invalid' not in response.text


def test_missing_endpoint_is_configuration_error():
    config=Settings(routing_mode='keyword',answer_mode='evidence')
    result=asyncio.run(orchestrator.investigate('How do I build the service?',settings=config,agents=build_agents(config)))
    assert result.pipeline_status=='failed'
    assert result.errors[0].code=='AGENT_NOT_CONFIGURED'
    assert 'DOCS_AGENT_URL' in result.errors[0].resolution


@pytest.mark.parametrize('has_evidence', [True,False])
def test_success_and_empty_search_are_distinct_from_errors(has_evidence):
    class Docs:
        async def run(self,question,context):
            return AgentFinding(agent='docs',status='ok' if has_evidence else 'no_evidence',summary='Search complete',
                evidence=[Evidence(source_type='doc',summary='Retrieved fact',ref='docs:1')] if has_evidence else [])
    result=asyncio.run(orchestrator.investigate('How do I build the service?',
        agents={'docs':Docs()},settings=Settings(routing_mode='keyword',answer_mode='evidence')))
    assert result.pipeline_status==('ok' if has_evidence else 'no_evidence')
    assert result.errors==[] and not result.partial


def test_specialist_error_is_preserved_with_partial_evidence():
    class Specialist:
        def __init__(self,name):self.name=name
        async def run(self,question,context):
            if self.name=='docs':
                return AgentFinding(agent='docs',status='no_access',summary='Denied',error=PipelineError(
                    code='DOCS_MODEL_ACCESS_DENIED',agent='docs',stage='model',http_status=401,
                    message='Azure denied inference.',resolution='Ask the resource owner to grant inference access.'))
            return AgentFinding(agent=self.name,summary='Verified event',evidence=[
                Evidence(source_type='log' if self.name=='logs' else 'code',summary='Retrieved evidence',ref=self.name+':1')])
    result=asyncio.run(orchestrator.investigate('Why is checkout failing?',
        agents={name:Specialist(name) for name in ('logs','docs','code')},
        settings=Settings(routing_mode='keyword',answer_mode='evidence')))
    assert result.pipeline_status=='partial' and result.evidence
    assert result.errors[0].code=='DOCS_MODEL_ACCESS_DENIED'
    assert result.errors[0].http_status==401


def test_wrong_error_identity_is_rejected():
    class Docs:
        async def run(self,question,context):
            return AgentFinding(agent='docs',status='no_access',summary='Denied',error=PipelineError(
                code='AGENT_ACCESS_DENIED',agent='code',stage='model',message='Denied',resolution='Check access'))
    result=asyncio.run(orchestrator.investigate('How do I build the service?',agents={'docs':Docs()},
        settings=Settings(routing_mode='keyword',answer_mode='evidence')))
    assert result.errors[0].code=='AGENT_INVALID_RESPONSE'
    assert result.errors[0].agent=='docs'



def test_model_fallbacks_have_codes_and_keep_successful_evidence(monkeypatch):
    import query_analysis
    import answer_synthesis
    async def planning_failed(*args, **kwargs):
        raise RuntimeError('private-provider-body')
    async def synthesis_failed(*args, **kwargs):
        return '{"unexpected":"private-provider-body"}'
    class Docs:
        async def run(self,question,context):
            return AgentFinding(agent='docs',summary='Verified source',evidence=[
                Evidence(source_type='doc',summary='Retrieved fact',ref='docs:1')])
    monkeypatch.setattr(query_analysis,'_model_analysis',planning_failed)
    monkeypatch.setattr(answer_synthesis,'complete_json',synthesis_failed)
    config=Settings(llm_endpoint='https://example.invalid',llm_model='deployment',llm_api_key='test-key')
    result=asyncio.run(orchestrator.investigate('How do I build the service?',agents={'docs':Docs()},settings=config))
    assert result.pipeline_status=='partial' and result.partial and result.evidence
    assert [error.code for error in result.errors]==['PLANNING_FALLBACK','SYNTHESIS_FAILED']
    assert 'private-provider-body' not in result.model_dump_json()
