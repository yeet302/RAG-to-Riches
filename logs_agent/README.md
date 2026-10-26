# Logs Agent

Specialist agent that investigates application logs and returns structured evidence for an orchestrator.

Investigation engine: `investigate_logs(request: str) -> LogInvestigationResult`

Orchestrator v1 wire contract: HTTP `POST` of `AgentRequest` → `AgentFinding`.
See [LOGS_AGENT_HANDOFF.md](LOGS_AGENT_HANDOFF.md).

## Orchestrator entry points

Production (HTTPAgent). Complete POST URL, no trailing slash:

```bash
uvicorn orchestrator_service:app --host 127.0.0.1 --port 8101
# LOGS_AGENT_URL=http://127.0.0.1:8101/agents/logs/run
```

In-process (same mapping, if this package is importable):

```python
from logs_agent import AgentRequest, handle_agent_request, investigate_logs

finding = handle_agent_request(
    AgentRequest(
        request_id="req-1",
        question="Why are checkout requests failing?",
        task="Search operational logs for relevant errors, events, and patterns.",
        intent="incident",
    )
)

# Internal investigation API (not the HTTP wire contract):
result = investigate_logs("Why are checkout requests failing?")
```

The parent of this `logs_agent/` directory must be on `PYTHONPATH` (the GitHub repo root that contains this folder).

## Architecture

```
Application / services
  → OpenTelemetry
  → OpenTelemetry Collector
  → Azure Log Analytics / OTelLogs
  → AzureLogAnalyticsConnector
  → Logs Agent
  → LogInvestigationResult
  → AgentFinding adapter (HTTP POST /agents/logs/run)
  → Orchestrator
```

The OpenTelemetry Demo is a separate repo and is not part of this project.

## Backends

| `LOG_CONNECTOR` | Backend |
|---|---|
| `local` (or unset) | `LocalLogConnector` — `data/ad_service.log` |
| `azure` | `AzureLogAnalyticsConnector` — Azure Log Analytics table `OTelLogs` |

### Azure field mapping

| OTelLogs column | `LogEvent` field |
|---|---|
| TimeGenerated | timestamp |
| ServiceName | service |
| SeverityText | severity |
| TraceId | trace_id |
| SpanId | span_id |
| Body | message |

## Authentication

- Code uses `DefaultAzureCredential`. Do not store Azure credentials in the repository.
- **Local development:** typically Azure CLI (`az login`).
- **Production:** the same code path with a **managed identity**. That identity must be allowed to query the target Log Analytics workspace.

Copy `.env.example` to `.env` for local runs. `.env` is gitignored.

## Model

`FOUNDRY_MODEL` selects the Azure AI Foundry deployment.

Recommended Logs Agent configuration: **`gpt-4.1-mini`**.

A model-router deployment can be set via `FOUNDRY_MODEL` if needed; it is not required for production Logs Agent operation.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in values
```

## Testing

Run from this directory with the project venv (`python -m pytest`, not a global Anaconda `pytest`).

```bash
# Offline unit tests (default; does not query Azure or Foundry)
python -m pytest -q

# Live Azure Log Analytics connector tests (no LLM)
python -m pytest -q -m integration tests/integration/test_azure_integration.py

# Live Azure + Foundry agent smoke test (invokes the model; may incur cost)
python -m pytest -q -m integration_agent tests/integration/test_azure_agent_smoke.py
```

- Unit tests are mocked and offline.
- `integration` tests query the real Azure workspace.
- `integration_agent` also calls the Foundry model.

## Production assumptions

The production environment should provide:

- `LOG_CONNECTOR=azure`
- `AZURE_LOG_ANALYTICS_WORKSPACE_ID`
- Foundry configuration: `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_AGENT_NAME`, `FOUNDRY_MODEL`
- Azure managed identity (or equivalent) with Log Analytics read permission
- Network access to Azure Monitor and Azure AI Foundry
- A reachable Logs Agent HTTP URL (`LOGS_AGENT_URL` on the orchestrator)
- Optional shared `LOGS_AGENT_TOKEN` (same value on both sides; this service enforces it)
- Orchestrator `AGENT_TIMEOUT_SECONDS` large enough for Azure + Foundry (default 20s is often too short)
