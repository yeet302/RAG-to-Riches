# Azure analysis and evidence-based answers

There are two independent model features:

| Feature | Purpose | Default |
|---|---|---|
| Question analysis | Interpret intent and select an existing execution route. | Keywords; Azure is optional. |
| Answer generation | Explain collected evidence in a readable, cited answer. | AI when Azure is configured; otherwise an evidence list. |

The same configured deployment, endpoint, and backend key are used for both.
Selecting Keywords only disables the input-analysis call. It does not disable
AI answer generation; choose Evidence list as well for a run with no model calls.

## Pipeline

```text
Question -> keyword rules OR Azure intent analysis
         -> Python executes an existing route
         -> collect and validate evidence
         -> Azure writes a cited explanation (default when configured)
         -> Python validates citation IDs and returns the response
```

The model does not execute code or contact specialist services itself. Python
controls execution, validation, timeouts, and response structure. Full planning
and query-specific assignments remain future work, as does real integration with
the teammates' specialist models and data sources.

## Configuration

Use the ignored orchestrator/.env for credentials. This checkout uses the provided
Foundry endpoint and exact deployment gpt-5.6-terra; there is no model substitution.
For new setup, populate AZURE_AI_ENDPOINT, AZURE_AI_MODEL, and AZURE_AI_API_KEY.

| Variable | Meaning |
|---|---|
| ROUTING_MODE | keyword (default) or llm for requests with no routing override. |
| ANSWER_MODE | auto (default), llm, or evidence. Auto uses AI when credentials are configured. |
| AZURE_AI_ENDPOINT | Resource URL, /openai/v1 base, or full /chat/completions URL. |
| AZURE_AI_MODEL | Exact deployment name. |
| AZURE_AI_API_KEY | Backend key, never a frontend VITE_ variable. |
| AZURE_AI_API_STYLE | openai_v1 (default), or inference for an existing /models endpoint. |
| AZURE_AI_API_VERSION | Optional API version; normally omitted for v1. |
| LLM_TIMEOUT_SECONDS | Input-analysis deadline, default 10 seconds. |
| SYNTHESIS_TIMEOUT_SECONDS | Answer-generation deadline, default 30 seconds. |

Start from orchestrator/:

```bash
.venv/bin/python -m uvicorn main:app --env-file .env --host 127.0.0.1 --port 8000
```

Restart after editing .env and refresh the frontend. The app reads environment
variables; --env-file loads the file explicitly. Existing exported variables can
override it. Terminal chat uses exported variables.

Explicit canned demo mode keeps prepared answers and makes no model requests.

## Frontend and API controls

Use **Question analysis** to choose Keywords or Azure AI.
Use **Answer generation** to choose AI explanation or Evidence list.

Examples:

| Selection | Model calls when evidence exists |
|---|---|
| Keywords + Evidence list | None |
| Azure AI + Evidence list | Input analysis only |
| Keywords + AI explanation | Final-answer generation only |
| Azure AI + AI explanation | Separate analysis and final-answer calls |

API overrides use two independent fields:

```json
{
  "question": "How does the orchestrator route requests?",
  "routing_mode": "keyword",
  "answer_mode": "llm"
}
```

Omit answer_mode to use the backend default. GET /api/route only previews intent
analysis and does not collect evidence or generate an answer.

## Evidence and citation handling

The final-answer request contains the original question, retrieved evidence
summaries/excerpts, agent statuses, source gaps, and the selected intent. This is
a deliberate data-flow change from input analysis, which only sends the question
and trusted role descriptions.

Only evidence from successful findings enters the combined evidence list.
The model receives up to 12 sources and a bounded context (32,000 characters),
with shortened summaries/excerpts. Original evidence remains in the API response
and Sources UI even when model context is limited.

The prompt treats questions and retrieved content as untrusted data. It requires
grounded explanations, distinguishes facts from interpretations, reports gaps,
and prohibits invented observations, sources, commands, and confidence percentages.
Static examples and keyword matches are not proof of a live incident cause.

The model returns JSON containing answer and evidence_ids. It cites supplied IDs
such as [E1]. Python checks that all cited IDs exist and match the declared list,
then resolves them to the collected source references. Fabricated/missing IDs,
refusals, malformed responses, and timeouts retain the evidence-list fallback.

Citation checks validate source identity and format. They do not establish the
truth of every claim or guarantee that retrieved instructions cannot influence a
model. Readers can inspect the source excerpts. Root-cause confidence fields and
likely_cause are not populated from unverified narrative.

If sources are unavailable, Python preserves the coverage notice even if the model
omits it. With no supporting evidence, no model answer is generated.

## Inspect actual behavior

| Response field | Interpretation |
|---|---|
| analysis.source = llm | Azure successfully performed input analysis. |
| analysis.source = keyword / keyword_fallback | Rules were used for routing. |
| synthesis.source = llm | Azure generated the final answer and citation checks passed. |
| synthesis.source = evidence | Evidence-list mode, or no supporting evidence. |
| synthesis.source = evidence_fallback | AI answering was requested but unavailable or invalid. |
| synthesis.warning / context_truncated | Explains failure or limited context. |

The frontend labels the final output AI answer, Evidence list, or Evidence fallback.
Stage 4 in the execution trace records final-answer generation. Health reports
configuration presence and effective defaults; it does not prove a successful model
call. Check the response fields above.

## Code and tests

| File | Role |
|---|---|
| azure_client.py | Shared JSON Chat Completions transport for the configured deployment. |
| query_analysis.py | Input prompt, intent validation, route mapping, and keyword fallback. |
| answer_synthesis.py | Bounded evidence context, grounded prompt, citation validation, and output fallback. |
| settings.py / schemas.py | Configuration, independent modes, and analysis/synthesis metadata. |
| orchestrator.py | Execute searches, assemble evidence, then generate the final answer. |
| main.py | API overrides and health defaults. |
| frontend/src/App.tsx / api.ts / components/Thread.tsx | Controls, request fields, and output provenance. |
| tests/test_query_analysis.py / tests/test_answer_synthesis.py | Mocked contracts, failure modes, citations, and pipeline integration. |

The current suite contains 115 cases. Automated model tests use mock transports;
live model checks are separate. Input analysis was verified on 2026-10-20.
To prove final synthesis, send a request and inspect synthesis.source=llm.

See the [demo checklist](PIPELINE_SUMMARY.md#8-demo-checklist).

## API references

- [Azure Foundry Chat Completions](https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/azureopenai/chat)
- [OpenAI JSON output guidance](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)

The adapter requests JSON-object mode and performs application-side validation.
The configured deployment must support that API format.
