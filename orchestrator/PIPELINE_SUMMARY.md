# Orchestrator pipeline and demo guide

## 1. Project goal and current status

The intended orchestrator will understand a question, plan an investigation,
delegate specific JSON assignments to AI specialists, and generate a detailed,
cited answer from their findings. Each specialist owns its model, tools, and data
connections; the orchestrator knows its capabilities and coordinates the work.

| Role | Intended capability |
|---|---|
| Docs | Documentation, runbooks, known procedures, and past incidents. |
| Logs | Operational errors, events, traces, and supported observability data. |
| Codebase (code in JSON) | Implementation, configuration, and changes supported by its tools. |

```text
Target: question -> AI plan -> JSON assignments -> specialist models/data sources
        -> validated findings -> AI synthesis -> detailed answer
```

The current implementation supports Azure-generated JSON delegation plans,
question-specific specialist requests, validated JSON findings, local retrieval,
and evidence-grounded Azure answer generation. Actual teammate model services
still need their endpoints configured and verified.

```text
Current: frontend or terminal question
         -> keyword rules OR Azure JSON assignments (role, stage, question, task)
         -> Python validates the plan and runs its stages
         -> local searches or JSON requests to configured specialist services
         -> validated evidence and partial-result handling
         -> Azure writes a cited explanation, or Python returns the evidence list
         -> frontend or terminal
```

The planner generates focused questions and tasks for up to three unique roles
across two stages. A separate model call explains returned evidence and cites its
source IDs. Python controls execution, validates data/citations, and retains
keyword/evidence fallbacks. Planning and answering default to Azure when configured.
The planner does not run code or access specialist tools itself.
See [Azure setup](LLM_ROUTING.md) for the two independent controls and verification.

## 2. What the current implementation demonstrates

| Implemented feature | Behavior |
|---|---|
| Frontend request/response flow | Submit a question and display the backend's answer and sources. |
| Input validation | Reject empty questions and text over 2,000 characters. |
| Keyword or Azure planning | Generate role-specific assignments and expose the plan and its source. |
| Local retrieval | Search supported files and return excerpts with file/line references. |
| Staged execution | Run independent first-stage work together, then code with earlier evidence. |
| Evidence handling | Validate findings, preserve attribution, and remove duplicate evidence. |
| Failure handling | Report unavailable roles/sources while retaining other usable findings. |
| Structured output | Return answer, evidence, findings, IDs, analysis/synthesis metadata, and execution trace. |
| AI explanation | Generate a grounded answer from retrieved evidence with validated citation IDs. |
| Terminal interface | Run the same investigation from chat.py. |
| Integration scaffolding | Shared JSON models, HTTP adapters, and a local reference service. |

In the default local configuration, docs and code search the orchestrator directory.
Logs is unavailable until configured. These implementations are local searches,
not the AI pipelines developed by teammates. The incident list is empty outside
explicit canned demo mode.

## 3. Which code does what

| Step | Code | Responsibility |
|---|---|---|
| Submit a question | [App.tsx](../frontend/src/App.tsx): ask() | Submission, analysis-mode choice, loading state, and result storage. |
| Send JSON | [api.ts](../frontend/src/api.ts): api.ask() | POST question, optional IDs, and routing_mode. |
| Proxy development requests | [vite.config.ts](../frontend/vite.config.ts) | Forward /api to backend port 8000. |
| Receive and validate | [main.py](main.py): ask() | Handle /api/ask, check text, and invoke investigate(). |
| Define data | [schemas.py](schemas.py) | Request, finding, evidence, analysis, trace, and response models. |
| Coordinate execution | [orchestrator.py](orchestrator.py): investigate() | Analyze, run stages, and record request metadata and trace. |
| Analyze intent | [query_analysis.py](query_analysis.py): analyze_question() | Select keywords or Azure, validate model output, and fall back visibly. |
| Match keywords | [router.py](router.py): classify(), _has() | Apply ordered word/phrase rules and return a RoutePlan. |
| Load configuration | [settings.py](settings.py): Settings.from_env() | Mode, directories, URLs, tokens, Azure settings, and deadlines. |
| Select implementations | [agents.py](agents.py): build_agents() | Choose local search, HTTP connection, or unavailable implementation per role. |
| Retrieve local evidence | [agents.py](agents.py): terms(), LocalSearchAgent | Extract terms, search eligible files, score lines, and return findings. |
| Bound and validate calls | [orchestrator.py](orchestrator.py): call_agent() | Timeout, identity/schema checks, attribution, error conversion, and timing. |
| Build internal handoff | [orchestrator.py](orchestrator.py): merge_findings() | Combine successful first-stage content plus status and source gaps. |
| Assemble evidence | [orchestrator.py](orchestrator.py): synthesize() | Deduplicate evidence, build the fallback text, and mark partial results. |
| Generate final answer | [answer_synthesis.py](answer_synthesis.py): generate_answer() | Send bounded evidence to Azure, validate citations, and retain fallback on failure. |
| Call the deployment | [azure_client.py](azure_client.py): complete_json() | Shared JSON transport for intent analysis and answer synthesis. |
| Display results | [Thread.tsx](../frontend/src/components/Thread.tsx) and its cards | Show answer, sources, analysis, warnings, and trace. |
| Terminal entry | [chat.py](chat.py): chat() | Read questions, invoke investigate(), and print answers. |

