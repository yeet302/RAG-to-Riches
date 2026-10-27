# Three-agent integration

The orchestrator sends versioned JSON requests over HTTP to docs, logs, and code.
Each service returns a JSON finding with evidence and coverage gaps. The LLM plans
which agents to consult; same-stage calls run concurrently and stage 2 receives
validated stage-1 evidence. The final LLM receives the collected evidence and must
return citations validated by the orchestrator. Communication is request/response;
agents do not require a callback URL or a separate frontend connection.

## Configure remote endpoints

Edit the existing `orchestrator/.env`, preserving your Azure model settings:

```dotenv
ORCHESTRATOR_MODE=remote
ROUTING_MODE=auto
ANSWER_MODE=auto
AGENT_TIMEOUT_SECONDS=60
LOGS_AGENT_URL=http://127.0.0.1:8101/agents/logs/run
DOCS_AGENT_URL=http://127.0.0.1:8102/agents/docs/run
CODE_AGENT_URL=http://127.0.0.1:8103/agents/code/run
# Set tokens only when the corresponding service enables bearer authentication.
# LOGS_AGENT_TOKEN=
# DOCS_AGENT_TOKEN=
# CODE_AGENT_TOKEN=
```

Replace URLs with the full reachable POST endpoints from each developer. A remote
machine's `localhost` address is not reachable as that machine from your computer.
Keys configured with these TOKEN variables are sent as `Authorization: Bearer`.
If a hosted API expects another authentication header, its integration must be
adjusted to match that service. Do not put Azure source credentials in the frontend.
Remote mode reports missing endpoints as unavailable instead of searching local files.
Restart the backend after changing environment configuration.

## Start services locally (optional)

Install each service's requirements in its own Python environment. Keep service
source/model configuration in that service's environment. All services require the
shared root `agent_contracts.py` when deployed from this checkout.

From the repository root, run in separate terminals:

```bash
python -m uvicorn docs_agent.service:app --env-file docs_agent/.env --host 127.0.0.1 --port 8102
python -m uvicorn code_agent.service:app --env-file code_agent/.env --host 127.0.0.1 --port 8103
```

For logs, from `logs_agent/`:

```bash
python -m uvicorn orchestrator_service:app --env-file .env --host 127.0.0.1 --port 8101
```

Docs retrieves official documentation/GitHub and optionally uses its Azure model.
Logs requires its Foundry configuration and, for live Azure logs, `LOG_CONNECTOR=azure`,
a workspace ID, and Azure identity access. Code requires Foundry and Azure AI Search
configuration, Azure identity access, and an indexed repository revision. See each
service's README for the source-specific settings. A service's `/health` endpoint
indicates liveness, not successful authentication or retrieval from its data source.

Start the orchestrator from `orchestrator/`:

```bash
python -m uvicorn main:app --env-file .env --host 127.0.0.1 --port 8000
```

The frontend reads `/api/health` to select the backend's effective planning and
answer modes. With Azure configured and both modes `auto`, it selects LLM planning
and answers. Earlier initialization values in the React component are replaced by
this health response. A question may select fewer than three agents when appropriate.

## Verification

`/api/health` should show `HTTPAgent` for all three roles. This confirms the adapter
configuration, not network reachability. Submit a known question through the UI.
Check `analysis.source=llm`, assignments, `findings`, evidence references,
`synthesis.source=llm`, and `sources_unavailable`. Missing agents/timeouts produce
an explicit partial response. Empty evidence does not trigger an invented AI answer.

The automated test `tests/test_three_services.py` exercises `/api/ask`, the actual
HTTP adapter, all three actual specialist hosts, bearer authentication, delegated
context, logs/docs evidence passed to code, final synthesis, and partial results.
External model/source calls are mocked. Live retrieval requires the real service
URLs, credentials, and known source data; automated success does not establish it.

## Agents that accept requests without authentication

Set the three full agent POST URLs and leave `LOGS_AGENT_TOKEN`,
`DOCS_AGENT_TOKEN`, and `CODE_AGENT_TOKEN` unset or empty. The HTTP adapter then
sends neither an Authorization header nor an API key. The corresponding services
must also have their agent tokens unset. No agent-token secrets are needed in Key
Vault in this configuration. The orchestrator model still needs its own Azure
credentials, and each agent still needs access to its model and data sources.

This applies to the agents' FastAPI endpoints, not raw Foundry project or Azure
Search URLs. Reachability or firewall access alone does not grant Azure identity
permissions. The three-service integration tests cover both authenticated and
unauthenticated calls, including partial results when logs is unavailable.


## User query lifecycle

In remote integration mode the browser sends the original question and correlation
IDs to `/api/ask` without routing or answer-mode overrides. Set `ROUTING_MODE=llm`
and `ANSWER_MODE=llm` in `orchestrator/.env` for model planning and final compilation.
The planning model produces focused questions, tasks, and stages for the selected
specialists. Independent assignments run concurrently; later assignments receive
validated earlier findings. The specialist hosts access their own sources: docs
uses official OpenTelemetry documentation and GitHub documents, logs queries its
configured Log Analytics workspace, and code uses Azure AI Search and GitHub source.
The final model receives the original query, source excerpts, successful specialist
summaries and hypotheses linked to evidence IDs, statuses, and coverage gaps. It
compiles a cited response; only source IDs from that package are accepted. No source
evidence means no generated factual answer. Failed planning is labeled as keyword
fallback; failed synthesis returns the actual evidence list with a warning.


## Per-agent deadlines

`AGENT_TIMEOUT_SECONDS` remains the shared fallback. Set
`LOGS_AGENT_TIMEOUT_SECONDS=180` to allow longer Foundry/Log Analytics investigations;
`DOCS_AGENT_TIMEOUT_SECONDS` and `CODE_AGENT_TIMEOUT_SECONDS` optionally override the
other roles. The HTTP read timeout and total orchestration deadline both use the
same effective value. Connection/pool waits stay bounded to at most 10 seconds.
`/api/health` exposes `agent_timeouts_seconds` for verification. Investigations still
return `AGENT_TIMEOUT` when a role exceeds its own deadline; failed calls are not
automatically retried. Restart the orchestrator after changing these settings.
