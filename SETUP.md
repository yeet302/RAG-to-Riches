# Run main locally: short setup

The checklist for running the integrated app from `main`: the orchestrator, logs, docs and code
agents, and the web UI. Commands are PowerShell from the repository root; Git Bash/macOS use the
same commands with `/` paths and `.venv/bin/python`. Full details: [orchestrator/INTEGRATION.md](orchestrator/INTEGRATION.md).

## 1. Get the latest main

```powershell
git checkout main
git pull                      # always update from GitHub; never merge a stale local main
```

## 2. Install (first time, or when requirements change)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r orchestrator\requirements-dev.txt
cd frontend; npm install; cd ..
```

## 3. Four `.env` files (never commit them)

Copy each `.env.example` to `.env` in the same folder and fill it in:

| File | Must have |
|---|---|
| `orchestrator/.env` | `ORCHESTRATOR_MODE=remote`; `LOGS_AGENT_URL=http://127.0.0.1:8101/agents/logs/run`, `DOCS_AGENT_URL=http://127.0.0.1:8102/agents/docs/run`, `CODE_AGENT_URL=http://127.0.0.1:8103/agents/code/run`; `AZURE_AI_ENDPOINT`, `AZURE_AI_MODEL` and `AZURE_AI_AUTH_MODE=entra` (or `AZURE_AI_API_KEY`) |
| `code_agent/.env` | `FOUNDRY_PROJECT_ENDPOINT`, `MODEL_DEPLOYMENT_NAME`, `AZURE_SEARCH_ENDPOINT`; `CODE_REPO=yeet302/RAG-to-Riches`; `CODE_REF` = the commit the search index was built from |
| `logs_agent/.env` | `LOG_CONNECTOR=azure`, `AZURE_LOG_ANALYTICS_WORKSPACE_ID`, `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_AGENT_NAME` (the logs agent's own settings, not the orchestrator's) |
| `docs_agent/.env` | `AZURE_AI_ENDPOINT`, `AZURE_AI_MODEL`, and `AZURE_AI_API_KEY` or `AZURE_AI_AUTH_MODE=entra` |

## 4. Sign in to Azure in the terminal that starts the services

The orchestrator and the logs and code agents sign in as you (`az login`), in that terminal only.

```powershell
$env:Path += ";C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin"   # only if `az` is not found
az login
```

Your account needs access to each Foundry project, the code search index and the Log Analytics
workspace (ask the owner of each resource if a check below reports 401/403).

## 5. Check before starting

```powershell
.\.venv\Scripts\python.exe orchestrator\check_setup.py --deep
```

Fix every `FAIL`. `--deep` builds the code agent the same way its service does on the first
question, which is the step that fails with "Code Agent is not configured".

## 6. Start

```powershell
# Stop services left over from an earlier run (run_local.py reuses whatever is already running).
Get-NetTCPConnection -LocalPort 8000,8101,8102,8103 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
.\.venv\Scripts\python.exe orchestrator\run_local.py
```

In a second terminal: `cd frontend; npm run dev`, then open http://localhost:5173.

## 7. Confirm the code agent works

Ask: *Why do some GetAds requests fail with UNAVAILABLE errors in the Ad Service?* The progress
panel should show the code agent finishing with evidence (a file under `src/ad/`). Each service's
log is in `diagnostics/local-run/<time>/` (for example `code.log`).

## If the code agent fails (for example "Code Agent is not configured", CODE_NOT_CONFIGURED)

The code agent's `/health` only shows the process is up; its settings are first used on a question.
Run `check_setup.py --deep`, then look at `diagnostics/local-run/<time>/code.log`:

| In `code.log` / the check | Fix |
|---|---|
| `Missing environment variables: ...` | Add them to `code_agent/.env` (step 3). |
| `Only yeet302/RAG-to-Riches is approved` | `CODE_REPO=yeet302/RAG-to-Riches` (or delete the line). |
| `SyntaxError` with `<<<<<<<` | A merge left conflict markers. Resolve them, or `git checkout origin/main -- <file>`. |
| `ModuleNotFoundError` | Step 2 (`pip install -r orchestrator\requirements-dev.txt`) in the same `.venv`. |
| `DefaultAzureCredential failed` / `az` not found | Step 4 in the terminal that runs `run_local.py`, then restart it. |

Other symptoms: **405 on `/api/ask/stream`** = an old orchestrator is still running (step 6 stops
it). **Code agent returns no evidence** = the search index wasn't built for `CODE_REF`; ask Navya,
or run `python -m code_agent.index_repo` with the same `.env`.
