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
`orchestrator/.env` for model delegation and final compilation. The local-search
mode retains the Question analysis and Answer generation controls for debugging.
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
