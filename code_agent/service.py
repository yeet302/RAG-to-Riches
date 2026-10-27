"""Independent Code Agent HTTP service for the orchestrator wire contract.

From repository root:
  uvicorn code_agent.service:app --host 127.0.0.1 --port 8103

Configure CODE_AGENT_TOKEN on both this service and the orchestrator.
"""
import hmac
import logging
import os
from fastapi import FastAPI, Header, HTTPException, Response
from typing import Optional
from .contracts import AgentFinding, AgentRequest
from agent_contracts import PipelineError

log = logging.getLogger('code_agent.service')


def create_app(agent=None):
    app = FastAPI(title='RAG-to-Riches Code Agent', version='1.0')
    app.state.agent = agent  # Lazily construct Foundry client on first real request.

    @app.get('/health')
    async def health():
        return {'status': 'ok', 'agent': 'code'}  # Liveness, not Azure readiness.

    async def execute(request: AgentRequest, response: Response,
                      authorization: Optional[str]):
        token = os.environ.get('CODE_AGENT_TOKEN', '')
        if token and not hmac.compare_digest(
                (authorization or '').encode(), f'Bearer {token}'.encode()):
            raise HTTPException(401, 'Invalid agent credential')
        response.headers["X-Request-ID"] = request.request_id
        if app.state.agent is None:
            try:
                from .agent import CodeAgent
                app.state.agent = CodeAgent()
            except Exception:
                log.exception('Code Agent initialization failed for request %s', request.request_id)
                return AgentFinding(status='error', summary='Code Agent is not configured.',
                                    sources_unavailable=['Azure Foundry or AI Search configuration'], error=PipelineError(
                                        code='CODE_NOT_CONFIGURED', agent='code', stage='dispatch',
                                        message='The code agent could not initialize.',
                                        resolution='Check Foundry project, deployment, and Search settings in code_agent/.env.'))
        context = {
            'request_id': request.request_id, 'original_question': request.original_question or request.question,
            'task': request.task, 'intent': request.intent, 'incident_id': request.incident_id,
            'conversation_id': request.conversation_id, 'evidence_bundle': request.evidence_bundle,
        }
        response.headers['X-Request-ID'] = request.request_id
        return await app.state.agent.run(request.question, context)

    @app.post('/run', response_model=AgentFinding)
    @app.post('/agents/code/run', response_model=AgentFinding)
    async def run(request: AgentRequest, response: Response,
                  authorization: Optional[str] = Header(default=None)):
        return await execute(request, response, authorization)

    return app


app = create_app()
