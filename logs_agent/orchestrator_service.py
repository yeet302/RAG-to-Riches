"""
HTTP host for the orchestrator v1 Logs Agent contract.

POST /run and POST /agents/logs/run accept AgentRequest and return an
AgentFinding object (HTTP 200, no envelope).

    uvicorn orchestrator_service:app --host 127.0.0.1 --port 8101
"""

from __future__ import annotations

import asyncio
import hmac
import os
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException

from orchestrator_adapter import handle_agent_request
from orchestrator_contract import AgentFinding, AgentRequest

app = FastAPI(title="Logs Agent", version="1.0")


def _configured_token() -> str:
    return os.getenv("LOGS_AGENT_TOKEN") or ""


def _authorize(authorization: str | None) -> None:
    token = _configured_token()
    if not token:
        return
    expected = f"Bearer {token}".encode()
    provided = (authorization or "").encode()
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid agent credential")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "agent": "logs"}


async def _run(
    request: AgentRequest,
    authorization: str | None,
) -> AgentFinding:
    _authorize(authorization)
    return await asyncio.to_thread(handle_agent_request, request)


@app.post("/run", response_model=AgentFinding)
async def run(
    request: AgentRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> AgentFinding:
    return await _run(request, authorization)


@app.post("/agents/logs/run", response_model=AgentFinding)
async def run_named(
    request: AgentRequest,
    authorization: Annotated[str | None, Header()] = None,
) -> AgentFinding:
    return await _run(request, authorization)


def main() -> None:
    import uvicorn

    host = os.getenv("LOGS_AGENT_HOST", "127.0.0.1")
    port = int(os.getenv("LOGS_AGENT_PORT", "8101"))
    uvicorn.run("orchestrator_service:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