SETTINGS and AGENTS are created when orchestrator.py is imported. Restart after
changing configuration. main.py and chat.py share the investigation function.

## 4. Routing and execution

### Keyword routes (also used as fallback)

| Intent | Example in keyword mode | First stage | Second stage |
|---|---|---|---|
| how_to | How do I restart checkout? | Docs | None |
| code_lookup | Where is the retry function defined? | Docs | Code |
| logs_lookup | Show checkout logs since noon | Logs | None |
| incident | Why is checkout returning errors? | Logs and docs together | Code |
| unknown | Investigate checkout | Logs and docs together | Code |

Selective routing chooses roles; asyncio.gather() runs independent calls
concurrently within each stage. The existing stages are shared by local and HTTP
implementations. Azure can choose different role combinations and stages, with
at most one assignment per role, two stages, and at least one first-stage task.
Every second-stage assignment receives the successful first-stage evidence and
all status gaps. Generated tasks cannot choose service endpoints or credentials.

### Keyword rule priority

router.py lowercases the input and uses word-boundary matching. First match wins:

1. How-to wording plus trigger/inject/simulate/reproduce, without code terms: docs.
2. Incident words such as errors, down, timeout, cpu, or why: full incident route.
3. Code words such as function, file, config, commit, or repository: docs then code.
4. How-to wording without log terms: docs.
5. Mixed how-to and log terms: full incident route.
6. Log terms alone: logs.
7. No match: full route.

Thus "How do I fix checkout errors?" takes the incident route, "How do I simulate
errors?" can use docs, and "Explain the retry function" includes code.
These are heuristics; [Azure mode](LLM_ROUTING.md) provides semantic intent analysis.
GET /api/route previews the selected analysis without running searches.

### Validation, timing, and handoff

- Calls have a configurable deadline, default 20 seconds per agent. First-stage
  calls overlap; a second stage adds another wait. The model-analysis deadline is
  separate and defaults to 10 seconds.
- Findings must match the requested specialist identity. Omitted evidence agent
  names are filled in; conflicting identities invalidate the finding.
- An ok finding without evidence becomes no_evidence. Error/no_access findings
  cannot contribute claims.
- merge_findings() passes successful summaries, hypotheses, and evidence to code,
  together with every first-stage status and missing-source information.
- synthesize() deduplicates by source_type, ref, and link. Missing sources or failed
  roles mark the response partial; a completed empty search alone does not.

The common interface is async run(question, context) -> AgentFinding.
LocalSearchAgent retrieves files, HTTPAgent calls a configured specialist, and
UnavailableAgent reports missing configuration. DemoAgent returns prepared findings
only in explicit demo mode. The reference HTTP host in
[agent_service.py](agent_service.py) always hosts local searches and does not proxy
remote URLs. Exact wire formats belong in [COMMUNICATION.md](COMMUNICATION.md).

## 5. Local retrieval

For "Why is checkout returning timeout errors?", terms include checkout, returning,
timeout, and error. Common words are removed and selected variants normalized.

1. Search supported documentation, log, or source/configuration file extensions.
2. Add 3 points for each question term in a line and 1 for each term in its path.
   Code can gain up to 2 additional points from earlier evidence terms.
3. Require a question-term match in the line or path. Halve documentation heading
   scores to favor explanatory text; skip JSON question examples in docs.
4. Keep one best line per matching file and return up to four files per role.
5. Include the line and up to two following lines, with an exact file/line reference.

Summaries are capped at 300 characters; excerpts at 1,200. Searches are bounded by
500 eligible files, a deadline, and a 1 MB per-file limit. Hidden entries, symlinks,
selected generated/dependency/test directories, and demo_data.py are skipped.
Truncation and unreadable supported sources are reported as gaps. Filesystem work
runs in a worker thread rather than blocking the API event loop.

This is lexical retrieval of current files, not vector search, git-history
analysis, whole-function extraction, or diagnosis. Relevance scores are not
root-cause probabilities; live likely_cause and diagnosis confidence remain unset.

## 6. Request state and observability

State is per request: question, analysis/route, IDs, evidence bundle, findings, and
trace. A fresh request_id identifies each investigation. Incident and conversation
IDs are correlation metadata, not authentication or stored conversation history.

