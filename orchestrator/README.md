# Incident Copilot orchestrator

Accept a question, select docs/logs/code roles, collect evidence, and return one
response with citations and an execution trace. Keyword routing is the default;
optional Azure Foundry analysis interprets intent and selects an existing route.
When Azure is configured, it also generates a cited answer from collected evidence
by default. Input analysis and answer generation can be selected independently.

## Documentation

| Guide | Use it for |
|---|---|
| [Pipeline summary](PIPELINE_SUMMARY.md) | Architecture, code responsibilities, routing, current scope, next sprint, and demos. |
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
| local (default) | Search local files; a configured agent URL overrides that role. |
| remote | Call configured services; unconfigured roles report unavailable. |
| demo | Use prepared scenarios and keyword routing; responses have demo=true. |

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

Azure can analyze intent and generate a final explanation from collected evidence.
Python executes supported routes, validates results and citation IDs, and retains
an evidence-list fallback. Full AI planning, generated specialist assignments, and
teammate-service integration remain planned work. Current local searches do not
inspect git history or persist investigations. Citation validation checks source
identity; it does not prove that every generated claim is supported.

Retrieved content is data and is never executed. Retrieval confidence does not
establish a root cause. The API is intended for local development.
