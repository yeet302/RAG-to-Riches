"""Compare model-only routing with the conservative fast path. No subagents run."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time
from uuid import uuid4

import httpx
from dotenv import load_dotenv

from evaluate import append_row
from query_analysis import analyze_question
from settings import Settings

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    {"id":"ROUTE-CODE-1", "question":"Where is GetAds implemented?", "expected_agents":["code"], "eligible":True},
    {"id":"ROUTE-CODE-2", "question":"Where is the retry function defined?", "expected_agents":["code"], "eligible":True},
    {"id":"ROUTE-LOGS", "question":"Show ad service warning logs from the last hour.", "expected_agents":["logs"], "eligible":True},
    {"id":"ROUTE-DOCS", "question":"Show documentation for configuring tracing.", "expected_agents":["docs"], "eligible":True},
    {"id":"CONTROL-INCIDENT", "question":"The ad service is returning GetAds failures with status UNAVAILABLE. What could be causing this?", "expected_agents":None, "eligible":False},
    {"id":"CONTROL-MIXED", "question":"Show ad service logs and locate the code that emits the warning.", "expected_agents":None, "eligible":False},
]


class CountedTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.calls = 0
        self.inner = None

    async def handle_async_request(self, request):
        if self.inner is None:
            self.inner = httpx.AsyncHTTPTransport(retries=0)
        self.calls += 1
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        if self.inner is not None:
            await self.inner.aclose()
            self.inner = None


async def benchmark(settings, repeat, output_dir, accuracy_csv):
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    output = output_dir / run_id
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for case in CASES:
        for trial in range(1, repeat + 1):
            # Alternate order to avoid always putting one mode first.
            modes = [False,True] if trial % 2 else [True,False]
            for enabled in modes:
                config = replace(settings, routing_mode="llm", routing_fast_path=enabled)
                transport = CountedTransport()
                started = time.perf_counter()
                try:
                    analysis = await analyze_question(case["question"], config, transport=transport)
                finally:
                    await transport.aclose()
                elapsed = round((time.perf_counter() - started) * 1000, 3)
                roles = analysis.stage1 + analysis.stage2
                selection_ok = (set(roles) == set(case["expected_agents"])) if case["expected_agents"] else None
                guard_ok = analysis.fast_path == (enabled and case["eligible"])
                row = dict(case_id=case["id"],question=case["question"],trial=trial,
                    mode="fast_path_enabled" if enabled else "model_only",eligible=case["eligible"],
                    routing_ms=elapsed,model_calls=transport.calls,selection_ok=selection_ok,
                    guard_ok=guard_ok,analysis=analysis.model_dump())
                records.append(row)
                with (output / "runs.jsonl").open("a") as handle:
                    handle.write(json.dumps(row) + "\n")
                append_row(accuracy_csv, {
                    "bug":case["id"], "expected cause":"Routing benchmark; expected roles: " + str(case["expected_agents"] or "LLM decision for complex request"),
                    "agent answer":json.dumps(analysis.model_dump()),
                    "correct?":("Yes (expected routing roles)" if selection_ok else "No (unexpected routing roles)") if selection_ok is not None else "Not reviewed (complex LLM delegation)",
                    "fix worked?":"Not tested (no incident remediation)","time taken":f"{elapsed} ms (routing only)",
                    "run date":datetime.now(timezone.utc).isoformat(),"run type":"Routing latency benchmark: " + row["mode"],
                    "question":case["question"],"expected agents":json.dumps(case["expected_agents"]),
                    "agents used":json.dumps(roles),"routing correct?":"Yes" if selection_ok else "No" if selection_ok is False else "Not reviewed",
                    "pipeline status":analysis.source,"request id":run_id + "-" + case["id"] + "-" + str(trial) + "-" + row["mode"],
                    "step timings ms":json.dumps({"routing":elapsed}),"errors":analysis.warning or "",
                })
                print(json.dumps({key:row[key] for key in ["case_id","trial","mode","routing_ms","model_calls","selection_ok","guard_ok"]}), flush=True)
    eligible_before = [r for r in records if r["eligible"] and r["mode"] == "model_only"]
    eligible_after = [r for r in records if r["eligible"] and r["mode"] == "fast_path_enabled"]
    before = statistics.median(r["routing_ms"] for r in eligible_before)
    after = statistics.median(r["routing_ms"] for r in eligible_after)
    summary = {
        "run_id":run_id,"scope":"Routing only; no subagents, source retrieval, synthesis, or answer caching", "model_deployment":settings.llm_model,
        "repeat_per_case":repeat,"total_trials":len(records),"eligible_pairs":len(eligible_before),
        "eligible_baseline_median_ms":before,"eligible_optimized_median_ms":after,
        "eligible_median_saved_ms":round(before-after,3),"eligible_routing_reduction_percent":round((1-after/before)*100,4) if before else None,
        "eligible_baseline_model_calls":sum(r["model_calls"] for r in eligible_before),
        "eligible_optimized_model_calls":sum(r["model_calls"] for r in eligible_after),
        "eligible_baseline_selection_passes":sum(r["selection_ok"] is True for r in eligible_before),
        "eligible_optimized_selection_passes":sum(r["selection_ok"] is True for r in eligible_after),
        "all_guards_correct":all(r["guard_ok"] for r in records),
        "model_fallback_trials":sum(r["analysis"]["source"]=="keyword_fallback" for r in records),
        "cases":[],
        "limitations":["Small, handcrafted routing benchmark; not a general accuracy score.",
                       "Complex incidents still use model planning and are not accelerated by this change.",
                       "Full request time was not benchmarked against real subagents; downstream execution is unchanged."],
    }
    for case in CASES:
        rows = [r for r in records if r["case_id"]==case["id"]]
        summary["cases"].append({"case_id":case["id"],"question":case["question"],"eligible":case["eligible"],
            "baseline_median_ms":statistics.median(r["routing_ms"] for r in rows if r["mode"]=="model_only"),
            "optimized_median_ms":statistics.median(r["routing_ms"] for r in rows if r["mode"]=="fast_path_enabled"),
            "optimized_fast_paths":sum(r["analysis"]["fast_path"] for r in rows if r["mode"]=="fast_path_enabled")})
    (output / "summary.json").write_text(json.dumps(summary,indent=2))
    print("SUMMARY " + json.dumps(summary), flush=True)
    print("Saved: " + str(output), flush=True)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live",action="store_true",help="Make real routing-only calls using the configured Azure model")
    parser.add_argument("--repeat",type=int,default=2)
    parser.add_argument("--env-file",type=Path,default=Path(__file__).parent / ".env")
    parser.add_argument("--output-dir",type=Path,default=ROOT / "diagnostics/routing-benchmark")
    parser.add_argument("--accuracy-csv",type=Path,default=ROOT / "orchestrator/results/accuracy_results.csv")
    args=parser.parse_args()
    if not args.live or args.repeat<1:
        parser.error("Use --live and a positive --repeat count; only handcrafted questions are sent, never source evidence")
    load_dotenv(args.env_file)
    settings=Settings.from_env()
    if not settings.llm_configured:
        parser.error("The orchestrator Azure model must be configured for a real before/after comparison")
    summary=asyncio.run(benchmark(settings,args.repeat,args.output_dir,args.accuracy_csv))
    return 0 if (summary["all_guards_correct"] and not summary["model_fallback_trials"]
                 and summary["eligible_optimized_selection_passes"] == summary["eligible_pairs"]) else 1


if __name__=="__main__":
    raise SystemExit(main())
