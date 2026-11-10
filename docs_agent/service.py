"""HTTP contract consumed by the orchestrator's HTTPAgent adapter."""
import hmac
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Response

from .contracts import AgentFinding, AgentRequest
from .main import DocsAgent


def create_app(agent=None):
    agent = agent or DocsAgent()
    app = FastAPI(title='Docs Agent', version='1.0')

    @app.get('/health')
    async def health():
        return {'agent': 'docs', 'llm_configured': agent.settings.configured}

    @app.post('/agents/docs/run', response_model=AgentFinding)
    async def run(request: AgentRequest, response: Response,
                  authorization: Optional[str] = Header(default=None)):
        token = agent.settings.token
        if token and not hmac.compare_digest((authorization or '').encode(), f'Bearer {token}'.encode()):
            raise HTTPException(status_code=401, detail='Invalid agent credential')
        response.headers['X-Request-ID'] = request.request_id
        return await agent.run(request)

    return app


app = create_app()
