# Demonstration verification - November 2, 2026

Open http://127.0.0.1:8010. The offline walkthrough is running with checked-in sources and no Azure calls. Existing environment files were preserved.

## What you can show

- Plain-language question entry with automatic specialist selection.
- Actual file/line evidence; the code-location example ranks async def investigate first.
- Measured step timings, highlighted slowest step, and speedup suggestions.
- Automatic durable recording of every API investigation, including failures.
- Human review of bug, expected cause, correctness, fix outcome, and notes.
- Downloadable CSV with all six required columns.
- Results retained after refresh; review audit history retained in SQLite.

## Verification

207 backend/integration tests passed in 2.13 seconds. TypeScript and production build passed. The final browser walkthrough passed all four questions, CSV download, review/reload persistence, and mobile layout without browser errors.

| Question | Automatically selected | Measured total | Slowest |
|---|---|---:|---|
| Where is the investigate function defined? | code | 11.965 ms | agent:code |
| How does the orchestrator route requests? | docs | 8.114 ms | agent:docs |
| Show ad service warning logs | logs | 22.683 ms | agent:logs |
| The ad service is returning errors. Why? | logs, docs, code | 24.347 ms | agent:docs |

These timings measure local source search, not remote model performance. A partial result from bundled logs reflects local search coverage; it does not prove a live incident cause. Correctness stays Not reviewed and fix outcome stays Not tested until independently verified.

Initial browser verification exposed missing Chromium libraries and an ambiguous text-area accessible name; both were addressed. A concurrent frontend build temporarily removed dist/assets during backend test collection; the suite passed when rerun after the build. These verification failures are preserved in accuracy_results.csv.

The interactive log currently has 16 walkthrough records. results/showcase_accuracy_results.csv is a snapshot of the app export with the required sheet columns. results/accuracy_results.csv also preserves the prior experiments and every automated verification result.

## Artifacts

- Presentation script: ../DEMO.md
- Current app export: /api/accuracy.csv
- Desktop investigation screenshot: ../../diagnostics/showcase/investigation-desktop.png
- Desktop accuracy review screenshot: ../../diagnostics/showcase/accuracy-desktop.png
- Mobile screenshot: ../../diagnostics/showcase/accuracy-mobile.png
- Repeatable browser check: ../tests/browser_showcase.py

The shared sheet is not ready. No remote sheet writes are claimed. Download CSV and import it when the shared sheet becomes available.


## Integration onto the orchestrator branch

The sprint changes were applied on top of origin/orchestrator f35f645, preserving
its confidence-score policy and agent-provided solution features. The merge
retains confidence computation inside measured finalization and includes both
confidence/solution styles and the new timing/results styles.

Verification: 234 orchestrator tests passed in 3.02 seconds and 30 code-agent tests
passed in 0.82 seconds. TypeScript and Vite build passed. A temporary local server
on port 8011 passed the full browser walkthrough, including all four routes,
source evidence, timings, review persistence, CSV download, and mobile viewport.
The temporary test server was stopped; existing user services were not stopped.
All check outcomes and four investigation results were recorded locally.

The main-branch sprint push remains undone by b5f11d8. This integrated work targets
origin/orchestrator. Generated accuracy CSVs, SQLite data, and environment files
are excluded from the push and remain local.
