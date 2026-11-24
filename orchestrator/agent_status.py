"""Live status of the three agents for the header: are they up, and did their last run work?

"Up" alone isn't enough: a service can answer its health check and still fail every question
(for example an expired key). So each agent's status combines:
  - a health check of its service (GET <service>/health, 2 s timeout), and
  - the outcome of its most recent investigation, recorded by main.py after every question.

  ready     service up, and its last run returned evidence or found nothing (or it hasn't run yet)
  degraded  service up, but its last run failed (the error code is shown)
  offline   service not reachable
  local     agent runs inside the orchestrator (ORCHESTRATOR_MODE=local)
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Literal, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel

from schemas import CopilotResponse

State = Literal["ready", "degraded", "offline", "local"]


class LastRun(BaseModel):
    status: str                      # ok / no_evidence / error / no_access
    error_code: Optional[str] = None
    message: Optional[str] = None
    at: str


class AgentState(BaseModel):
    name: str
    state: State
    detail: str
    latency_ms: Optional[int] = None
    last_run: Optional[LastRun] = None


class AgentStatusReport(BaseModel):
    agents: list[AgentState]
    ready: int
    total: int
    checked_at: str


LAST_RUNS: dict[str, LastRun] = {}


def remember(response: CopilotResponse) -> None:
    """Record each agent's outcome from a finished investigation."""
    now = datetime.now(timezone.utc).isoformat()
    for finding in response.findings:
        LAST_RUNS[finding.agent] = LastRun(
            status=finding.status, at=now,
            error_code=finding.error.code if finding.error else None,
            message=(finding.error.message if finding.error else None) or
                    (finding.summary if finding.status in {"error", "no_access"} else None))


def health_url(run_url: str) -> str:
    """http://127.0.0.1:8101/agents/logs/run -> http://127.0.0.1:8101/health"""
    parts = urlsplit(run_url)
    return urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))


async def _probe(client: httpx.AsyncClient, url: str) -> tuple[bool, Optional[int], str]:
    start = time.perf_counter()
    try:
        response = await client.get(url)
    except httpx.HTTPError as exc:
        return False, None, f"not reachable ({type(exc).__name__})"
    latency = int((time.perf_counter() - start) * 1000)
    if response.status_code >= 400:
        return False, latency, f"health check returned HTTP {response.status_code}"
    return True, latency, ""


async def check(agents: dict, transport: Optional[httpx.AsyncBaseTransport] = None) -> AgentStatusReport:
    remote = {name: agent for name, agent in agents.items() if getattr(agent, "url", None)}
    async with httpx.AsyncClient(timeout=2.0, transport=transport) as client:
        probes = await asyncio.gather(*(_probe(client, health_url(a.url)) for a in remote.values()))
    results = dict(zip(remote, probes))
    states = []
    for name, agent in agents.items():
        last = LAST_RUNS.get(name)
        if name not in results:
            states.append(AgentState(name=name, state="local", last_run=last,
                                     detail="Runs inside the orchestrator (local file search)."))
            continue
        up, latency, why = results[name]
        if not up:
            states.append(AgentState(name=name, state="offline", latency_ms=latency, last_run=last,
                                     detail=f"Service {why}. Start it with run_local.py."))
        elif last and last.status in {"error", "no_access"}:
            code = last.error_code or last.status
            states.append(AgentState(name=name, state="degraded", latency_ms=latency, last_run=last,
                                     detail=f"Up, but the last question failed: {code}."))
        elif last:
            outcome = "returned evidence" if last.status == "ok" else "ran, found nothing relevant"
            states.append(AgentState(name=name, state="ready", latency_ms=latency, last_run=last,
                                     detail=f"Up; last question {outcome}."))
        else:
            states.append(AgentState(name=name, state="ready", latency_ms=latency,
                                     detail="Up; no questions asked yet."))
    ready = sum(s.state in {"ready", "local"} for s in states)
    return AgentStatusReport(agents=states, ready=ready, total=len(states),
                             checked_at=datetime.now(timezone.utc).isoformat())
