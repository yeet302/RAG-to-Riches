#!/usr/bin/env python3
"""
Repeatable evaluation harness for the Foundry Logs Agent.

Uses a fixed set of cases grounded in data/ad_service.log. Calls
investigate_logs_with_metrics() for each case and scores deterministic
checks only (status, message terms, trace IDs, forbidden claim
substrings, latency), plus reports deployment / serving-model / token
metrics for baseline vs Model Router comparison.

Does NOT change the agent prompts, connector, tools, schema, or cases.

Usage:
    DEBUG_TOOL_CALLS=0 ./venv/bin/python evaluate_agent.py
    FOUNDRY_MODEL=gpt-4.1-mini DEBUG_TOOL_CALLS=0 ./venv/bin/python evaluate_agent.py
    FOUNDRY_MODEL=logs-model-router DEBUG_TOOL_CALLS=0 ./venv/bin/python evaluate_agent.py
    ./venv/bin/python evaluate_agent.py --limit 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

from foundry_agent import (
    DEBUG_TOOL_CALLS,
    get_foundry_model,
    investigate_logs_with_metrics,
)
from investigation_metrics import InvestigationMetrics
from investigation_result import LogInvestigationResult

load_dotenv()

EVAL_RESULTS_DIR = Path(__file__).resolve().parent / "evaluation_results"

# Real values from data/ad_service.log (LocalLogConnector parse).
GETADS_TRACE_A = "3b4949e9e897c7352e518f21f38ad64d"
GETADS_TRACE_B = "d97e8147efa7fb6ff84a0b82af2077f3"
NON_FAIL_TRACE = "43cb16e3bbb1b9d8234cd38a7e27aaef"
TARGETED_TRACE = "30b7c337a5a66d804ab80e8c373367ad"
ERROR_TIMESTAMP = "2026-10-17T22:31:37.664000+00:00"
FAKE_TRACE = "00000000000000000000000000000000"


@dataclass(frozen=True)
class EvalCase:
    id: str
    question: str
    expected_status: str
    expected_message_terms: tuple[str, ...] = ()
    expected_trace_ids: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    notes: str = ""
    # If True, require every expected_trace_id to appear in related_trace_ids
    # or evidence[].trace_id. If False, only check when expected_trace_ids
    # is non-empty that at least one appears (unused — we require all).
    require_all_traces: bool = True
    # Genuinely correct for missing-trace lookups: do not echo the caller ID.
    expect_empty_related_trace_ids: bool = False


@dataclass
class CaseResult:
    case: EvalCase
    passed: bool
    actual_status: str
    evidence_count: int
    actual_trace_ids: list[str]
    terms_found: bool
    traces_found: bool
    forbidden_hit: Optional[str]
    latency_s: float
    error: Optional[str] = None
    fail_reasons: list[str] = field(default_factory=list)
    metrics: Optional[InvestigationMetrics] = None


# Claims that must not appear for GetAds root-cause / ownership questions.
_GETADS_UNSUPPORTED_CAUSE_CLAIMS = (
    "upstream connectivity",
    "upstream service",
    "network partition",
    "engineer",
    "deployed the version",
    "code change caused",
)


def build_eval_cases() -> list[EvalCase]:
    """Fixed 10–15 cases; IDs and questions are stable across runs."""
    return [
        EvalCase(
            id="E01_getads_existence",
            question="Are there any GetAds failures?",
            expected_status="evidence_found",
            expected_message_terms=("GetAds", "UNAVAILABLE"),
            expected_trace_ids=(GETADS_TRACE_A, GETADS_TRACE_B),
            forbidden_claims=_GETADS_UNSUPPORTED_CAUSE_CLAIMS,
            notes="Direct existence/list retrieval of GetAds failures.",
        ),
        EvalCase(
            id="E02_error_severity_list",
            question="What ERROR events appear in the Ad Service logs?",
            expected_status="evidence_found",
            expected_message_terms=("ERROR", "Failed to export"),
            expected_trace_ids=(),
            forbidden_claims=("GetAds",),
            notes=(
                "Severity filter: ERROR events are javaagent exporter "
                "failures (severity=ERROR), not GetAds (severity=null)."
            ),
        ),
        EvalCase(
            id="E03_service_targeted_ads",
            question="Show Ad Service logs about targeted ad requests for binoculars.",
            expected_status="evidence_found",
            expected_message_terms=("Targeted", "binoculars"),
            expected_trace_ids=(),
            forbidden_claims=("GetAds Failed",),
            notes="Service + distinctive query retrieval.",
        ),
        EvalCase(
            id="E04_single_nonfail_trace",
            question=f"What happened for trace {NON_FAIL_TRACE}?",
            expected_status="evidence_found",
            expected_message_terms=("Non-targeted", "random response"),
            expected_trace_ids=(NON_FAIL_TRACE,),
            forbidden_claims=("GetAds Failed", "UNAVAILABLE"),
            notes="Single-trace investigation of a non-failure request.",
        ),
        EvalCase(
            id="E05_single_getads_fail_trace",
            question=f"What happened for trace {GETADS_TRACE_A}?",
            expected_status="evidence_found",
            expected_message_terms=("GetAds", "UNAVAILABLE"),
            expected_trace_ids=(GETADS_TRACE_A,),
            forbidden_claims=(
                "upstream connectivity",
                "engineer",
            ),
            notes="Single failed GetAds trace retrieval (not root-cause).",
        ),
        EvalCase(
            id="E06_getads_root_cause",
            question="What is the exact root cause of the GetAds failure?",
            expected_status="insufficient_evidence",
            expected_message_terms=("GetAds", "UNAVAILABLE"),
            expected_trace_ids=(GETADS_TRACE_A, GETADS_TRACE_B),
            forbidden_claims=_GETADS_UNSUPPORTED_CAUSE_CLAIMS,
            notes=(
                "Multi-trace root-cause: inspect both failed traces; "
                "logs show UNAVAILABLE but not exact cause."
            ),
        ),
        EvalCase(
            id="E07_why_getads_failing",
            question="Why are GetAds requests failing in the Ad Service?",
            expected_status="insufficient_evidence",
            expected_message_terms=("GetAds", "UNAVAILABLE"),
            expected_trace_ids=(GETADS_TRACE_A, GETADS_TRACE_B),
            forbidden_claims=_GETADS_UNSUPPORTED_CAUSE_CLAIMS,
            notes="Pattern/why question over multiple GetAds failure traces.",
        ),
        EvalCase(
            id="E08_no_evidence_engineer",
            question="Which engineer deployed the version that caused this failure?",
            expected_status="no_evidence_found",
            expected_message_terms=(),
            expected_trace_ids=(),
            forbidden_claims=(
                "Alice",
                "Bob",
                "deployed by",
                "commit",
                "pull request",
            ),
            notes="Ownership/deployment not present in logs.",
        ),
        EvalCase(
            id="E09_missing_trace",
            question=f"What happened for trace {FAKE_TRACE}?",
            expected_status="no_evidence_found",
            expected_message_terms=(),
            expected_trace_ids=(),
            forbidden_claims=(GETADS_TRACE_A, GETADS_TRACE_B, "GetAds Failed"),
            notes=(
                "Unknown trace ID should not hallucinate evidence; "
                "related_trace_ids must stay empty."
            ),
            expect_empty_related_trace_ids=True,
        ),
        EvalCase(
            id="E10_exporter_errors_not_getads",
            question=(
                "Were there OpenTelemetry exporter failures when exporting "
                "logs or metrics from the Ad Service?"
            ),
            expected_status="evidence_found",
            expected_message_terms=("Failed to export",),
            expected_trace_ids=(),
            forbidden_claims=("GetAds Failed", "UNAVAILABLE"),
            notes=(
                "Unrelated telemetry exporter failures (severity=ERROR, "
                "no trace_id) — must not mix with GetAds."
            ),
        ),
        EvalCase(
            id="E11_getads_vs_exporter_unrelated",
            question=(
                "Did GetAds fail because of the OpenTelemetry "
                "'Failed to export logs' errors?"
            ),
            expected_status="insufficient_evidence",
            expected_message_terms=("GetAds",),
            expected_trace_ids=(),
            forbidden_claims=(
                "caused by the exporter",
                "caused by Failed to export",
                "exporter timeouts caused GetAds",
                "engineer",
            ),
            notes=(
                "Unrelated failure classes: GetAds UNAVAILABLE (app, has "
                "trace_id) vs javaagent exporter ERROR (no trace_id). "
                "Logs do not establish a causal link."
            ),
        ),
        EvalCase(
            id="E12_time_window_error",
            question=(
                f"What happened around {ERROR_TIMESTAMP} in the Ad Service?"
            ),
            expected_status="evidence_found",
            expected_message_terms=("Failed to export",),
            expected_trace_ids=(),
            forbidden_claims=("engineer",),
            notes="Time-window / context investigation near first ERROR.",
        ),
        EvalCase(
            id="E13_warn_severity",
            question="List WARN or WARNING events from the Ad Service.",
            expected_status="evidence_found",
            expected_message_terms=(),  # wording varies (WARN vs WARNING)
            expected_trace_ids=(),
            forbidden_claims=("GetAds Failed",),
            notes="Severity listing; either WARN or WARNING may appear.",
        ),
        EvalCase(
            id="E14_targeted_trace",
            question=f"What happened for trace {TARGETED_TRACE}?",
            expected_status="evidence_found",
            expected_message_terms=("Targeted", "binoculars"),
            expected_trace_ids=(TARGETED_TRACE,),
            forbidden_claims=("GetAds Failed",),
            notes="Single targeted-ad request trace.",
        ),
    ]


def _result_blob(result: LogInvestigationResult) -> str:
    """Lowercased text used for term / forbidden-claim scans."""
    parts = [
        result.summary,
        " ".join(result.limitations),
        " ".join(h.description for h in result.hypotheses),
        " ".join(e.message or "" for e in result.evidence),
        " ".join(result.related_trace_ids),
        " ".join(e.trace_id or "" for e in result.evidence),
        " ".join(e.severity or "" for e in result.evidence),
    ]
    return " ".join(parts).lower()


def _actual_trace_ids(result: LogInvestigationResult) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for tid in list(result.related_trace_ids) + [
        e.trace_id for e in result.evidence if e.trace_id
    ]:
        if not tid or tid in seen:
            continue
        seen.add(tid)
        ids.append(tid)
    return ids


def score_case(
    case: EvalCase,
    result: LogInvestigationResult,
    latency_s: float,
    metrics: Optional[InvestigationMetrics] = None,
) -> CaseResult:
    blob = _result_blob(result)
    actual_traces = _actual_trace_ids(result)
    actual_traces_lower = {t.lower() for t in actual_traces}
    reasons: list[str] = []

    status_ok = result.status.value == case.expected_status
    if not status_ok:
        reasons.append(
            f"status: expected {case.expected_status}, got {result.status.value}"
        )

    terms_ok = True
    if case.expected_message_terms:
        missing = [
            term
            for term in case.expected_message_terms
            if term.lower() not in blob
        ]
        terms_ok = not missing
        if missing:
            reasons.append(f"missing terms: {missing}")

    traces_ok = True
    if case.expected_trace_ids:
        missing_t = [
            tid
            for tid in case.expected_trace_ids
            if tid.lower() not in actual_traces_lower
        ]
        traces_ok = not missing_t
        if missing_t:
            reasons.append(f"missing trace_ids: {missing_t}")

    forbidden_hit: Optional[str] = None
    for claim in case.forbidden_claims:
        if claim.lower() in blob:
            forbidden_hit = claim
            reasons.append(f"forbidden claim present: {claim!r}")
            break

    related_ok = True
    if case.expect_empty_related_trace_ids and result.related_trace_ids:
        related_ok = False
        reasons.append(
            "related_trace_ids must be empty when no matching events exist; "
            f"got {result.related_trace_ids}"
        )

    passed = (
        status_ok
        and terms_ok
        and traces_ok
        and forbidden_hit is None
        and related_ok
    )

    return CaseResult(
        case=case,
        passed=passed,
        actual_status=result.status.value,
        evidence_count=len(result.evidence),
        actual_trace_ids=actual_traces,
        terms_found=terms_ok,
        traces_found=traces_ok,
        forbidden_hit=forbidden_hit,
        latency_s=latency_s,
        fail_reasons=reasons,
        metrics=metrics,
    )


def _metrics_or_empty(scored: CaseResult) -> dict[str, Any]:
    if scored.metrics is None:
        return {
            "configured_deployment": get_foundry_model(),
            "serving_models": [],
            "model_call_count": 0,
            "tool_call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "latency": scored.latency_s,
            "steps": [],
        }
    m = scored.metrics
    return {
        "configured_deployment": m.configured_deployment,
        "serving_models": list(m.underlying_models_used),
        "model_call_count": m.model_call_count,
        "tool_call_count": m.tool_call_count,
        "input_tokens": m.input_tokens,
        "output_tokens": m.output_tokens,
        "total_tokens": m.total_tokens,
        "latency": m.elapsed_seconds,
        "steps": [step.to_dict() for step in m.steps],
    }


def _print_case_report(index: int, total: int, scored: CaseResult) -> None:
    case = scored.case
    verdict = "PASS" if scored.passed else "FAIL"
    meta = _metrics_or_empty(scored)
    print("=" * 72)
    print(f"[{index}/{total}] {verdict}  {case.id}")
    print(f"question:         {case.question}")
    print(f"expected status:  {case.expected_status}")
    print(f"actual status:    {scored.actual_status}")
    print(f"evidence count:   {scored.evidence_count}")
    print(f"trace IDs:        {scored.actual_trace_ids or '(none)'}")
    print(f"configured deployment: {meta['configured_deployment']}")
    print(f"serving model(s): {meta['serving_models'] or '(none)'}")
    print(f"model calls:      {meta['model_call_count']}")
    print(f"tool calls:       {meta['tool_call_count']}")
    print(f"input tokens:     {meta['input_tokens']}")
    print(f"output tokens:    {meta['output_tokens']}")
    print(f"total tokens:     {meta['total_tokens']}")
    print(f"latency:          {meta['latency']:.2f}s")
    print(f"notes:            {case.notes}")
    if scored.error:
        print(f"error:            {scored.error}")
    if scored.fail_reasons:
        print("fail reasons:     " + "; ".join(scored.fail_reasons))
    print()


def _aggregate_metrics(results: list[CaseResult]) -> dict[str, Any]:
    step_model_counts: Counter[str] = Counter()
    total_model_calls = 0
    total_tool_calls = 0
    total_input = 0
    total_output = 0
    total_tokens = 0
    latencies: list[float] = []

    for scored in results:
        meta = _metrics_or_empty(scored)
        for step in meta["steps"]:
            serving = step.get("serving_model")
            if serving:
                step_model_counts[serving] += 1
        if not meta["steps"]:
            for name in meta["serving_models"]:
                step_model_counts[name] += 1
        total_model_calls += meta["model_call_count"]
        total_tool_calls += meta["tool_call_count"]
        total_input += meta["input_tokens"]
        total_output += meta["output_tokens"]
        total_tokens += meta["total_tokens"]
        latencies.append(float(meta["latency"]))

    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
    return {
        "configured_deployment": get_foundry_model(),
        "model_selection_distribution": dict(step_model_counts),
        "total_model_calls": total_model_calls,
        "total_tool_calls": total_tool_calls,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_tokens,
        "average_latency": avg_latency,
    }


def _print_summary(results: list[CaseResult]) -> None:
    total = len(results)
    if total == 0:
        print("No cases run.")
        return

    status_hits = sum(
        1 for r in results if r.actual_status == r.case.expected_status
    )
    cases_with_traces = [r for r in results if r.case.expected_trace_ids]
    trace_hits = sum(1 for r in cases_with_traces if r.traces_found)
    forbidden_violations = sum(1 for r in results if r.forbidden_hit)
    passes = sum(1 for r in results if r.passed)
    agg = _aggregate_metrics(results)

    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"configured deployment:       {agg['configured_deployment']}")
    print(f"total cases:                 {total}")
    print(f"overall PASS:                {passes}/{total}")
    print(
        f"status accuracy:             {status_hits}/{total} "
        f"({100.0 * status_hits / total:.1f}%)"
    )
    if cases_with_traces:
        print(
            f"trace-ID accuracy:           {trace_hits}/{len(cases_with_traces)} "
            f"({100.0 * trace_hits / len(cases_with_traces):.1f}%)"
        )
    else:
        print("trace-ID accuracy:           n/a (no cases expect traces)")
    print(f"forbidden-claim violations:  {forbidden_violations}")
    print(f"model selection distribution:{agg['model_selection_distribution']}")
    print(f"total model calls:           {agg['total_model_calls']}")
    print(f"total tool calls:            {agg['total_tool_calls']}")
    print(f"total input tokens:          {agg['total_input_tokens']}")
    print(f"total output tokens:         {agg['total_output_tokens']}")
    print(f"total tokens:                {agg['total_tokens']}")
    print(f"average latency:             {agg['average_latency']:.2f}s")
    print()


def _case_result_payload(scored: CaseResult) -> dict[str, Any]:
    meta = _metrics_or_empty(scored)
    return {
        "case_id": scored.case.id,
        "question": scored.case.question,
        "pass": scored.passed,
        "expected_status": scored.case.expected_status,
        "actual_status": scored.actual_status,
        "serving_models": meta["serving_models"],
        "model_call_count": meta["model_call_count"],
        "tool_call_count": meta["tool_call_count"],
        "input_tokens": meta["input_tokens"],
        "output_tokens": meta["output_tokens"],
        "total_tokens": meta["total_tokens"],
        "latency": meta["latency"],
        "evidence_count": scored.evidence_count,
        "actual_trace_ids": scored.actual_trace_ids,
        "fail_reasons": scored.fail_reasons,
        "error": scored.error,
        "steps": meta["steps"],
    }


def save_evaluation_results(
    results: list[CaseResult],
    *,
    configured_deployment: str,
) -> Path:
    EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_dep = "".join(
        ch if ch.isalnum() or ch in "-._" else "_"
        for ch in configured_deployment
    )
    path = EVAL_RESULTS_DIR / f"eval_{safe_dep}_{stamp}.json"

    status_hits = sum(
        1 for r in results if r.actual_status == r.case.expected_status
    )
    cases_with_traces = [r for r in results if r.case.expected_trace_ids]
    trace_hits = sum(1 for r in cases_with_traces if r.traces_found)
    forbidden_violations = sum(1 for r in results if r.forbidden_hit)
    passes = sum(1 for r in results if r.passed)
    agg = _aggregate_metrics(results)
    total = len(results)

    payload = {
        "saved_at": stamp,
        "configured_deployment": configured_deployment,
        "cases": [_case_result_payload(r) for r in results],
        "summary": {
            "total_cases": total,
            "overall_pass": passes,
            "status_accuracy": {
                "hits": status_hits,
                "total": total,
                "pct": (100.0 * status_hits / total) if total else 0.0,
            },
            "trace_id_accuracy": {
                "hits": trace_hits,
                "total": len(cases_with_traces),
                "pct": (
                    (100.0 * trace_hits / len(cases_with_traces))
                    if cases_with_traces
                    else None
                ),
            },
            "forbidden_claim_violations": forbidden_violations,
            "model_selection_distribution": agg["model_selection_distribution"],
            "total_model_calls": agg["total_model_calls"],
            "total_tool_calls": agg["total_tool_calls"],
            "total_input_tokens": agg["total_input_tokens"],
            "total_output_tokens": agg["total_output_tokens"],
            "total_tokens": agg["total_tokens"],
            "average_latency": agg["average_latency"],
        },
    }

    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def run_evaluation(
    *,
    limit: Optional[int] = None,
    only_ids: Optional[set[str]] = None,
) -> list[CaseResult]:
    cases = build_eval_cases()
    if only_ids:
        cases = [
            c
            for c in cases
            if c.id in only_ids
            or any(c.id == oid or c.id.startswith(oid + "_") for oid in only_ids)
        ]
    if limit is not None:
        cases = cases[: max(0, limit)]

    results: list[CaseResult] = []
    total = len(cases)
    configured = get_foundry_model()

    print(f"Foundry deployment: {configured}")
    print(f"Logs Agent evaluation harness — {total} cases")
    print(f"DEBUG_TOOL_CALLS={'on' if DEBUG_TOOL_CALLS else 'off'}")
    print()

    for index, case in enumerate(cases, start=1):
        if DEBUG_TOOL_CALLS:
            print(f"\n--- tool activity for {case.id} ---")

        start = time.perf_counter()
        try:
            result, metrics = investigate_logs_with_metrics(case.question)
            latency = time.perf_counter() - start
            # Prefer wall elapsed from metrics collector when present.
            scored = score_case(case, result, metrics.elapsed_seconds or latency, metrics)
        except Exception as exc:
            latency = time.perf_counter() - start
            scored = CaseResult(
                case=case,
                passed=False,
                actual_status="error",
                evidence_count=0,
                actual_trace_ids=[],
                terms_found=False,
                traces_found=not bool(case.expected_trace_ids),
                forbidden_hit=None,
                latency_s=latency,
                error=str(exc),
                fail_reasons=[f"exception: {exc}"],
                metrics=None,
            )

        results.append(scored)
        _print_case_report(index, total, scored)

    _print_summary(results)
    out_path = save_evaluation_results(
        results, configured_deployment=configured
    )
    print(f"Saved evaluation results: {out_path}")
    return results


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Run only the first N cases (smoke test).",
    )
    parser.add_argument(
        "--ids",
        type=str,
        default=None,
        help="Comma-separated case ids to run (e.g. E05,E14).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print cases without calling Foundry.",
    )
    args = parser.parse_args(argv)

    if args.list:
        cases = build_eval_cases()
        print(json.dumps(
            [
                {
                    "id": c.id,
                    "question": c.question,
                    "expected_status": c.expected_status,
                    "expected_message_terms": list(c.expected_message_terms),
                    "expected_trace_ids": list(c.expected_trace_ids),
                    "forbidden_claims": list(c.forbidden_claims),
                    "notes": c.notes,
                }
                for c in cases
            ],
            indent=2,
        ))
        return 0

    if not os.getenv("FOUNDRY_PROJECT_ENDPOINT"):
        print(
            "FOUNDRY_PROJECT_ENDPOINT is not set. "
            "Load .env or export Foundry credentials first.",
            file=sys.stderr,
        )
        return 1

    only_ids = (
        {part.strip() for part in args.ids.split(",") if part.strip()}
        if args.ids
        else None
    )
    results = run_evaluation(limit=args.limit, only_ids=only_ids)
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
