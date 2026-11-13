# Frontend with the orchestrator

This frontend was imported from origin/frontend into the local orchestrator
checkout. Run both processes in WSL from the repository root.

In terminal 1, follow the [backend quick start](../orchestrator/README.md#quick-start).
It starts Uvicorn with --env-file .env, so local Azure configuration is loaded.

In terminal 2, with Node.js and npm available:

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --strictPort
```

Open http://127.0.0.1:5173. Vite forwards /api requests to the orchestrator on
port 8000. Leave VITE_API_URL unset to use this proxy.

Ask **How does the orchestrator route requests?** to test docs search. Ask
**Why is checkout returning timeout errors?** to exercise the incident route and
inspect **How Copilot investigated** below the response.

Local mode searches the configured files; it does not provide canned diagnoses.
The incident list is empty until a real incident source is connected. Logs are unavailable until
LOGS_ROOT or LOGS_AGENT_URL is configured. Use the question box even when the
incident list is empty.

In remote integration mode, the browser lets the orchestrator choose the planning
and answer modes. Configure `ROUTING_MODE=llm` and `ANSWER_MODE=llm` in
`orchestrator/.env` for model delegation and final compilation. The browser uses
the server routing and answer settings in both local and remote modes; configure
those settings in `orchestrator/.env`.
The header reads the actual mode from `/api/health`. Each response shows the model's
assignments, specialist execution trace, evidence, and any planning or answer fallback.
Azure credentials stay on the backend. See [Azure setup](../orchestrator/LLM_ROUTING.md).

To build the frontend, run npm run build in this directory. After restarting,
the orchestrator can serve that build directly at http://127.0.0.1:8000.


## Step review and routing display

The What to try panel has Previous step and Next step buttons for reviewing the
source-backed suggestions. Navigation does not submit feedback or change recorded
outcomes. Yes/No remains a separate action; recorded results are retained when
moving backward, including after success or after all steps fail.

The performance panel shows an Agent routing diagram from the response's actual
stage assignments. Agents within one stage are grouped as parallel; Stage 2 is
shown after Stage 1 only when the plan includes it. Nodes show measured durations,
returned source counts, and the slowest-agent marker. The same diagram supports
single-agent fast-path requests and stacks vertically on small screens.

Optional browser-only regression check (requires the existing Playwright setup):

```bash
cd orchestrator
.venv/bin/python tests/browser_step_navigation.py
```

This uses controlled API fixtures, checks navigation/outcome preservation and
staged/parallel/single-agent layouts, and saves desktop/mobile screenshots under
`diagnostics/step-navigation/`. It makes no model calls or real feedback updates.


Long evidence summaries, references, answers, and commands wrap within their cards.
Expanded source excerpts retain line breaks and use a bounded, keyboard-focusable
scroll area. The browser regression includes long unbroken strings and paths at
desktop (1280px) and mobile (390px) widths.


Narrative answers display exact known source references as compact numbered citation
buttons. Selecting a number reveals the full source, expands hidden evidence when
needed, opens the excerpt, and focuses the selected source. Full references remain
in Sources; stored answers and exports remain unchanged. Unknown bracketed text and
literal log labels are displayed as supplied. Paragraph breaks are retained.


## Example investigations and saved results

The home-page buttons query the connected OpenTelemetry Demo sources: Ad Service
build documentation, GetAds implementation, recent warning logs, and an UNAVAILABLE
investigation using logs, docs, and code. The first three are single-source fast
paths; the incident prompt uses semantic planning (keyword fallback selects logs
and docs, then code). Actual evidence depends on available sources and access.

Returning home or refreshing clears the browser conversation, not saved Accuracy
results. Completed investigation responses and reviews persist in the backend
SQLite file, normally orchestrator/results/accuracy.sqlite3; ACCURACY_DB_PATH can
override its location. Restarting the server preserves that file. The tab shows
the latest 100 runs and Download CSV exports all stored runs. Exports are separate
downloaded files. Step Yes/No feedback is currently in server memory and is lost
on restart; use the saved Accuracy review for a durable fix outcome.

### Live investigation progress

Queries use `POST /api/ask/stream`, an NDJSON response carrying `progress` events
for planning, selected specialists, individual specialist starts/completions, and
answer preparation, followed by a terminal `result` or `error` event. The original
`POST /api/ask` JSON endpoint remains available for other clients.

The loading card displays completed stages, agent states, elapsed time, and
animated accents (disabled with reduced-motion preferences). Stage completion
measures workflow milestones, not predicted time remaining. Specialist failures
and empty results appear explicitly while the investigation continues.
Restart the orchestrator and rebuild the frontend together when deploying this change.

### Questions beside the composer

Starter questions cover documentation, source code, logs, and a full investigation.
After an answer, the frontend independently calls `POST /api/suggestions` with a
bounded question, answer, source gaps, and up to three recent questions. The
orchestrator uses its configured Azure model to generate up to three self-contained
follow-ups. Suggestion generation has an eight-second deadline and does not delay
or invalidate the investigation answer. If the model is unavailable, the composer
stays usable and shows that suggestions are unavailable. Starting a new query or
returning home invalidates older suggestion requests.