The trace records routing, role actions, parallel stages, and measured timing.
Agent status and latency are logged with request_id. The ask handler does not log
the user's question or remote error bodies. Raw provider exceptions are not put in
error responses. The model analyzer logs failure type rather than credentials.

POST /api/feedback stores feedback in memory; it is lost on restart and does not
train the model or change routing. Durable history, caching, enterprise access
control, stored audits, and monitoring infrastructure are future work.

## 7. Integration boundary and next sprint

Existing AgentRequest/AgentFinding models, HTTPAgent, internal evidence bundles,
and reference HTTP tests provide scaffolding. They do not demonstrate completed
handoffs with teammates' actual models and databases.

Planned next work:

1. Agree on the shared request/finding contract with specialist developers.
2. Connect their services and validate real requests, findings, and source references.
3. Evaluate the generated plans and assignments against real specialist capabilities.
4. Verify dependent tasks use returned evidence and identify contradictions/gaps.
5. Evaluate and improve the grounded answer synthesizer against real specialist findings.
6. Test the full frontend -> orchestrator -> specialist services -> frontend flow.

See the [shared contract](COMMUNICATION.md) and
[docs-specific onboarding](DOCS_AGENT_HANDOFF.md). Specialists own retrieval and
their model/database connections; the orchestrator owns coordination and synthesis.

## 8. Demo checklist

Start both processes using the [backend quick start](README.md#quick-start) and
[frontend instructions](../frontend/README.md). Open http://127.0.0.1:5173 and refresh
after backend configuration changes. Use local specialist mode with default
directories and no remote role URLs for these demonstrations.

### Last pushed backend: keyword demonstration

The last pushed orchestrator commit is 1537f8c, "Connect orchestrator to logs, docs,
and code agents." It adds actual local retrieval, failure handling, terminal chat,
and integration scaffolding. The API, keyword router, parallel-stage structure, and
trace already existed. The following actions use the local frontend to show it.

Select **Keywords** and **Answer generation: Evidence list** to reproduce the pushed backend, then:

| Action | What to show | What it proves |
|---|---|---|
| Open /api/health on port 5173 | local mode; docs/code are LocalSearchAgent; logs is UnavailableAgent. | Frontend proxy reaches the backend; identifies configured implementations. |
| Ask "How does the orchestrator route requests?" | Sources and docs-only trace; open a cited file in the editor. | Retrieves real file content rather than prepared findings. |
| Ask "Where is classify defined in the code?" | Docs in stage 1, code in stage 2, and code references. | Existing staged flow runs real local retrieval. |
| Ask "Why is checkout returning timeout errors?" | Logs unavailable, remaining evidence, and partial response. | An unavailable role does not stop the request. |
| F12 -> Network -> ask | POST question/IDs; response answer, evidence, findings, trace, request_id, partial, and demo=false. | Structured frontend/backend exchange. |

The default files are this repository, not a real checkout deployment. The incident
question demonstrates pipeline behavior, not a checkout diagnosis. Exact retrieved
excerpts may change as documentation changes. The UI does not show an order sent
to a teammate service because none is connected.

### Current local addition: Azure demonstration

1. With Answer generation set to Evidence list, ask **Which component implements
   retry handling?** with Keywords selected, then repeat with Azure AI selected.
2. Expand Question analysis and How Copilot investigated to compare routing.
3. Change Answer generation to AI explanation and ask **How does the orchestrator
   route requests?** Show the explanation, citations, and AI answer label.
4. In Network -> ask -> Response, verify analysis.source=llm for model routing
   and synthesis.source=llm for model-written answers. These are independent.
   Configuration availability alone is not proof of either model call.

In a verified live test, keywords chose unknown (logs/docs -> code), while Azure
chose code_lookup (docs -> code). Model decisions can vary. The API key stays in
the ignored backend .env. This feature is local work beyond the last push.

**Say:** "Azure can interpret intent and write an explanation from collected evidence.
Python controls execution, validates results and citations, and handles fallback.
Azure now generates JSON specialist assignments; actual teammate endpoints still
need to be connected and evaluated."

### Optional checks

- At http://127.0.0.1:8000/docs, use POST /api/ask -> Try it out with
  {"question": "   "}. Expect HTTP 400. The frontend blocks empty input first.
  Validation predates the last push; show it as a pipeline check.
- From orchestrator/, run .venv/bin/python -m pytest -q. Tests cover local retrieval,
  reference HTTP communication, invalid plans, parallel generated assignments,
  JSON specialist handoff, final synthesis, and configuration compatibility.
  Automated Azure calls use mock transports; live validation is separate.

**Closing statement:** "We moved from prepared findings to working local retrieval,
structured results, and failure handling. Azure can now analyze intent and construct
a cited explanation from the evidence. Next we will connect the actual specialist
services and evaluate the generated task plans against their real capabilities."
