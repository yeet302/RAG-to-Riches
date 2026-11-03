# Code model JSON handoff fix - November 2, 2026

Observed failure: CODE_MODEL_RESPONSE_FAILED with JSONDecodeError while parsing the
model answer. This was not an HTTP connectivity failure. A live pre-final-fix check
also returned malformed JSON (12,961 characters, error offset 1,286).

Changes in code_agent/agent.py:
- Request strict JSON-schema output in the Foundry prompt-agent definition.
- Explicitly reference the version created with that schema.
- Extract only the final assistant message rather than aggregated output text.
- Validate the full result locally before accepting findings.
- Preserve exact retrieved commit/line citation checks.
- Distinguish empty output, malformed JSON, schema mismatch, incomplete responses,
  refusals, and unfinished tool calls with safe diagnostics.
- Correct non-permission Foundry errors so they do not suggest an access grant.

Foundry accepted schema configuration on the agent definition but rejected the
response-level text override with HTTP 400 invalid_payload. That override was
removed; this unsuccessful live attempt is retained in accuracy_results.csv.

Final verification:
- 30 code-agent tests passed in 0.82 seconds.
- 207 orchestrator tests passed in 1.92 seconds.
- Live request 2f02c10c-8b55-46d9-8aa5-400e91f0f732 completed in 24,419.004 ms,
  status ok, one source-verified citation, no error.
- The successful live question asked about GetAds and UNAVAILABLE in the configured
  OpenTelemetry repository. It did not claim to establish the cause of a live incident.

All test and live attempt outcomes were appended to accuracy_results.csv. The shared
sheet is still pending. Fix success is limited to JSON handoff/citation validation;
root-cause correctness and incident remediation remain unreviewed.

Restart the existing code-agent server with Ctrl+C in its terminal, then run from
the repository root:

```bash
orchestrator/.venv/bin/python -m uvicorn code_agent.service:app --env-file code_agent/.env --host 127.0.0.1 --port 8103
```

The user's running servers were not stopped or replaced. Existing environment
files were preserved. Test the live service with: Where is GetAds implemented,
and what can make it return UNAVAILABLE?

Structured-output reference consulted:
https://developers.openai.com/api/docs/guides/structured-outputs
