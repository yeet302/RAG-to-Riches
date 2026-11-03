# Validation results - November 2, 2026

Implemented full investigation timing, slowest-step reporting and recommendations, direct code/log fallback routing, frontend timing display, and repeatable CSV evaluation. No environment files were changed.

## Checks

- Baseline: two collection errors because Beautiful Soup was missing from the test environment. Installed declared service dependencies and included them in requirements-dev.txt.
- Initial implementation: 197 tests passed in 1.69 seconds.
- Final implementation: 200 tests passed in 1.43 seconds, including actual specialist HTTP hosts with mocked external dependencies, authentication, evidence handoff, partial failures, timing boundaries, and evaluation recording.
- Frontend TypeScript check and Vite production build passed.
- 24 offline fixture investigations passed routing/evidence checks across two rounds. These are synthetic fixtures, not production diagnosis accuracy.
- Eight configured-service investigations recorded: four with endpoints unavailable, followed by four after temporarily starting the configured local services. The temporary processes were stopped.
- All four questions selected the expected roles in both configured-service rounds. This validates selection for these cases, not general routing accuracy.

## Final configured-service measurements

| Case | Total | Slowest step | Step duration | Outcome |
|---|---:|---|---:|---|
| CODE-LOOKUP | 35.94 s | agent:code | 32.38 s | failed |
| DOCS-LOOKUP | 7.71 s | agent:docs | 4.48 s | partial |
| LOGS-LOOKUP | 15.76 s | agent:logs | 12.00 s | no_evidence |
| CHECKOUT-TIMEOUT | 52.14 s | agent:code | 27.68 s | partial |

The code agent was the largest measured bottleneck: 32.38 seconds on direct lookup and 27.68 seconds in the incident investigation. Routing took 3.23-6.53 seconds after services started. Logs took about 12-13 seconds. The incident answer-generation step took 5.03 seconds. Combining evidence and handoff were below a millisecond. Concurrent logs/docs timings overlap; do not add them as sequential costs. Measurements cover investigate(), excluding HTTP serialization and browser/network overhead.

## Speedup priorities

1. Profile the code specialist retrieval/model/validation internals. The direct lookup returned CODE_MODEL_RESPONSE_FAILED after 32.38 seconds. Inspect its model JSON and citation validation using the CSV request ID before adding retries; retries could increase latency.
2. Bound code search to relevant symbols, service, and repository revision, and reduce unnecessary retrieved context. Compare a faster specialist model while measuring accuracy on known bugs.
3. Reduce planning prompt/model latency. Use the recorded plain-language cases to compare a faster planning deployment or keyword mode before changing defaults. Semantic routing remains available and configured settings were preserved.
4. Retain concurrent independent calls. Clear fallback code lookups now skip docs; explicit log retrieval skips docs/code. Use staged execution when later retrieval needs earlier evidence.
5. Bound synthesis context and output length, then compare a faster synthesis model while preserving grounded citations. No current evidence shows local merge work needs optimization.

## Accuracy and remaining work

- The direct code lookup did not produce a verified answer (CODE_MODEL_RESPONSE_FAILED).
- The log lookup returned no matching evidence. This does not establish a service incident or an access failure.
- Docs and the incident investigation returned partial coverage. Their answers need human review against known source data.
- None of the runs applied a remediation. Fix worked remains Not tested; no production fix success is claimed.
- Current sample questions are routing/integration cases. Production diagnosis accuracy still requires known bugs with independently verified expected causes and a reproduce/fix/retest procedure.
- The user confirmed the shared accuracy sheet is not ready. accuracy_results.csv is the local record, ready for import when that sheet exists. No shared-sheet writes were performed.

The CSV contains 432 rows: 24 fixture investigations, 8 configured-service investigations, 399 automated test outcomes (including initial failures), and 1 frontend build. Automated checks and live answers are labeled separately. Failed initial runs were preserved.

## Repeat

From orchestrator/:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python evaluate.py --fixture --repeat 3 --output results/accuracy_results.csv
.venv/bin/python evaluate.py --env-file .env --output results/accuracy_results.csv
```

The configured-service command requires running services. Use --cases cases.json for team-owned known bugs. Each case supplies bug, question, expected_cause, and expected_agents. Review correct? and fix worked? only after independent verification.
