# Incident Copilot orchestrator

Text question -> select logs/docs/codebase specialists -> validate JSON findings ->
return one answer with file/line citations and an execution trace.
This extends Justina's Sprint 2 FastAPI/router skeleton. `code` is the existing API
name for the codebase agent; there are three specialist roles, not four.

For a step-by-step implementation explanation, see [How the orchestrator works](ORCHESTRATOR_README.md).

## Run from this directory (Python 3.10+)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python chat.py
```

Or run the existing API:

```bash
uvicorn main:app --host 127.0.0.1 --port 8000
curl -s http://127.0.0.1:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"How does the orchestrator route requests?"}'
```

`GET /docs` opens FastAPI's interactive API documentation. There is no bundled
browser chat frontend in this checkout. `chat.py` provides a terminal chat loop.
Questions are independent; conversation/incident IDs are propagated for correlation,
not interpreted as persisted conversation history.

By default, docs and code search the files in this directory. They retrieve actual
text, not hard-coded scenarios. Set `DOCS_ROOT`, `CODE_ROOT`, and `LOGS_ROOT` to your
application data. Logs report `no_access` until a corpus or remote endpoint is set.
Local docs support Markdown, reStructuredText, plain text and AsciiDoc. This is
bounded lexical retrieval, not vector search or an LLM diagnosis engine.

```bash
export DOCS_ROOT=/path/to/runbooks
export CODE_ROOT=/path/to/application
export LOGS_ROOT=/path/to/logs
python chat.py
```

The orchestrator runs logs and docs concurrently for incident questions, then passes
their evidence to code search. How-to questions use docs; code questions use docs then
code; log lookups use logs. Unknown intent takes the full path. See [ROUTING.md](ROUTING.md).

## Connect teammates' agents

Set each full HTTP POST endpoint and optionally its bearer token, then restart:

```bash
export ORCHESTRATOR_MODE=remote
export LOGS_AGENT_URL=http://127.0.0.1:8101/run
export DOCS_AGENT_URL=http://127.0.0.1:8102/run
export CODE_AGENT_URL=http://127.0.0.1:8103/run
uvicorn main:app --host 127.0.0.1 --port 8000
```

All three use the versioned `AgentRequest` -> `AgentFinding` JSON contract documented
in [COMMUNICATION.md](COMMUNICATION.md). In local mode, individual URL settings also
work: a configured URL overrides that local specialist, so services can be integrated
one at a time. Remote mode reports unconfigured specialists as unavailable.
There is no automatic demo fallback.

A reference host lets you exercise real HTTP communication before teammate services
are available. In terminal 1, set the three corpus roots and run:

```bash
uvicorn agent_service:app --host 127.0.0.1 --port 8100
```

In terminal 2, set the orchestrator's endpoints:

```bash
export ORCHESTRATOR_MODE=remote
export LOGS_AGENT_URL=http://127.0.0.1:8100/agents/logs/run
export DOCS_AGENT_URL=http://127.0.0.1:8100/agents/docs/run
export CODE_AGENT_URL=http://127.0.0.1:8100/agents/code/run
python chat.py
```

The host reads local files; it does not proxy the endpoint variables. If an agent
bearer token is configured on the host, configure the same token on the orchestrator.
`.env.example` lists settings; `.env` files are not loaded automatically.

## Verify

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests cover routing, real file retrieval through the API, JSON and identity checks,
timeouts, partial failures, evidence handoff, and HTTP calls to a running specialist
server. The fixtures create clearly synthetic files in temporary directories.

## Scope and limits

Live responses combine cited evidence without inventing a root cause, confidence
percentage, or remediation commands. Local matches are useful investigation leads;
remote agents can provide richer retrieved evidence through the same contract.
Full natural-language synthesis, semantic/model routing, git-history search, durable
investigation state, caching, and enterprise authentication/RBAC are future work.
The API is intended for local development; expose it only behind appropriate access
controls. Retrieved content is treated as data and is never executed.

`ORCHESTRATOR_MODE=demo` explicitly restores the old canned scenarios and marks
responses `demo: true`. Sample incidents are exposed only in demo mode.
