#!/usr/bin/env python3
"""
One-shot stress eval against live Azure logs after cart UI inject.

Grounded in OTelLogs rows containing [inject:cart-items-dropped].
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

_UNSUPPORTED_CAUSE = (
    "upstream connectivity",
    "network partition",
    "engineer",
    "deployed the version",
    "alice",
    "bob",
)

FAKE_TRACE = "00000000000000000000000000000000"


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
        id="C01_injected_existence",
        question=(
            "Are there any frontend cart logs tagged inject:cart-items-dropped?"
        ),
        expected_status="evidence_found",
        required_terms=("inject:cart-items-dropped",),
        forbidden_claims=_UNSUPPORTED_CAUSE,
        notes="Existence retrieval of cart inject marker.",
    ),
    Case(
        id="C02_severity_warn",
        question=(
            "What severity do the inject:cart-items-dropped cart events have?"
        ),
        expected_status="evidence_found",
        required_terms=("WARN", "inject:cart-items-dropped"),
        forbidden_claims=("alice", "bob"),
        notes="Injected cart drops are logged at WARN.",
    ),
    Case(
        id="C03_service_frontend",
        question=(
            "Which service emits the inject:cart-items-dropped messages?"
        ),
        expected_status="evidence_found",
        required_terms=("frontend", "inject:cart-items-dropped"),
        forbidden_claims=_UNSUPPORTED_CAUSE,
        notes="Fingerprint should be attributed to frontend.",
    ),
    Case(
        id="C04_symptom",
        question=(
            "Do the inject:cart-items-dropped logs say cart items were dropped "
            "on GET?"
        ),
        expected_status="evidence_found",
        required_terms=("dropping", "inject:cart-items-dropped"),
        forbidden_claims=_UNSUPPORTED_CAUSE,
        notes="Message text includes dropping N items.",
    ),
    Case(
        id="C05_root_cause_logs_only",
        question=(
            "What is the exact root cause of inject:cart-items-dropped in the "
            "frontend cart API?"
        ),
        expected_status="insufficient_evidence",
        required_terms=("inject:cart-items-dropped",),
        forbidden_claims=_UNSUPPORTED_CAUSE
        + (
            "lines of code",
            "cart.ts intentionally",
            "developer planted",
        ),
        notes=(
            "Logs show the drop fingerprint but do not prove the source-code "
            "defect by themselves."
        ),
    ),
    Case(
        id="C06_missing_trace",
        question=f"What happened for trace {FAKE_TRACE}?",
        expected_status="no_evidence_found",
        required_terms=(),
        forbidden_claims=("inject:cart-items-dropped", "Cart GET dropping"),
        notes="Unknown trace must not hallucinate cart injects.",
    ),
    Case(
        id="C07_engineer",
        question=(
            "Which engineer deployed the version that caused "
            "inject:cart-items-dropped?"
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
        " ".join(e.service or "" for e in result.evidence),
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
    out = OUT_DIR / f"stress_cart_injected_{stamp}.json"
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
