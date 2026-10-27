"""Keep long logs investigations alive without relaxing other agents' deadlines."""
import asyncio
import httpx
import pytest
from agents import build_agents
from orchestrator import investigate
from settings import Settings


def config(**changes):
    values=dict(timeout_seconds=.01, agent_timeouts={'logs':.5}, routing_mode='keyword', answer_mode='evidence',
                agent_urls={name:f'https://{name}.invalid/run' for name in ('logs','docs','code')})
    values.update(changes)
    return Settings(**values)


def test_logs_response_can_outlive_the_general_deadline_at_both_layers():
    settings=config()
    registry=build_agents(settings)
    async def respond(request):
        assert request.extensions['timeout']['read']==.5
        await asyncio.sleep(.04)  # Longer than the general deadline.
        return httpx.Response(200,json={'agent':'logs','summary':'Retrieved events',
            'evidence':[{'source_type':'log','summary':'Observed error','ref':'events:1'}]})
    registry['logs'].transport=httpx.MockTransport(respond)
    result=asyncio.run(investigate('Show Ad Service logs',settings=settings,agents=registry))
    assert result.agents_used==['logs']
    assert result.findings[0].status=='ok' and result.evidence
    assert not result.errors


def test_docs_keeps_its_original_deadline_when_logs_is_extended():
    settings=config()
    registry=build_agents(settings)
    async def slow(request):
        assert request.extensions['timeout']['read']==.01
        await asyncio.sleep(.04)
        return httpx.Response(200,json={'agent':'docs','summary':'No matches','status':'no_evidence'})
    registry['docs'].transport=httpx.MockTransport(slow)
    result=asyncio.run(investigate('How do I build the service?',settings=settings,agents=registry))
    assert result.errors[0].code=='AGENT_TIMEOUT' and result.errors[0].agent=='docs'


def test_logs_override_remains_a_bounded_deadline():
    settings=config(timeout_seconds=.5,agent_timeouts={'logs':.01})
    registry=build_agents(settings)
    async def slow(request):
        await asyncio.sleep(.04)
        return httpx.Response(200,json={'agent':'logs','summary':'No matches','status':'no_evidence'})
    registry['logs'].transport=httpx.MockTransport(slow)
    result=asyncio.run(investigate('Show Ad Service logs',settings=settings,agents=registry))
    assert result.errors[0].code=='AGENT_TIMEOUT'
    assert '0.01 seconds' in result.errors[0].message


def test_long_read_budget_keeps_connection_timeout_short():
    settings=config(timeout_seconds=60,agent_timeouts={'logs':180})
    agent=build_agents(settings)['logs']
    def respond(request):
        assert request.extensions['timeout']=={'connect':10.0,'read':180,'write':180,'pool':10.0}
        return httpx.Response(200,json={'agent':'logs','status':'no_evidence','summary':'No matches'})
    agent.transport=httpx.MockTransport(respond)
    finding=asyncio.run(agent.run('Find logs',{'request_id':'timeout-test','intent':'logs_lookup'}))
    assert finding.status=='no_evidence'


@pytest.mark.parametrize('overrides',[{'logs':0},{'logs':-1},{'logs':float('nan')},{'logs':float('inf')},{'other':1}])
def test_invalid_role_deadlines_are_rejected(overrides):
    with pytest.raises(ValueError,match='timeout overrides'):
        config(agent_timeouts=overrides)


def test_role_timeout_environment_overrides_fall_back_to_general_deadline(monkeypatch):
    monkeypatch.setenv('AGENT_TIMEOUT_SECONDS','60')
    monkeypatch.setenv('LOGS_AGENT_TIMEOUT_SECONDS','180')
    monkeypatch.delenv('DOCS_AGENT_TIMEOUT_SECONDS',raising=False)
    monkeypatch.delenv('CODE_AGENT_TIMEOUT_SECONDS',raising=False)
    settings=Settings.from_env()
    assert settings.timeout_for('logs')==180
    assert settings.timeout_for('docs')==settings.timeout_for('code')==60
