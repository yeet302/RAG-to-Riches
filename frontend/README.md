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
