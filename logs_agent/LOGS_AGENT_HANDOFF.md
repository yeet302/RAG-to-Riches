# Logs Agent onboarding for the orchestrator

This is the Logs Agent side of the v1 specialist contract. Shared request,
response, evidence, authentication, and error rules live in the orchestrator
repo (`COMMUNICATION.md` / `schemas.py`). This service implements that contract;
it does not replace it.

Investigation behavior (`investigate_logs` → `LogInvestigationResult`) is
unchanged. An HTTP adapter maps that result onto `AgentFinding`.

## What this service exposes

A reachable HTTP POST endpoint that accepts `AgentRequest` and returns an
`AgentFinding` object directly (HTTP 200, no `data`/`result` wrapper).

Two equivalent paths:

| Path | Use |
|---|---|
| `POST /agents/logs/run` | Matches the docs-agent handoff URL shape |
| `POST /run` | Matches `COMMUNICATION.md` |

Set orchestrator `LOGS_AGENT_URL` to the **complete** POST URL, for example
`http://127.0.0.1:8101/agents/logs/run`. Avoid a trailing slash; HTTPAgent does
not follow redirects.

- `agent` is always `logs`. Evidence `agent` is `logs` when present.
- `source_type` is `log`.
- `ref` is a stable log pointer such as
  `OTelLogs/{timestamp}/{service}/{trace_id}/{span_id}`.
- `question` is the only field used for investigation. `task` and
  `evidence_bundle` are ignored as authority (logs receives an empty bundle).
- Correlation IDs (`request_id`, `incident_id`, `conversation_id`) are logged,
  not used as credentials.

### Status mapping

| Logs Agent (`LogInvestigationResult`) | Wire (`AgentFinding.status`) |
|---|---|
| `evidence_found` | `ok` |
| `insufficient_evidence` with log events | `ok` (events are still claims; summary explains the gap) |
| `no_evidence_found` | `no_evidence` |
| `error` | `error` (generic summary; no internals) |
| Missing Azure workspace / 401/403 to Log Analytics | `no_access` |

Only `ok` findings with evidence contribute orchestrator claims. `no_evidence`
is a valid empty search.

Hypotheses on the wire are strings (the investigation `description` only).

## Start the Logs Agent HTTP host

From `logs_agent/` after `pip install -r requirements.txt` and a filled `.env`:

```bash
# Production-shaped local run: Azure logs + Foundry
export LOG_CONNECTOR=azure
uvicorn orchestrator_service:app --host 127.0.0.1 --port 8101
```

Or: `python orchestrator_service.py` (`LOGS_AGENT_HOST` / `LOGS_AGENT_PORT`).

`GET /health` returns `{"status":"ok","agent":"logs"}`.

If `LOGS_AGENT_TOKEN` is set, every `POST` requires
`Authorization: Bearer <token>`. The service enforces it. Configure the same
value as orchestrator `LOGS_AGENT_TOKEN`.

Keep Foundry and Azure credentials inside this service. Do not put them on
the orchestrator.

## Configure the orchestrator (do not commit these in this repo)

On the orchestrator host:

```bash
export LOGS_AGENT_URL=http://127.0.0.1:8101/agents/logs/run
# export LOGS_AGENT_TOKEN=<same value as this service, if used>
# Foundry + Azure Log Analytics work often exceeds the 20s default:
export AGENT_TIMEOUT_SECONDS=60
```

Then restart the orchestrator. `/api/health` should show `agents.logs` as
`HTTPAgent`.

## Direct contract check

```bash
curl -sS -X POST http://127.0.0.1:8101/agents/logs/run \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: logs-test-001' \
  -d '{"schema_version":"1.0","request_id":"logs-test-001","question":"Are there any checkout failures?","task":"Search operational logs for relevant errors, events, and patterns. Cite exact sources.","intent":"incident","evidence_bundle":{}}'
```

When `LOGS_AGENT_TOKEN` is set, also pass `Authorization: Bearer <token>`.

Expect HTTP 200 and a JSON object with `"agent":"logs"` and a `status` of
`ok`, `no_evidence`, `error`, or `no_access`.

Then, from the orchestrator:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"Why is checkout returning timeout errors?","routing_mode":"keyword","conversation_id":"logs-test"}'
```

Expect `agents_used` to include `"logs"`, `demo=false`, and a logs finding.

## In-process fallback (same mapping, no HTTP)

If the orchestrator process can import this package (repo root on `PYTHONPATH`):

```python
from logs_agent import AgentRequest, handle_agent_request

finding = handle_agent_request(
    AgentRequest(
        request_id="req-1",
        question="Why are checkout requests failing?",
        task="Search operational logs for relevant errors, events, and patterns.",
        intent="incident",
    )
)
```

HTTPAgent is the production path. Prefer `LOGS_AGENT_URL`.

## Timeout

HTTPAgent's default deadline is 20 seconds with no retries. A Logs Agent run
includes Azure Log Analytics queries plus Azure AI Foundry tool steps and often
needs more than 20 seconds. If the orchestrator times out, the finding becomes
`error` / partial on the orchestrator side even when this service later
succeeds. Coordinate `AGENT_TIMEOUT_SECONDS` before treating timeouts as agent
bugs.

## Acceptance checklist

- Complete POST URL and startup command
- Token requirements (none, or shared `LOGS_AGENT_TOKEN`)
- `LOG_CONNECTOR=azure`, workspace ID, Foundry config, managed identity / CLI
- One question known to match and one known to return `no_evidence`
- `agent` is `logs`; evidence `source_type` is `log`; `ref` is exact
- Direct curl succeeds; `/api/ask` includes a logs finding
- Timeout budget agreed with the orchestrator
