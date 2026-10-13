# Orchestrator routing notes

How the orchestrator will route a question to the logs, docs and code agents.
Status: routing and the stage flow are implemented (`router.py`, `orchestrator.py`).
The agents themselves are stubs that return hard-coded findings for 3 demo questions.

## Choice: Option 1, selective routing (from our architecture diagram)

The orchestrator calls only the agents a question needs, instead of calling all of them every time
(Option 2, parallel routing). Why: lower token cost, less irrelevant evidence, and it scales as we add agents.
To limit Option 1's main risk (missing evidence because the wrong agent was skipped), an incident question
calls **all three agents**, and the unclear-intent case falls back to the full path too.

## The flow for an incident question

```
Engineer question
   │
   ▼
Orchestrator: classify -> RoutePlan {intent, stage1, stage2}
   │
   ├── Stage 1 (parallel, asyncio.gather) ──┬── Logs agent  -> AgentFinding JSON
   │                                        └── Docs agent  -> AgentFinding JSON
   │
   ├── merge_findings(): one evidence bundle (summaries, hypotheses, evidence, sources it couldn't see)
   │
   ├── Stage 2 ── Code agent, prompted with the evidence bundle + instructions
   │              ("find the code path, config or recent change that explains this")
   │
   └── Stage 3: combine and rank evidence -> CopilotResponse (likely cause, confidence,
                evidence with sources, not-ruled-out, steps) -> front end
```

This follows the team's agreement: logs and docs run in parallel, then **their evidence becomes the code
agent's prompt** (Navya's suggestion). Agent outputs are **JSON** (Lantian's question), so they can be
appended and passed along.

## Routing table (current keyword version)

| Intent | Example | Stage 1 (parallel) | Stage 2 |
|---|---|---|---|
| `incident` | "Ads aren't loading, ad service returning errors" | logs, docs | code |
| `how_to` | "How do I trigger a failure in the ad service?" | docs | none |
| `code_lookup` | "Where is the ad failure flag checked in code?" | docs | code |
| `logs_lookup` | "Show me ad service logs since 14:00" | logs | none |
| `unknown` | anything else | logs, docs | code |

Debug any question's route with `GET /api/route?q=...`.

## Agent JSON contract (`schemas.AgentFinding`)

```json
{
  "agent": "logs",
  "status": "ok | no_evidence | error | no_access",
  "summary": "About 1 in 10 GetAds calls fail with UNAVAILABLE since 14:02.",
  "hypotheses": ["Ad service is rejecting a fixed share of requests"],
  "evidence": [
    {"source_type": "log | code | doc | incident | metric",
     "summary": "one human-readable sentence",
     "ref": "exact pointer: file:L10-L20, log query, doc section",
     "link": "deep link (GitHub line, Jaeger, doc URL)",
     "snippet": "raw lines (optional)"}
  ],
  "sources_searched": ["ad service logs"],
  "sources_unavailable": ["frontend-proxy access logs"],
  "confidence": 0.8,
  "latency_ms": 640
}
```

Every claim in the final answer must trace back to an `evidence` item (Objective 4: 100% source attribution).
`sources_unavailable` is what the UI shows as "Copilot couldn't see ..." (interview finding: show what the
tool could not access, and lower confidence when key sources are missing).

## How each real agent plugs in

Implement `async run(question, context) -> AgentFinding` and register it in `AGENTS` in `orchestrator.py`.
`context["evidence_bundle"]` is available to stage-2 agents.

| Agent | Owner | Current state | Plug-in plan |
|---|---|---|---|
| Logs | Lantian | Foundry model + custom log-search tools over local log files | Wrap as `LogsAgent`; later point at the shared store (SQL tables or Azure AI Search) |
| Docs | | Retrieval spike (docs + code search, cited) | `DocsAgent` over the OpenTelemetry demo docs and runbooks, indexed in Azure AI Search |
| Code | Navya | Design doc on Jira | `CodeAgent` takes the evidence bundle as its prompt and searches the repo / git history |

## Next steps

1. Replace `classify()` with an LLM call (Azure AI Foundry) that returns the same `RoutePlan` JSON;
   keep the keyword router as a fallback and for tests.
2. Replace hard-coded synthesis with an LLM step that writes the response from the merged findings only.
3. Confidence = combination of agent confidences, agreement between agents, and missing-source penalties.
4. Timeouts per agent (e.g. 20 s) so one slow agent doesn't block the answer; return partial results.
5. Routing test set: 20 prompts with expected agents (Objective 2, KR4: 90% correct routing).
