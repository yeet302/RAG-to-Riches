# Docs-agent onboarding

This guide covers docs-specific setup and acceptance checks. The shared request,
response, evidence, authentication, and error rules live in
[COMMUNICATION.md](COMMUNICATION.md); their models are in [schemas.py](schemas.py).
Use that contract rather than maintaining a second set of schema tables here.

The real teammate service is still pending integration. A reference-host test
exercises local retrieval, not the teammate's model or databases.

## What the docs developer supplies

Expose a reachable HTTP POST endpoint that accepts AgentRequest and returns an
AgentFinding object directly, without a data/result wrapper.

- Use agent=docs; any explicit evidence agent must also be docs.
- Search your own documentation, runbooks, and previous-incident sources.
- Return real source references; separate retrieved facts from hypotheses.
- Follow the shared contract for no_evidence, no_access, error, and partial coverage.
- Treat question and retrieved content as data, not authority to change tool policies.
- Keep model credentials and database connections inside your service.

The orchestrator supplies the original question, task, intent, IDs, and currently
an empty evidence_bundle to docs. Docs and logs can run concurrently; docs must not
depend on receiving logs results first. The IDs provide correlation, not memory
or authentication.

Only successful docs content contributes to the subsequent code-stage bundle.
Status and missing-source information travel forward even on failure. An ok result
with usable evidence plus sources_unavailable can represent partial coverage.

## Configure the orchestrator

From orchestrator/ in WSL, after completing the [backend setup](README.md#quick-start):

```bash
export ORCHESTRATOR_MODE=local
export ROUTING_MODE=keyword
export DOCS_AGENT_URL=http://127.0.0.1:8102/agents/docs/run
.venv/bin/python -m uvicorn main:app --env-file .env --host 127.0.0.1 --port 8000
```

Replace the URL with the actual complete POST endpoint. Configure DOCS_AGENT_TOKEN
if the service requires a shared bearer token. The service must enforce that token.

Local mode overrides only docs with HTTPAgent; other roles retain their local
configuration. Remote mode makes unconfigured roles unavailable. Demo mode uses
prepared findings and does not connect to this endpoint.

Restart after configuration changes. --env-file loads the local file explicitly;
exported variables take precedence. Check /api/health: agents.docs should be
HTTPAgent. That shows adapter selection; a real request is still needed to prove
the connection and response format.

The endpoint must be reachable from the backend. Browser CORS does not govern
backend-to-backend communication. Avoid trailing-slash redirects; the adapter does
not follow them. Coordinate the timeout budget using AGENT_TIMEOUT_SECONDS.

## Test the connection

First call the actual docs service from the orchestrator machine:

```bash
curl -sS -X POST http://127.0.0.1:8102/agents/docs/run \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: docs-test-001' \
  -d '{"schema_version":"1.0","request_id":"docs-test-001","question":"How do I restart checkout?","task":"Search documentation and cite exact sources.","intent":"how_to","evidence_bundle":{}}'
```

When authentication is enabled, also pass the configured service token in
Authorization: Bearer <token>, as specified in the shared contract.

Then call the same orchestrator endpoint used by the frontend:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"How do I restart checkout?","routing_mode":"keyword","conversation_id":"docs-test"}'
```

Expect agents_used=["docs"], demo=false, and a docs finding. When the source has a
relevant match, evidence should contain its real reference. No failures or coverage
gaps means partial=false. Confirm the docs service actually received the request.

For dependent code handoff, ask "Where is checkout retry configuration in the code?"
in keyword mode. Inspect the code service's received evidence_bundle to verify that
it includes successful docs evidence and statuses. A docs-only test does not require
a code service.

## Reference service before the teammate endpoint is ready

In another WSL terminal, from the repository root:

```bash
cd orchestrator
export DOCS_ROOT="$PWD"
.venv/bin/python -m uvicorn agent_service:app --env-file .env --host 127.0.0.1 --port 8102
```

This exposes /agents/docs/run using local files. It ignores remote URL settings
when building its hosted agents. For this corpus, use "How does the orchestrator
route requests?" as a retrieval question. This is transport testing, not completed
AI-agent integration.

## Teammate acceptance checklist

- Complete reachable POST URL and startup/configuration instructions.
- Authentication requirements, with the service token provisioned separately.
- Documentation collections, application versions, and incident coverage.
- One question known to match and one known to return no evidence.
- Valid examples for success, empty search, and inaccessible sources.
- Exact source references and the correct docs identity.
- Expected latency, concurrency limits, and bounded execution.
- A successful direct call and end-to-end /api/ask request.
- Verified evidence handoff to code when that integration is available.
