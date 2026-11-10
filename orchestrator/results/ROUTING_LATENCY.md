# Orchestrator routing latency experiment - November 3, 2026

Implemented a conservative fast path in the orchestrator. Short, explicit, single-source lookups use existing specialist task contracts without an Azure planning call. Complex incidents and mixed requests retain model-generated delegation. No subagent source, model deployment, API key, or existing .env was changed.

## Measured result

The live A/B benchmark used the configured gpt-5.6-terra deployment, six handcrafted questions, and two repetitions per question/mode. Trial order alternated. This produced 24 routing trials: 8 eligible before/after pairs and 4 complex control pairs. No specialist or final-synthesis requests were made.

Across eligible requests, median routing time fell from **3330.008 ms** to **0.099 ms**, saving **3.330 seconds** per eligible request at the routing stage. Planning calls fell from 8 to 0; both modes selected the expected specialist in all 8 eligible trials. No model fallbacks occurred.

| Question | Baseline median | Fast-path median | Planning skipped? |
|---|---:|---:|---|
| Where is GetAds implemented? | 4661.542 ms | 0.264 ms | Yes |
| Where is the retry function defined? | 3488.122 ms | 0.158 ms | Yes |
| Show ad service warning logs from the last hour. | 3273.450 ms | 0.057 ms | Yes |
| Show documentation for configuring tracing. | 3193.901 ms | 0.119 ms | Yes |
| The ad service is returning GetAds failures with status UNAVAILABLE. What could be causing this? | 5754.854 ms | 5683.751 ms | No - model retained |
| Show ad service logs and locate the code that emits the warning. | 5879.031 ms | 4654.327 ms | No - model retained |

Timing differences between the complex controls are normal variation in model calls, not measured benefits of this optimization. The earlier GetAds incident question is a complex control and is deliberately not accelerated by the fast path.

## Limits

- These are routing measurements, not full live investigation measurements. The agents and final synthesis were not rerun.
- The small handcrafted dataset establishes behavior for these cases, not general routing accuracy or semantic answer correctness.
- Downstream retrieval and synthesis are unchanged; no reduction in their duration is claimed.
- Exact original questions, including time constraints, are preserved when dispatching fast-path requests.
- Mixed, long, unclear, diagnostic, and explicit model-only requests retain model planning.

## Validation and use

274 orchestrator tests passed in 2.21 seconds. The frontend type-check and production build passed. Tests verify expected agent selection, zero model calls for eligible requests, model planning for complex questions, the disable switch, explicit LLM overrides, and API dispatch/recording.

The optimization defaults on for server-selected LLM routing. Set ROUTING_FAST_PATH=false to disable it. An explicit API routing_mode=llm also forces the baseline planning behavior. Keyword routing remains available. Restart the orchestrator or single-command launcher to load the changes. The frontend labels eligible requests Fast path (no planning call).

Repeat from orchestrator/:

```bash
.venv/bin/python benchmark_routing.py --live --repeat 2
```

All 24 live trials were appended to the ignored local accuracy_results.csv. Detailed rows and model-call counts are in diagnostics/routing-benchmark/20261103T211356Z-7b899f07/. All generated result contents remain local. No changes were pushed.
