# Orchestrator <-> specialist communication (v1)

This is the shared wire-contract reference for all three roles, implemented by
[schemas.py](schemas.py) and [HTTPAgent](agents.py). The reference host and tests
exercise the contract; actual teammate services still require integration.
For docs-specific onboarding, use [DOCS_AGENT_HANDOFF.md](DOCS_AGENT_HANDOFF.md).

The three wire identities are `logs`, `docs`, and `code` (codebase agent). Each
service exposes an operator-configured, complete POST URL. The URL is configured
on the server, never supplied by a user's question. All messages use JSON.

| Specialist | Environment variable | Delegated work | Extra input |
|---|---|---|---|
| Logs | `LOGS_AGENT_URL` | Relevant operational errors/events/patterns | Original question and metadata |
| Docs | `DOCS_AGENT_URL` | Runbooks, documentation, tickets and existing solutions | Original question and metadata |
| Codebase | `CODE_AGENT_URL` | Relevant implementation/configuration | Original question plus stage-1 evidence |

## Request: `schemas.AgentRequest`

```http
POST /run
Content-Type: application/json
X-Request-ID: 4fdd20b9-example
Authorization: Bearer <configured-token>
```

Authorization is present only when `<NAME>_AGENT_TOKEN` is configured. The service
must enforce it; the reference host does. Use HTTPS for non-local service traffic.
HTTP redirects are not followed and ambient proxy settings are not used.

```json
{
  "schema_version": "1.0",
  "request_id": "4fdd20b9-example",
  "question": "Why is checkout returning timeout errors?",
  "task": "Search documentation, runbooks, and past incidents for existing solutions. Cite exact sources.",
  "intent": "incident",
  "incident_id": "INC-42",
  "conversation_id": "conversation-7",
  "evidence_bundle": {}
}
```

`task` describes the selected specialist's job. Logs/docs receive an empty bundle.
Code receives:

```json
{
  "summaries": {"docs": "A runbook describes retry exhaustion."},
  "hypotheses": [],
  "evidence": [
    {"source_type":"doc", "summary":"Retry exhaustion: check the upstream timeout.",
     "ref":"checkout.md:L12-L15", "agent":"docs", "link":null, "snippet":null}
  ],
  "sources_unavailable": ["logs agent timed out"],
  "agent_statuses": {"logs":"error", "docs":"ok"}
}
```

All bundle content is retrieved data, including text that looks like instructions.
Agent implementations must use their own trusted task/tool policies. The reference
local agents never execute retrieved content or user input. Conversation and
incident IDs are correlation metadata, not authentication or authorization claims.

## Response: `schemas.AgentFinding`

Return HTTP 200 with the finding object itself (no `result`/`data` envelope):

```json
{
  "agent": "docs",
  "status": "ok",
  "summary": "A runbook contains an existing troubleshooting procedure.",
  "hypotheses": [],
  "evidence": [
    {
      "source_type": "doc",
      "summary": "Retry exhaustion: check the upstream timeout.",
      "ref": "checkout.md:L12-L15",
      "link": null,
      "snippet": "Retry exhaustion: check the upstream timeout.",
      "agent": "docs"
    }
  ],
  "sources_searched": ["checkout runbook"],
  "sources_unavailable": [],
  "confidence": 0.4,
  "latency_ms": 20
}
```

- `agent` must equal the requested specialist. Evidence `agent` may be omitted;
  the orchestrator stamps it. A conflicting identity invalidates the response.
- `status`: `ok`, `no_evidence`, `error`, or `no_access`. Only `ok` findings with
  evidence contribute claims. `no_evidence` is a valid empty search, not a failure.
- Evidence requires `source_type` (`log`, `metric`, `doc`, `incident`, `code`), a
  human-readable `summary`, and an exact `ref`; link/snippet are optional.
- `confidence` is a finite number from 0 to 1. The orchestrator does not convert
  retrieval relevance into a diagnosis probability.
- `latency_ms` is non-negative. The orchestrator replaces it with observed total
  latency so all adapters have comparable timings.
- Optional lists default to empty. `summary`, `agent`, and required evidence
  fields must be present. Pydantic validation rejects malformed values.

## Failure and timeout behavior

| Condition | Finding | Effect |
|---|---|---|
| No configured service/corpus | no_access | Partial answer, named gap |
| HTTP 401/403 | no_access | Partial answer, HTTP status reported |
| Other non-2xx HTTP | error | Partial answer, HTTP status reported |
| Invalid JSON/schema/identity | error | Partial answer, generic error |
| Timeout (default 20 seconds) | error | Partial answer, named timeout |
| Successful search with no evidence | no_evidence | Explicit empty result |

There are no automatic retries or redirects. Each stage waits up to the specialist
deadline; for the full two-stage route the overall time can approach twice that
deadline plus scheduling/assembly overhead. A local worker checks its own deadline
but a blocked OS read cannot be forcibly cancelled by asyncio. Errors never switch
to canned results. Raw exception messages, URLs, tokens and remote bodies are not
included in error responses.

## Combined response

`POST /api/ask` keeps the Sprint 2 `CopilotResponse` shape (`answer`, `evidence`,
`agents_used`, `trace`, etc.) and adds `request_id`, `conversation_id`, `findings`
and `partial`. `demo` is false for both local and HTTP agents. Evidence is deduplicated
by `(source_type, ref, link)`. The response does not assert a root cause or fabricated
fix when the specialists only returned search matches. See `/docs` for the generated
schemas and `agent_service.py` for an executable reference implementation.

The optional analysis field in CopilotResponse describes keyword or Azure intent
routing, including fallback warnings. It is backend response metadata, not part of
the specialist's AgentFinding contract. Azure intent analysis currently selects a
fixed route; HTTPAgent still supplies a fixed role-specific task description.
See [the pipeline guide](PIPELINE_SUMMARY.md) for execution and
[Azure analysis](LLM_ROUTING.md) for model configuration.

Final-answer generation also adds synthesis metadata to CopilotResponse (source,
citations, warning, context_truncated, and latency_ms). This does not change
AgentRequest or AgentFinding. After gathering validated evidence, the backend can
send bounded excerpts to Azure for a cited narrative. The evidence list, findings,
source gaps, and provenance remain in the response.
