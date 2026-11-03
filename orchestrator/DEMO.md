# Two-minute demo: plain issue to reviewed accuracy result

## Open the app

Open http://127.0.0.1:8010. The offline walkthrough searches actual checked-in
orchestrator documentation/code and bundled historical log files. The blue source
notice identifies this clearly. No Azure calls or prepared diagnoses are used.
It demonstrates routing, source evidence, measured execution, and result recording;
it does not establish live production diagnosis accuracy.

To start it again, from the repository root in WSL:

```bash
cd frontend
npm run build
cd ../orchestrator
.venv/bin/python showcase.py --port 8010
```

The launcher leaves existing .env files untouched. Normal service integration
continues to use the usual main:app launch command with your configured .env.

## Present the workflow

1. Click **Where is the investigate function defined?** or type it yourself.
   Say: "I describe what I need. I do not name any agents."
2. Point to **Automatically selected: code**. Then show **Total time**, **Slowest
   step**, and the per-step timing bars. Expand **Ideas to speed up the slowest
   step**. The displayed measurements come from this request.
3. Show the answer's file/line evidence. Then try **How does the orchestrator route
   requests?** to demonstrate docs-only selection. Try **Show ad service warning
   logs** for logs-only selection. The error-investigation example selects
   logs/docs together, then code with their evidence.
4. Open **Accuracy results**. Every submitted investigation is already saved,
   including failures. The table has the six required columns: bug, expected cause,
   agent answer, correct?, fix worked?, and time taken.
5. Click a case name. Enter a bug identifier and an independently verified expected
   cause. Review the answer. Set **Correct?** only after checking the evidence;
   leave **Fix worked? = Not tested** until a remediation has actually been tried.
   Click **Save review**, then refresh to demonstrate persistence.
6. Click **Download CSV**. Open it in a spreadsheet or import it into the shared
   accuracy results sheet once that sheet is available.

Suggested narration: "The response, selected agents, and timing are captured
without manual copying. Human correctness and fix verification remain explicit
review decisions. Each row is linked to the original request."

## What is persisted

Browser/API investigations are stored in `results/accuracy.sqlite3`, including the
full original response, errors, agent findings, and performance report. Human
reviews are saved with an audit history. The database is local and gitignored.
`GET /api/accuracy.csv` exports all those investigations with current reviews.
The UI shows the newest 100; export includes all saved rows.

Earlier command-line experiments and automated-test outcomes remain in
`results/accuracy_results.csv`. They are preserved separately from the interactive
review log. The shared sheet is not yet ready, so no remote sheet update is claimed.
Use `ACCURACY_DB_PATH` to select a different local database when needed.

## Verification

```bash
.venv/bin/python -m pytest -q
```

Optional browser check (against a running showcase):

```bash
.venv/bin/python -m pip install playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python tests/browser_showcase.py
```

The browser check submits all four example questions, verifies automatic routing,
checks timing display and evidence, saves a review, downloads the CSV, reloads to
verify persistence, and checks a mobile viewport. It leaves clearly identified
walkthrough records in the app and writes screenshots to `diagnostics/showcase/`.

Live Azure runs previously exposed a code-model response failure and incomplete
source coverage. This offline walkthrough is a reliable demonstration of the
integrated workflow; use known bugs and independently verified causes to measure
production answer accuracy. See `results/VALIDATION.md` for prior live measurements.
