"""Repeatable question-only runs; append every result to an accuracy CSV.

No fix is executed and live diagnosis correctness requires human review.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from dotenv import load_dotenv

from orchestrator import investigate
from settings import Settings

FIELDS = ["bug", "expected cause", "agent answer", "correct?", "fix worked?", "time taken",
          "run date", "run type", "question", "expected agents", "agents used", "routing correct?",
          "pipeline status", "request id", "step timings ms", "slowest step", "speedup ideas", "errors"]
DEFAULT_CASES = [
    {"bug": "CODE-LOOKUP", "question": "Where is the retry function defined?",
     "expected_cause": "Implementation lookup; no incident cause asserted", "expected_agents": ["code"]},
    {"bug": "DOCS-LOOKUP", "question": "How do I configure tracing?",
     "expected_cause": "Documentation lookup; no incident cause asserted", "expected_agents": ["docs"]},
    {"bug": "LOGS-LOOKUP", "question": "Show checkout error logs from the last hour",
     "expected_cause": "Retrieve operational evidence; no incident cause asserted", "expected_agents": ["logs"]},
    {"bug": "CHECKOUT-TIMEOUT", "question": "Checkout keeps timing out. Why?",
     "expected_cause": "Unknown until verified against live evidence", "expected_agents": ["logs", "docs", "code"]},
]


def append_row(path: Path, row: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    populated = path.exists() and path.stat().st_size > 0
    if populated:
        with path.open(newline="", encoding="utf-8") as handle:
            if next(csv.reader(handle)) != FIELDS:
                raise ValueError("Existing CSV has different columns; choose another output path")
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if not populated:
            writer.writeheader()
        writer.writerow(row)


async def run_cases(cases: list[dict], settings: Settings, output: Path, repeat: int,
                    run_type: str) -> list[dict]:
    rows = []
    for case in cases:
        for _ in range(repeat):
            started = time.perf_counter()
            row = {"bug": case["bug"], "expected cause": case["expected_cause"],
                   "question": case["question"], "run date": datetime.now(timezone.utc).isoformat(),
                   "run type": run_type, "expected agents": json.dumps(case.get("expected_agents", [])),
                   "correct?": "Not reviewed", "fix worked?": "Not tested"}
            try:
                response = await investigate(case["question"], settings=settings)
                report = response.performance
                routing_ok = set(response.agents_used) == set(case.get("expected_agents", []))
                row.update({"agent answer": response.answer, "agents used": json.dumps(response.agents_used),
                    "routing correct?": "Yes" if routing_ok else "No",
                    "pipeline status": response.pipeline_status, "request id": response.request_id,
                    "time taken": f"{report.total_ms:.3f} ms",
                    "step timings ms": json.dumps([step.model_dump() for step in report.steps]),
                    "slowest step": report.slowest_step,
                    "speedup ideas": " ".join(report.recommendations),
                    "errors": json.dumps([error.model_dump() for error in response.errors])})
                if run_type == "fixture":
                    evidence_ok = all(text.lower() in response.answer.lower()
                                      for text in case.get("expected_text", []))
                    row["correct?"] = "Yes (fixture routing/evidence only)" if (
                        routing_ok and evidence_ok and bool(response.evidence) and response.pipeline_status == "ok") else "No (fixture checks)"
                elif response.pipeline_status in {"failed", "no_evidence"}:
                    row["correct?"] = "Not assessable: no verified answer"
            except Exception as exc:
                row.update({"pipeline status": "evaluation_error", "correct?": "Not assessable: run failed",
                            "errors": type(exc).__name__,
                            "time taken": f"{(time.perf_counter() - started) * 1000:.3f} ms"})
            append_row(output, row)
            rows.append(row)
            print(json.dumps({key: row.get(key) for key in (
                "bug", "run type", "pipeline status", "routing correct?", "time taken", "slowest step")}), flush=True)
    return rows


def fixture_settings(root: Path) -> Settings:
    for role in ("docs", "logs", "code"):
        (root / role).mkdir()
    (root / "docs" / "tracing.md").write_text(
        "Configure tracing: set OTEL_SERVICE_NAME and OTEL_EXPORTER_OTLP_ENDPOINT.\n"
        "Checkout timeout runbook: inspect upstream_timeout before changing retries.\n")
    (root / "code" / "checkout.py").write_text(
        "def retry():  # checkout upstream_timeout = 1 second\n    pass\n")
    (root / "logs" / "checkout.log").write_text(
        "ERROR checkout timeout upstream after 1 second\n")
    return Settings(mode="local", routing_mode="keyword", answer_mode="evidence",
                    docs_root=root / "docs", logs_root=root / "logs", code_root=root / "code")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, help="JSON list: bug, question, expected_cause, expected_agents")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--env-file", type=Path, help="Load service configuration without printing secrets")
    parser.add_argument("--fixture", action="store_true", help="Explicit offline fixture; never a live accuracy claim")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    if args.fixture and args.env_file:
        parser.error("--fixture and --env-file are mutually exclusive")
    cases = json.loads(args.cases.read_text()) if args.cases else DEFAULT_CASES
    if args.fixture:
        with TemporaryDirectory(prefix="copilot-evaluation-") as tmp:
            rows = asyncio.run(run_cases(cases, fixture_settings(Path(tmp)), args.output, args.repeat, "fixture"))
    else:
        if args.env_file:
            load_dotenv(args.env_file)
        rows = asyncio.run(run_cases(cases, Settings.from_env(), args.output, args.repeat, "configured services"))
    durations = [float(row["time taken"].split()[0]) for row in rows]
    if durations:
        print(f"Recorded {len(rows)} runs; median {statistics.median(durations):.3f} ms; max {max(durations):.3f} ms.")
    return 1 if any(row["pipeline status"] == "evaluation_error" or
                    row["correct?"].startswith("No (") for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
