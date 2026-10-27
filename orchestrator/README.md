# Incident Copilot orchestrator

Accept a question, select docs/logs/code roles, collect evidence, and return one
response with citations and an execution trace. When Azure is configured, it plans
question-specific JSON assignments for the selected specialist roles and generates
a cited answer from their returned evidence. Without Azure, keyword routing and an
evidence list are used. Planning and answer generation are independent controls.

## Documentation

| Guide | Use it for |
|---|---|
| [Pipeline summary](PIPELINE_SUMMARY.md) | Architecture, code responsibilities, routing, current scope, next sprint, and demos. |
| [Three-agent integration](INTEGRATION.md) | Endpoint setup, credentials, launch commands, and verification. |
| [Agent communication](COMMUNICATION.md) | Shared JSON/HTTP request and finding contract. |
| [Azure analysis](LLM_ROUTING.md) | Model configuration, mode selection, fallback, and verification. |
| [Docs-agent handoff](DOCS_AGENT_HANDOFF.md) | Docs-specific onboarding and acceptance checks. |
| [Frontend setup](../frontend/README.md) | Running the browser interface with this backend. |

## Quick start

From the orchestrator directory in WSL (Python 3.10+):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
.venv/bin/python -m uvicorn main:app --env-file .env --host 127.0.0.1 --port 8000
```

Existing .env configuration is preserved. Restart the backend after editing it.
The app reads environment variables; --env-file explicitly loads the local file.
For optional model analysis, follow [Azure setup](LLM_ROUTING.md).

Open http://127.0.0.1:8000/docs for interactive API documentation. If frontend/dist
exists, the backend also serves that build at /. For Vite development on port 5173,
follow the [frontend instructions](../frontend/README.md).

To use the terminal interface instead:

```bash
.venv/bin/python chat.py
```

Terminal chat uses exported environment variables; it does not automatically load
.env. Questions are independent; conversation IDs do not provide stored history.

## Choose data sources

Specialist mode, question analysis, and answer generation are independent.

| ORCHESTRATOR_MODE | Specialist behavior |
|---|---|
| local | Explicit offline retrieval: search local files; a configured agent URL overrides that role. |
| remote (default) | Call configured specialist services; unconfigured roles report unavailable. |

Local docs and code search this directory unless DOCS_ROOT and CODE_ROOT are set.
Logs requires LOGS_ROOT or LOGS_AGENT_URL. Actual teammate services still need
integration. Configure their full POST URLs with LOGS_AGENT_URL, DOCS_AGENT_URL, and
CODE_AGENT_URL, with optional per-role tokens. See the [contract](COMMUNICATION.md).

The [reference host](agent_service.py) exposes local searches over HTTP for testing;
it does not implement teammates' AI/database pipelines.

## Verify

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The tests exercise local retrieval, routing, staged execution, validation, failures,
reference HTTP communication, and mocked Azure analysis. Live model verification
is separate; inspect analysis.source and synthesis.source in a real response.

## Scope

Azure generates a bounded plan (at most one assignment per role across two stages)
and a final explanation from collected evidence. Python validates the plan, sends
JSON requests to configured specialist services, validates findings and citation
IDs, and retains keyword/evidence fallbacks. Actual teammate model endpoints still
need to be configured and verified. Current local searches do not
inspect git history or persist investigations. Citation validation checks source
identity; it does not prove that every generated claim is supported.

Retrieved content is data and is never executed. Retrieval confidence does not
establish a root cause. The API is intended for local development.


## Keyless model authentication

Set `AZURE_AI_AUTH_MODE=entra` in this service's `.env`, along with
`AZURE_AI_ENDPOINT` and `AZURE_AI_MODEL`. No `AZURE_AI_API_KEY` is needed in this
mode. Install this service's requirements, run `az login` in the same environment
as the service, and grant your account access to the model resource. Azure Identity
obtains access tokens for requests. Restart the service after changing `.env`.
API-key authentication remains the default (`AZURE_AI_AUTH_MODE=api_key`).


## Evidence-only integration

Prepared diagnoses, incident examples, and demo mode are removed from the runtime.
The integration defaults to `ORCHESTRATOR_MODE=remote`: all factual inputs to the
final answer come from successful subagent findings with source evidence. Missing
or failed agents report unavailable; they never fall back to prepared answers or
local corpora. Without supporting evidence, the response states that evidence is
missing and skips final model generation. Citation validation checks source IDs;
it does not prove that every generated claim is supported by its cited text.
`/api/incidents` and `/api/suggestions` remain empty until real sources are connected.


## Actionable pipeline errors

Processed investigations retain HTTP 200 so callers can inspect partial evidence,
findings, and the execution trace. Inspect `pipeline_status` (`ok`, `partial`,
`failed`, or `no_evidence`) and the `errors` list rather than treating HTTP 200 as
proof of successful source access. HTTP 400/422 still report invalid API requests.
Each error provides a stable `code`, `agent`, `stage`, safe `message`, `resolution`,
`retryable` flag, and the dependency's `http_status` when available. Specialist
handoffs carry the same object in `finding.error`; stage-2 evidence bundles include
those errors alongside source gaps. Failed findings never become factual evidence.
The browser displays the error codes and required actions.

Common codes include `DOCS_MODEL_ACCESS_DENIED`, `CODE_FOUNDRY_ACCESS_DENIED`,
`CODE_SEARCH_ACCESS_DENIED`, `AGENT_TIMEOUT`, `AGENT_UNREACHABLE`,
`AGENT_NOT_CONFIGURED`, `AGENT_INVALID_RESPONSE`, `PLANNING_FALLBACK`, and
`SYNTHESIS_FAILED`. An empty successful search is `no_evidence`, not an access error.
