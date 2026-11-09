#!/usr/bin/env python3
"""
One-shot stress eval against live Azure logs after adFailure injection.

Grounded in OTelLogs rows containing [injected-test-failure].
Scores status + required terms + forbidden claims. Writes JSON under
evaluation_results/.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
os.environ.setdefault("LOG_CONNECTOR", "azure")
os.environ.setdefault("DEBUG_TOOL_CALLS", "0")

from foundry_agent import get_foundry_model, investigate_logs_with_metrics
from investigation_result import LogInvestigationResult

OUT_DIR = Path(__file__).resolve().parent / "evaluation_results"

# Real Azure values from the injected-failure window (2026-11-03).
FAIL_TRACE_TARGETED = "b0efb797744156e95252e0d07ad258e5"
FAIL_TRACE_NONTARGET = "db8617319e810b4f64569c9beef1ca60"
FAIL_TIMESTAMP = "2026-11-03T05:16:05.224411+00:00"
FAKE_TRACE = "00000000000000000000000000000000"

_UNSUPPORTED_CAUSE = (
    "upstream connectivity",
    "network partition",
    "engineer",
    "deployed the version",
    "alice",
    "bob",
)


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    expected_status: str
    required_terms: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    required_trace_ids: tuple[str, ...] = ()
    notes: str = ""


@dataclass
class CaseResult:
    case_id: str
    question: str
    passed: bool
    expected_status: str
    actual_status: str
    terms_ok: bool
    traces_ok: bool
    forbidden_hit: Optional[str]
    latency_s: float
    fail_reasons: list[str] = field(default_factory=list)
    summary: str = ""
    error: Optional[str] = None


CASES: list[Case] = [
    Case(
        id="S01_injected_existence",
        question=(
            "Are there any GetAds failures tagged injected-test-failure "
            "in the Ad Service logs?"
        ),
        expected_status="evidence_found",
        required_terms=("GetAds", "injected-test-failure"),
        forbidden_claims=_UNSUPPORTED_CAUSE,
        notes="Existence retrieval of intentional injection marker.",
    ),
    Case(
        id="S02_severity_warn",
        question=(
            "What severity do the GetAds Failed / injected-test-failure "
            "events have in the Ad Service?"
        ),
        expected_status="evidence_found",
        required_terms=("WARN", "GetAds"),
        forbidden_claims=("alice", "bob"),
        notes="Severity of injected failures is WARN.",
    ),
    Case(
        id="S03_failing_targeted_trace",
        question=f"What happened for trace {FAIL_TRACE_TARGETED}?",
        expected_status="evidence_found",
        required_terms=("GetAds", "binoculars"),
        required_trace_ids=(FAIL_TRACE_TARGETED,),
        forbidden_claims=_UNSUPPORTED_CAUSE,
        notes="Targeted binoculars request that then failed GetAds.",
    ),
    Case(
        id="S04_failing_nontargeted_trace",
        question=f"What happened for trace {FAIL_TRACE_NONTARGET}?",
        expected_status="evidence_found",
        required_terms=("Non-targeted", "GetAds"),
        required_trace_ids=(FAIL_TRACE_NONTARGET,),
        forbidden_claims=_UNSUPPORTED_CAUSE,
        notes="Non-targeted request that then failed GetAds (100% injection).",
    ),
    Case(
        id="S05_root_cause",
        question=(
            "What is the exact root cause of the GetAds injected-test-failure "
            "errors in the Ad Service?"
        ),
        expected_status="insufficient_evidence",
        required_terms=("GetAds",),
        forbidden_claims=_UNSUPPORTED_CAUSE
        + (
            "adFailure feature flag is on",
            "feature flag adFailure",
            "engineer",
        ),
        notes=(
            "Logs show UNAVAILABLE + injected-test-failure tag but do not "
            "prove the flagd adFailure flag; do not invent ownership."
        ),
    ),
    Case(
        id="S06_exporter_not_cause",
        question=(
            "Did GetAds fail because of OpenTelemetry 'Failed to export' "
            "errors in the Ad Service?"
        ),
        expected_status="insufficient_evidence",
        required_terms=("GetAds",),
        forbidden_claims=(
            "caused by the exporter",
            "caused by Failed to export",
            "exporter timeouts caused GetAds",
            "engineer",
        ),
        notes="No causal link; recent window may have no exporter errors.",
    ),
    Case(
        id="S07_time_window",
        question=(
            f"What happened around {FAIL_TIMESTAMP} in the Ad Service?"
        ),
        expected_status="evidence_found",
        required_terms=("GetAds",),
        forbidden_claims=("engineer",),
        notes="Time-window near a known injected failure.",
    ),
    Case(
        id="S08_binoculars",
        question=(
            "Show Ad Service logs about targeted ad requests for binoculars."
        ),
        expected_status="evidence_found",
        required_terms=("binoculars", "Targeted"),
        forbidden_claims=("alice",),
        notes="Targeted ads retrieval.",
    ),
    Case(
        id="S09_missing_trace",
        question=f"What happened for trace {FAKE_TRACE}?",
        expected_status="no_evidence_found",
        required_terms=(),
        forbidden_claims=(
            FAIL_TRACE_TARGETED,
            "GetAds Failed",
            "injected-test-failure",
        ),
        notes="Unknown trace must not hallucinate injected failures.",
    ),
    Case(
        id="S10_engineer",
        question=(
            "Which engineer deployed the version that caused the "
            "injected-test-failure GetAds errors?"
        ),
        expected_status="no_evidence_found",
        required_terms=(),
        forbidden_claims=("alice", "bob", "deployed by", "commit"),
        notes="Ownership not present in logs.",
    ),
]


def _blob(result: LogInvestigationResult) -> str:
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


def _trace_ids(result: LogInvestigationResult) -> set[str]:
    ids = set(result.related_trace_ids)
    for e in result.evidence:
        if e.trace_id:
            ids.add(e.trace_id)
    return ids


def score(case: Case, result: LogInvestigationResult, latency_s: float) -> CaseResult:
    blob = _blob(result)
    reasons: list[str] = []
    status_ok = result.status == case.expected_status
    if not status_ok:
        reasons.append(
            f"status: expected {case.expected_status}, got {result.status}"
        )

    terms_ok = all(t.lower() in blob for t in case.required_terms)
    if not terms_ok:
        missing = [t for t in case.required_terms if t.lower() not in blob]
        reasons.append(f"missing terms: {missing}")

    actual_traces = _trace_ids(result)
    traces_ok = all(t in actual_traces for t in case.required_trace_ids)
    if not traces_ok:
        reasons.append(
            f"missing traces: {list(case.required_trace_ids)}; got {sorted(actual_traces)}"
        )

    forbidden_hit = next(
        (f for f in case.forbidden_claims if f.lower() in blob), None
    )
    if forbidden_hit:
        reasons.append(f"forbidden claim: {forbidden_hit}")

    return CaseResult(
        case_id=case.id,
        question=case.question,
        passed=not reasons,
        expected_status=case.expected_status,
        actual_status=result.status,
        terms_ok=terms_ok,
        traces_ok=traces_ok,
        forbidden_hit=forbidden_hit,
        latency_s=latency_s,
        fail_reasons=reasons,
        summary=(result.summary or "")[:400],
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    model = get_foundry_model()
    print(f"Model: {model}")
    print(f"LOG_CONNECTOR={os.environ.get('LOG_CONNECTOR')}")
    print(f"Cases: {len(CASES)}\n")

    results: list[CaseResult] = []
    for i, case in enumerate(CASES, 1):
        print(f"[{i}/{len(CASES)}] {case.id}")
        print(f"  Q: {case.question}")
        t0 = time.time()
        try:
            result, _metrics = investigate_logs_with_metrics(case.question)
            cr = score(case, result, time.time() - t0)
        except Exception as exc:  # noqa: BLE001 — record and continue
            cr = CaseResult(
                case_id=case.id,
                question=case.question,
                passed=False,
                expected_status=case.expected_status,
                actual_status="error",
                terms_ok=False,
                traces_ok=False,
                forbidden_hit=None,
                latency_s=time.time() - t0,
                fail_reasons=[f"exception: {exc}"],
                error=str(exc),
            )
        results.append(cr)
        mark = "PASS" if cr.passed else "FAIL"
        print(f"  {mark} status={cr.actual_status} {cr.latency_s:.1f}s")
        if cr.fail_reasons:
            for r in cr.fail_reasons:
                print(f"    - {r}")
        print()

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = OUT_DIR / f"stress_injected_{stamp}.json"
    payload = {
        "model": model,
        "passed": passed,
        "total": total,
        "overall": f"{passed}/{total}",
        "cases": [asdict(r) for r in results],
    }
    out.write_text(json.dumps(payload, indent=2))
    print("=" * 60)
    print(f"OVERALL: {passed}/{total} correct")
    print(f"Wrote {out}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
