"""Compatibility of the common contract with both actual specialist HTTP hosts."""
from pathlib import Path
from types import SimpleNamespace
import importlib

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent_contracts import AgentRequest, AgentFinding
from schemas import AgentRequest as OrchestratorRequest
from docs_agent.contracts import AgentRequest as DocsRequest, AgentFinding as DocsFinding
from docs_agent.contracts import Evidence as DocsEvidence
from docs_agent.service import create_app


def message():
    return dict(request_id='shared-1', question='Find Ad Service errors',
                original_question='Why is Ad Service failing?', task='Find exact sources',
                intent='incident', conversation_id='chat-1', evidence_bundle={})


def test_request_model_is_shared_with_legacy_defaults():
    assert OrchestratorRequest is DocsRequest is AgentRequest
    legacy = message()
    legacy.pop('original_question')
    parsed = AgentRequest.model_validate({**legacy, 'future_metadata': 'ignored'})
    assert parsed.original_question is None
    assert parsed.schema_version == '1.0'


@pytest.mark.parametrize('field', ['request_id', 'question', 'task', 'original_question'])
def test_blank_required_text_is_rejected(field):
    with pytest.raises(ValidationError):
        AgentRequest.model_validate({**message(), field: ' '})


def test_docs_defaults_remain_compatible_with_orchestrator():
    finding = DocsFinding(summary='Documentation found', evidence=[
        DocsEvidence(summary='Ad Service documentation', ref='README.md:L1')])
    parsed = AgentFinding.model_validate_json(finding.model_dump_json())
    assert parsed.agent == parsed.evidence[0].agent == 'docs'
    assert parsed.evidence[0].source_type == 'doc'


@pytest.mark.parametrize('name', ['docs', 'logs'])
def test_actual_service_preserves_delegation_fields(name, monkeypatch):
    received = []
    if name == 'docs':
        class Probe:
            settings = SimpleNamespace(token='', configured=False)

            async def run(self, request):
                received.append(request)
                return DocsFinding(status='no_evidence', summary='Probe')
        app = create_app(Probe())
    else:
        monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'logs_agent'))
        service = importlib.import_module('orchestrator_service')
        contract = importlib.import_module('orchestrator_contract')
        assert contract.AgentRequest is AgentRequest
        assert contract.AgentFinding is AgentFinding
        monkeypatch.delenv('LOGS_AGENT_TOKEN', raising=False)
        def handle(request):
            received.append(request)
            return AgentFinding(agent='logs', status='no_evidence', summary='Probe')
        monkeypatch.setattr(service, 'handle_agent_request', handle)
        app = service.app
    with TestClient(app) as client:
        response = client.post(f'/agents/{name}/run', json=message())
    assert response.status_code == 200
    assert AgentFinding.model_validate(response.json()).agent == name
    assert len(received) == 1
    assert received[0].model_dump() == AgentRequest(**message()).model_dump()
