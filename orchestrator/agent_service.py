"""Optional reference HTTP host for the three local retrieval specialists.

Run in a separate process: uvicorn agent_service:app --host 127.0.0.1 --port 8100
Teammate services can implement the same AgentRequest -> AgentFinding contract.
"""
from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException

from agents import LocalSearchAgent, UnavailableAgent
from schemas import AgentFinding, AgentName, AgentRequest
from settings import Settings

SETTINGS = Settings.from_env()
ROOTS = {"logs": SETTINGS.logs_root, "docs": SETTINGS.docs_root, "code": SETTINGS.code_root}
# This service always hosts local specialists; URL variables cannot cause recursive calls.
AGENTS = {name: LocalSearchAgent(name, root, SETTINGS.timeout_seconds) if root is not None
          else UnavailableAgent(name, f"{name} corpus is not configured") for name, root in ROOTS.items()}
app = FastAPI(title="Reference specialist agents", version="1.0")


@app.post("/agents/{name}/run", response_model=AgentFinding)
async def run_agent(name: AgentName, request: AgentRequest,
                    authorization: Annotated[str | None, Header()] = None):
    token = SETTINGS.agent_tokens.get(name)
    if token and not hmac.compare_digest((authorization or "").encode(), f"Bearer {token}".encode()):
        raise HTTPException(401, "Invalid agent credential")
    return await AGENTS[name].run(request.question, {
        "request_id": request.request_id, "intent": request.intent,
        "incident_id": request.incident_id, "conversation_id": request.conversation_id,
        "evidence_bundle": request.evidence_bundle,
    })
