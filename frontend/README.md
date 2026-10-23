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
The incident list is empty outside demo mode. Logs are unavailable until
LOGS_ROOT or LOGS_AGENT_URL is configured. Use the question box even when the
incident list is empty.

Use **Question analysis** to select Keywords or Azure AI. Azure AI becomes available
after the backend is configured with its endpoint, deployment, and key.
**Answer generation** defaults to AI explanation when configured; choose Evidence
list to use the formatter. The controls are independent. See
[optional Azure setup](../orchestrator/LLM_ROUTING.md). The credential stays on the backend.

The header reads the actual mode from /api/health, and incident responses show
the returned answer even when no root cause has been established.

To build the frontend, run npm run build in this directory. After restarting,
the orchestrator can serve that build directly at http://127.0.0.1:8000.
