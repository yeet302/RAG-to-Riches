import asyncio
import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from code_agent.service import create_app
from code_agent.contracts import AgentFinding, Evidence


class FakeCodeAgent:
    def __init__(self):
        self.received = None

    async def run(self, question, context):
        self.received = (question, context)
        return AgentFinding(status='ok', summary='Source path correlated to upstream evidence.',
            evidence=[Evidence(source_type='code', agent='code', summary='Verified code path',
                               ref='file.java@abcdef:L10-L15')],
            sources_searched=['GitHub repository'], confidence=0.7)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCodeAgent()
        self.client = TestClient(create_app(self.fake))
        self.request = {
            'schema_version':'1.0', 'request_id':'incident-001',
            'question':'Investigate GetAds UNAVAILABLE',
            'original_question':'Why are GetAds requests failing?',
            'task':'Correlate the source implementation with logs and docs',
            'intent':'incident', 'incident_id':'INC-001',
            'evidence_bundle': {
                'summaries': {'logs':'GetAds gRPC UNAVAILABLE', 'docs':'adFailure may cause simulated errors'},
                'evidence': [
                    {'source_type':'log','agent':'logs','summary':'UNAVAILABLE on GetAds','ref':'log-1'},
                    {'source_type':'doc','agent':'docs','summary':'adFailure test flag','ref':'doc-1'}],
                'agent_statuses':{'logs':'ok','docs':'ok'}},
        }

    def test_receives_upstream_context_and_returns_finding(self):
        with patch.dict(os.environ, {'CODE_AGENT_TOKEN':'test-token'}):
            res = self.client.post('/agents/code/run', json=self.request,
                headers={'Authorization':'Bearer test-token'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers['x-request-id'], 'incident-001')
        self.assertEqual(res.json()['agent'], 'code')
        self.assertEqual(res.json()['evidence'][0]['source_type'], 'code')
        self.assertEqual(self.fake.received[1]['evidence_bundle']['summaries']['logs'], 'GetAds gRPC UNAVAILABLE')
        self.assertEqual(self.fake.received[1]['evidence_bundle']['evidence'][1]['ref'], 'doc-1')

    def test_rejects_bad_token(self):
        with patch.dict(os.environ, {'CODE_AGENT_TOKEN':'test-token'}):
            res = self.client.post('/run', json=self.request, headers={'Authorization':'Bearer wrong'})
        self.assertEqual(res.status_code, 401)

    def test_invalid_request_fails_validation(self):
        bad = {**self.request, 'schema_version':'2.0'}
        res = self.client.post('/run', json=bad)
        self.assertEqual(res.status_code, 422)

if __name__ == '__main__':
    unittest.main()
