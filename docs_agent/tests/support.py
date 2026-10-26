"""Offline fixtures, not model-quality evaluation or canned production answers."""
import json
from pathlib import Path

import httpx

from docs_agent.config import Settings
from docs_agent.main import DocsAgent

FIXTURE = Path(__file__).parent / 'fixtures' / 'ad_README.md'
REF = '5c8d404a85380226ff45a39dcb4159eda9552c56'
CASES = [
    {'id': 'build', 'question': 'How do I build the Ad Service locally?',
     'expected': ['JDK 21', './gradlew installDist'], 'status': 'ok'},
    {'id': 'random', 'question': 'What does the Ad Service return without context keys?',
     'expected': ['random ads'], 'status': 'ok'},
    {'id': 'unsupported', 'question': 'How do I configure PostgreSQL replication for the Ad Service?',
     'expected': [], 'status': 'no_evidence'},
]


def docs_response(request):
    assert request.url.params['ref'] == REF
    return httpx.Response(200, text=FIXTURE.read_text())


def model_response(request):
    body = json.loads(request.content)
    assert body['response_format'] == {'type': 'json_object'}
    assert request.headers['api-key'] == 'test-key'
    payload = json.loads(body['messages'][1]['content'])
    question, sources = payload['question'], payload['sources']
    if 'build' in question.lower():
        text, needles = 'Use at least JDK 21 and run ./gradlew installDist to build the Ad Service.', ['JDK 21', './gradlew installDist']
    elif 'context keys' in question.lower():
        text, needles = 'Without context keys, the Ad Service returns random ads.', ['random ads']
    else:
        return model_json({'status': 'no_evidence', 'claims': [], 'hypotheses': []})
    citations = []
    for needle in needles:
        match = next((source['id'] for source in sources if needle in source['excerpt']), None)
        assert match is not None, f'Retrieval missed expected supporting text: {needle}'
        citations.append(match)
    return model_json({'status': 'ok', 'claims': [{'text': text, 'citations': list(dict.fromkeys(citations))}], 'hypotheses': []})


def model_json(answer):
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}]})


def offline_agent(**overrides):
    settings = Settings(endpoint='https://example.invalid', model='test-model', api_key='test-key',
                        paths=('src/ad/README.md',), ref=REF, github_roots=(), website_seeds=(), **overrides)
    return DocsAgent(settings, docs_transport=httpx.MockTransport(docs_response),
                     llm_transport=httpx.MockTransport(model_response))
