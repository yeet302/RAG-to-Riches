# Docs Agent

The Docs Agent retrieves OpenTelemetry Demo documentation and returns source references for debugging questions. It provides an HTTP interface for integration with the orchestrator.

**Current status:** Live retrieval and the HTTP interface are implemented. Azure LLM integration code is included, but configuration and live answer validation are pending.

## 1. Overview

Documentation sources:

- [Official OpenTelemetry Demo documentation](https://opentelemetry.io/docs/demo/)
- [OpenTelemetry Demo GitHub repository — Ad Service](https://github.com/open-telemetry/opentelemetry-demo/tree/main/src/ad)

The agent discovers website pages through links and GitHub documents through the repository tree. It splits documents into excerpts and ranks them using keyword matching.

```text
Question → Discover documents → Retrieve relevant excerpts
         → Generate a cited answer with Azure → Return findings
```

The answer-generation step requires Azure configuration. Without it, both retrieval and the HTTP endpoint return retrieved evidence without LLM analysis.

## 2. Setup

Run commands from the repository root. Use Python 3.9 or later; Python 3.10+ is recommended for orchestrator integration.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r docs_agent/requirements.txt
```

## 3. Run

### Run retrieval without Azure

This retrieves real documents and prints matching excerpts, source links, and coverage gaps. It does not generate an LLM answer.

```bash
python - <<'PY'
import asyncio
import json
from docs_agent.main import DocsAgent

async def main():
    question = "How do Ad Service feature flags cause failures?"
    evidence, searched, gaps = await DocsAgent().retrieve(question)

    print(json.dumps({
        "question": question,
        "sources_searched": searched,
        "coverage_gaps": gaps,
        "evidence": [item.model_dump() for item in evidence],
    }, indent=2))

asyncio.run(main())
PY
```

Change `question` to try another English documentation question.

### Start the HTTP service

```bash
python -m uvicorn docs_agent.service:app --host 127.0.0.1 --port 8102
```

Keep the service running so the orchestrator can send requests.

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Check service availability and whether LLM settings are present |
| `POST /agents/docs/run` | Submit a documentation question |
| `GET /docs` | View and test the API interactively |

For access from another machine, bind to `0.0.0.0` instead of `127.0.0.1` and provide a reachable host address.

## 4. Orchestrator Integration

Configure the docs endpoint in the orchestrator environment:

```bash
export DOCS_AGENT_URL=http://127.0.0.1:8102/agents/docs/run
```

Restart the orchestrator after changing its configuration. Use a non-demo mode and allow at least 20 seconds for agent requests.

If `DOCS_AGENT_TOKEN` is enabled, configure the same token in both services. The orchestrator sends it as a bearer token.

### Input

`POST /agents/docs/run` accepts an `AgentRequest`:

```json
{
  "schema_version": "1.0",
  "request_id": "docs-test-001",
  "question": "How do I build the Ad Service locally?",
  "task": "Search documentation and cite exact sources.",
  "intent": "how_to",
  "evidence_bundle": {}
}
```

Optional fields: `incident_id` and `conversation_id`.

### Output

The endpoint returns an `AgentFinding` directly, without a `data` or `result` wrapper.

| Field | Meaning |
| --- | --- |
| `agent` | Always `docs` |
| `status` | `ok`, `no_evidence`, `no_access`, or `error` |
| `summary` | Cited answer or an explanation of why no answer was generated |
| `hypotheses` | Evidence-linked possibilities or suggested checks |
| `evidence` | Source references, links, and retrieved excerpts |
| `sources_searched` | Documents successfully searched |
| `sources_unavailable` | Failed sources or incomplete coverage |
| `confidence` | Retrieval indicator, not an incident-cause probability |
| `latency_ms` | Agent processing time |

Each `[n]` citation refers to the nth item in that finding’s `evidence` list. GitHub references use file line numbers; website references use page or section links.

**Current integration scope:** Without Azure configuration, the endpoint operates in retrieval-only mode. Matching documents return `ok` with original excerpts and a summary explicitly stating that no LLM analysis was performed; `hypotheses` is empty. No matches return `no_evidence`, and inaccessible documentation returns `no_access`. Actual coverage gaps remain visible. The orchestrator can use successful retrieved evidence in downstream analysis now. Once Azure is configured, the same endpoint and schema return generated answers; model failures still return `error` rather than silently switching modes.

## 5. Tests and Results

Run the automated tests:

```bash
python -m pytest docs_agent/tests -q
```

| Test file | What it tests | Tests | Passed |
| --- | --- | ---: | ---: |
| `test_docs_agent.py` | Question cases, retrieval-only responses, citations, authentication, failures, and timeouts | 23 | 23 |
| `test_discovery.py` | Website/GitHub discovery, source boundaries, coverage limits, and partial failures | 14 | 14 |
| `test_orchestrator.py` | HTTP adapter, `/api/ask`, and evidence handoff in both retrieval-only and mocked LLM modes | 6 | 6 |
| **Total** | | **43** | **43** |

All 43 tests passed in the last verified run. Automated tests use mocked document and model responses. Orchestrator tests execute the reference implementation through in-process HTTP transport.

To include the orchestrator integration tests, provide the local orchestrator source directory:

```bash
ORCHESTRATOR_ROOT=/absolute/path/to/orchestrator \
python -m pytest docs_agent/tests -q
```

Without `ORCHESTRATOR_ROOT`, the six integration test cases are skipped.

### Live retrieval check

A separate real-network run for “How do Ad Service feature flags cause failures?” read **16 official pages and 2 GitHub files** in approximately **2.3 seconds**.

The first result included the official `adFailure` description concerning `GetAds` errors. The run reported incomplete coverage because it reached the website page limit. No LLM was called.

## 6. Current Limitations

- **Live LLM answers have not been validated.** Azure is not configured, so real answer quality, whether citations support the claims, and model response time have not been tested.
- **Retrieval ranking is basic.** English keyword matching may miss synonymous expressions or return directory listings, general descriptions, and duplicate excerpts.
- **Document coverage is incomplete.** By default, retrieval is limited to 16 website pages and 20 GitHub documents, with an additional time limit. Coverage gaps are reported when limits are reached.
