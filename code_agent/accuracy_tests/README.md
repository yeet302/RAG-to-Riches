# Code Agent accuracy sprint — controlled bugs (no changes to Code Agent)

## What this does
- `test_regressions.py` provides **three real, deliberately failing tests** for three planted defects.
- `retriever.py` implements the existing `CodeRetriever` tool interface over local test source. **It changes only the data the agent reads**, not its prompt, model, tool list, tool-selection logic or response handling.
- `run_accuracy.py` instantiates the existing `CodeAgent` and invokes **real Azure Foundry** to investigate each bug, without providing the expected cause or exact fix to the agent.
- `outputs/BUG-XX.json` contains the original full `AgentFinding`. `outputs/accuracy_results.csv` is a **review worksheet**, not a pre-scored evaluation.

**Important boundary:** This is a real Foundry reasoning/tool-use test using controlled source, **not** a live GitHub / Azure AI Search ingestion test or a full three-agent orchestration test. The fixture retriever provides a basic lexical `search_code` rather than your production Azure Search index. The `fixture://...` evidence links are test-only identifiers, not real GitHub URLs. Do not use them to demonstrate production GitHub citation accuracy. The hard-restricted `open-telemetry/opentelemetry-demo` production configuration is unchanged.

## Setup (WSL, from RAG-to-Riches root)

```bash
git status
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c code-accuracy-sprint
# Extract the package into RAG-to-Riches/ so accuracy_tests/ is at the root.
source .venv/bin/activate   # or create with python3 -m venv .venv
python -m pip install -r code_agent/requirements.txt
az login
```

Check your ignored root `.env` includes `FOUNDRY_PROJECT_ENDPOINT`, `MODEL_DEPLOYMENT_NAME`, and `AZURE_SEARCH_ENDPOINT`. The existing `Settings.load()` requires them, even though Search is not queried during this controlled test. `DefaultAzureCredential` must have access to your Foundry project. Do not put credentials in this package or commit `.env`.

## 1. Prove the planted bugs are real

```bash
python -m unittest discover -s accuracy_tests -p 'test_retriever.py' -v
python -m unittest discover -s accuracy_tests -p 'test_regressions.py' -v
```

The first command should pass **2 tests**. The second should report **3 failures** — intentional, and evidence of the planted bugs. If any regression passes before fixing, investigate the fixture setup.

## 2. Ask the *real* agent to investigate (actual Foundry calls; can incur charges)

```bash
python accuracy_tests/run_accuracy.py --case BUG-01
python accuracy_tests/run_accuracy.py --case BUG-02
python accuracy_tests/run_accuracy.py --case BUG-03
```

Or run all three at once:

```bash
python accuracy_tests/run_accuracy.py --case all
```

Each invocation writes `outputs/BUG-XX.json` and rewrites `outputs/accuracy_results.csv` **with only the selected cases**. Use `--case all` for the final consolidated CSV. Do not call the model repeatedly merely to get a favorable answer; record every run as an independent trial. To retain repeats, rename outputs before the next run.

The agent gets only the symptom question and the test source; it does not get `expected_cause`, `expected_line`, or `reference_fix` from `cases.json`.

## 3. Review **each** answer manually

Check the JSON response's `status`, `summary`, `hypotheses`, `evidence[].ref`, `sources_searched`, and `sources_unavailable`.

- **Correct?** Mark `Yes` only if the agent identifies the actual faulty logic and cites the faulty file and line or an adjoining, explanatory range. A general hypothesis with no correct source-line evidence is **No** for this acceptance criterion.
- **Fix worked?** Mark **Not testable** if no specific actionable change was proposed. Otherwise apply only the **agent's actual suggested edit** to that fixture's source file and rerun the regression tests. Record `Yes` only if the targeted test changes from failing to passing and no other available regression breaks. If the agent's proposed edit fails the test, mark `No`, not the outcome after you independently correct it.
- Record elapsed seconds from the CSV. Verify every source claim against the code. If model status is error, record it and don't fabricate a result.

After manually applying a proposed fix, run:

```bash
python -m unittest discover -s accuracy_tests -p 'test_regressions.py' -v
```

The targeted case should now pass; other cases will still fail until repaired. **Do not change all test fixtures to the reference fixes before evaluating the agent.** Keep the original planted versions in Git so you can reset between trials.

## 4. Enter results into the team's shared accuracy sheet

Transfer `bug`, `expected_cause`, `agent_answer`, your reviewed `correct?`, `fix_worked?`, and `time_taken_seconds`. Add fixture version, citation, suggested edit, and failure reason in notes where possible. A CSV produced here **does not upload itself to the team's shared sheet**.

## Verify the harness is not modifying your actual agent

```bash
git diff -- code_agent/
```

It should be empty if you have made no independent changes. The test harness imports `CodeAgent` and injects a `retriever` instance through the already-existing constructor parameter.
