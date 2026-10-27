# Project Name
Team Name: RAG to Riches
UW-Madison CS 620 Capstone Project

## Team Members
- Navya P.
- John W. 
- Roshan S.
- Justine R.
- Kalyan M.
- Lantian Y.

## Project Description
Incident Copilot runs an Azure-backed orchestrator that plans investigations, delegates tasks to docs/logs/code specialists, and compiles their source-grounded findings into a cited answer. The browser displays the plan, execution trace, evidence, and actionable dependency errors.

## Logs Agent

The Logs Agent lives in [`logs_agent/`](logs_agent/).

Orchestrator integration (HTTP `AgentRequest` → `AgentFinding`, URLs, tokens, timeouts, curl checks):

- **[logs_agent/LOGS_AGENT_HANDOFF.md](logs_agent/LOGS_AGENT_HANDOFF.md)** — start here if you are wiring `LOGS_AGENT_URL`
- [logs_agent/README.md](logs_agent/README.md) — setup, Azure backend, tests, production assumptions


## Run the integrated app locally

- [Integration setup](orchestrator/INTEGRATION.md): configure and launch the orchestrator plus all three specialist services.
- [Frontend setup](frontend/README.md): launch the React interface at http://localhost:5173.
- [Model configuration](orchestrator/LLM_ROUTING.md): Azure model endpoints, API-key or keyless sign-in, planning, and synthesis.

Keep credentials in each service's ignored `.env` file. Azure resource permissions
are required for keyless model/data access; the code agent can use `GITHUB_TOKEN`
for authenticated repository retrieval. The default integration uses actual remote
specialist endpoints. Prepared answers and synthetic incident cards are disabled.
Each investigation reports source coverage and errors explicitly. Logs can use a
separate 180-second deadline via `LOGS_AGENT_TIMEOUT_SECONDS`.
