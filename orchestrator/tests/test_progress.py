"""Live progress and terminal stream behavior; no external services."""
import asyncio
import json
from fastapi.testclient import TestClient
import main
import orchestrator
from schemas import AgentFinding, CopilotResponse
from settings import Settings


def test_parallel_agent_progress_before_stage_finishes():
    async def scenario():
        release, finished = asyncio.Event(), asyncio.Event()
        events = []

        class Specialist:
            def __init__(self, name):
                self.name = name

            async def run(self, question, context):
                if self.name == 'logs':
                    await release.wait()
                return AgentFinding(agent=self.name, status='no_evidence', summary='No matches')

        def progress(event):
            events.append(event)
            if event.get('agent') == 'docs' and event.get('status') == 'no_evidence':
                finished.set()

        task = asyncio.create_task(orchestrator.investigate(
            'Why is checkout returning timeout errors?',
            agents={name: Specialist(name) for name in ('logs', 'docs', 'code')},
            settings=Settings(timeout_seconds=2), on_progress=progress))
        try:
            await asyncio.wait_for(finished.wait(), timeout=1)
            assert not task.done()
            assert not any(e.get('agent') == 'code' for e in events)
        finally:
            release.set()
        response = await task
        assert events[0] == {'phase': 'planning'}
        assert events[-1] == {'phase': 'synthesis'}
        assert response.agents_used == ['logs', 'docs', 'code']
    asyncio.run(scenario())


def test_stream_result(monkeypatch):
    async def investigate(question, **kwargs):
        kwargs['on_progress']({'phase': 'planning'})
        return CopilotResponse(kind='answer', question=question, answer='Test answer', demo=False)
    monkeypatch.setattr(main, 'investigate', investigate)
    with TestClient(main.app) as client:
        response = client.post('/api/ask/stream', json={'question': 'Test question'})
    events = [json.loads(line) for line in response.text.splitlines()]
    assert response.status_code == 200
    assert events[0] == {'type': 'progress', 'phase': 'planning'}
    assert events[-1]['type'] == 'result'
    assert events[-1]['response']['accuracy_recorded'] is True


def test_stream_error_and_validation(monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError('private provider details')
    monkeypatch.setattr(main, 'investigate', fail)
    with TestClient(main.app) as client:
        response = client.post('/api/ask/stream', json={'question': 'Test question'})
        assert client.post('/api/ask/stream', json={'question': ' '}).status_code == 400
    assert json.loads(response.text)['type'] == 'error'
    assert 'private provider' not in response.text
