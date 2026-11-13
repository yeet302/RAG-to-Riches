# Subagent change record

Record maintained for the specialist developers and future integration work.
For every future change inside a subagent, append the date, owner/component,
reason, files, behavior/contract changes, validation, deployment state, and
limitations. Keep credentials and .env contents out of this record and patches.
User owns frontend/orchestrator integration; specialist changes need a clear
handoff to the corresponding agent developer.

## 2026-11-03 ? Docs agent: model response recovery

Status: implemented in the local orchestrator checkout and loaded by the running
local services. Uncommitted; not pushed or sent to another developer.
Base: orchestrator branch at `5b1c10569803d4acec4ea3f2744e1ab1047ba60f`.
Owner to review: docs-agent developer.
Patch: [2026-11-03-docs-model-recovery.patch](handoffs/subagents/2026-11-03-docs-model-recovery.patch).

### Problem and evidence

An investigation returned DOCS_INVALID_MODEL_RESPONSE despite retrieving docs.
The log for request a914ee9e-885c-4c0c-9e2b-2721daf643dd recorded ValueError with
FORMAT_OR_TRANSPORT. The old logging could not distinguish incomplete output,
a successful status with no claims, or an abstention containing claims.
The exact historical cause remains unconfirmed; this is a resilience fix, not
a claim that the original provider response was recovered or diagnosed.

### Files and changes

- docs_agent/main.py: include the local Answer JSON schema in model instructions;
  distinguish refused/filtered, incomplete, empty, malformed-envelope, schema,
  contradictory-abstention, and empty-claim responses with safe reason codes.
  The provider response_format remains json_object; this is not provider-enforced
  strict JSON-schema output.
- docs_agent/main.py: retry repairable output and model timeouts once with the same
  evidence and a safe reason code. At most two attempts share the remaining total
  agent deadline; the first can receive half the remaining budget. Reserve 50 ms
  for fallback. Refusals and HTTP errors, including 401/403/429, are not retried.
- docs_agent/main.py: on exhausted content validation or model timeout, discard
  generated claims and return original retrieved excerpts with a retrieval-only
  summary, empty hypotheses, confidence 0.4, and a specific PipelineError.
  Logs include request ID, attempt, and reason; recovered attempts are info logs.
  Raw provider/model content is not logged.
- docs_agent/tests/test_docs_agent.py: update expected fallback behavior; cover
  repair, persistent failures, refusals, invalid citations, sanitization, bounded
  deadlines, and HTTP errors without retries.
- docs_agent/tests/test_orchestrator.py: verify fallback excerpts reach the next
  stage while the warning remains visible. Update the older staged-routing query
  to explicitly request documentation and code, matching current routing rules.
- docs_agent/README.md: document recovery, fallback, deadlines, and limitations.

### Contract implications for the owner

The wire schema is unchanged, but behavior changes: when retrieval succeeds and
model content fails, status is now ok with error populated, rather than error.
Here ok means usable retrieval only. The orchestrator preserves excerpts for
synthesis/downstream agents and marks the overall investigation partial. Its UI
still shows the specific warning. Clients must inspect error, not status alone.
Citation/URL checks remain enforced and invalid generated claims are discarded.
Valid no_evidence answers still abstain. Authentication and HTTP failures retain
their previous error/no_access behavior. Retrieval deadline failure remains
DOCS_TIMEOUT. The fallback is not a model diagnosis or confirmed root cause.

### Validation

Run from repository root:

```bash
ORCHESTRATOR_ROOT="$PWD/orchestrator" orchestrator/.venv/bin/python -m pytest docs_agent/tests -q
(cd orchestrator && .venv/bin/python -m pytest tests -q)
git diff --check
```

Results: 74 docs tests passed (including HTTP adapter and staged handoff tests),
274 orchestrator tests passed, and diff formatting check passed.
All four local HTTP services became ready after restart. Tests use mocked models.
No live Azure recovery test was performed. Automatic approval review rejected the
live diagnostic because it would transmit retrieved documentation to the configured
Azure endpoint; explicit user approval is pending for that diagnostic.
Model/network failures remain possible. Retry may add a second model call and cost.
The shorter first-attempt time budget and added schema instructions need live
latency/quality evaluation by the docs owner. HTTP/auth failures do not get this
fallback, and a total retrieval deadline can still fail before evidence is ready.

### Reviewing or transferring the patch

The patch contains only the four docs-agent files above, relative to repository
root, against 5b1c105. Review before applying in a matching integration checkout:

```bash
git apply --stat handoffs/subagents/2026-11-03-docs-model-recovery.patch
git apply --check handoffs/subagents/2026-11-03-docs-model-recovery.patch
```

The standalone docs branch may differ from the integration base. Port the reviewed
changes instead of assuming the patch applies cleanly. Tests need the shared
agent_contracts module and optional ORCHESTRATOR_ROOT for integration coverage.
Regenerate this patch if the implementation changes after this record.

## 2026-11-03 ? Logs and code agents

No source-code edits to logs_agent/ or code_agent/ were made in this session.
Those directories already contained orchestrator integration changes relative to
their specialist remote branches; they predate this work. Do not attribute those
to the docs-model recovery patch.

## 2026-11-03 ? Local integration setup (not specialist source changes)

- User supplied/updated each agent's ignored .env file; contents are not recorded.
- Updated orchestrator/.env to remote mode and local service URLs: logs 8101,
  docs 8102, code 8103. Routing auto; general agent timeout 60 s; logs timeout 180 s.
- Installed repository-declared integration dependencies into orchestrator/.venv
  using requirements-dev.txt. Installed Playwright for frontend verification.
  These environment installs are not changes to dependency requirement files.
- User installed Azure CLI and signed in inside WSL. Sign-in does not itself grant
  Azure resource permissions. No role assignments or credentials were changed
  by this agent.
- Restarted run_local.py after config/login and after the docs fix. Services load
  their own .env files. HTTP readiness alone does not verify Azure/source access.
- Frontend dropdown removal, build refresh, and browser test changes are separate
  frontend/orchestrator work and are excluded from the specialist patch.


## 2026-11-03 ? Code GitHub credential check and restart

User reported CODE_GITHUB_ACCESS_DENIED (HTTP 403). A read-only request to the
public open-telemetry/opentelemetry-demo repository, using the credential already
present in code_agent/.env, returned HTTP 200 with a 5,000-request primary limit
and 4,999 remaining. No credential value was printed or changed. Historical logs
lack rate-limit headers, so the original 403 cause is unconfirmed. Restarting the
local launcher reloads the current credential. No code-agent source changes were
made. This verifies GitHub metadata access, not a complete live investigation.


## 2026-11-03 ? Configuration reload requested by user

Restarted the local launcher to reload the user-updated GitHub token from
code_agent/.env. No token contents were inspected or recorded, and no subagent
source files were changed. Service readiness does not verify a live investigation.


## 2026-11-03 ? Isolated origin/main comparison instance

Created a detached worktree at origin/main commit 7ebcf32 under
diagnostics/main-comparison/origin-main-7ebcf32. Built its own frontend and
started its original backend on 8001, with separate original logs/docs/code
services on 8201/8202/8203. Current orchestrator continues on 8000. No baseline
tracked source was modified. Copies of the user-provided .env files use the same
credentials and data sources; only the baseline orchestrator service URLs were
changed. Both use the primary project Python environment and launcher utility.
HTTP homepages and all baseline service health checks passed. No live model
investigations were submitted for validation. See diagnostics/main-comparison/README.md
for restart instructions and comparison caveats.
