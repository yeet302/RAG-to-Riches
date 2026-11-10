import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import main
import suggestions
from settings import Settings
from suggestions import SuggestionContext, followup_questions


def test_followups_use_orchestrator_context_and_remove_repeats(monkeypatch):
    captured = {}
    async def complete(settings, system, payload, **kwargs):
        captured.update(json.loads(payload))
        assert 'untrusted data' in system
        return json.dumps({'questions': [
            'Where is GetAds defined?', 'Where is GetAds defined?',
            'What documentation explains Ad Service GetAds errors?', 'What logs exist for Ad Service GetAds?']})
    monkeypatch.setattr(suggestions, 'complete_json', complete)
    context = SuggestionContext(question='Where is GetAds defined?', answer='Found implementation.',
                                sources_unavailable=['Logs unavailable'])
    result = asyncio.run(followup_questions(context, Settings(
        llm_endpoint='https://example.invalid', llm_model='model', llm_api_key='test')))
    assert result == {'source': 'llm', 'questions': [
        'What documentation explains Ad Service GetAds errors?', 'What logs exist for Ad Service GetAds?']}
    assert captured['answer'] == 'Found implementation.'
    assert captured['sources_unavailable'] == ['Logs unavailable']


@pytest.mark.parametrize('output', ['not JSON', '{"questions": []}', '{"questions": ["short"]}'])
def test_invalid_model_questions_do_not_break_answer_flow(monkeypatch, output):
    async def complete(*args, **kwargs):
        return output
    monkeypatch.setattr(suggestions, 'complete_json', complete)
    result = asyncio.run(followup_questions(SuggestionContext(question='Investigate Ad Service', answer='No evidence'),
        Settings(llm_endpoint='https://example.invalid', llm_model='model', llm_api_key='test')))
    assert result == {'source': 'unavailable', 'questions': []}


def test_endpoint_without_model_and_bounded_input(monkeypatch):
    monkeypatch.setattr(main, 'SETTINGS', Settings())
    with TestClient(main.app) as client:
        response = client.post('/api/suggestions', json={'question': 'GetAds errors?', 'answer': 'No evidence'})
        assert response.json() == {'questions': [], 'source': 'unavailable'}
        assert client.post('/api/suggestions', json={'question': 'GetAds errors?', 'answer': 'x' * 6001}).status_code == 422
