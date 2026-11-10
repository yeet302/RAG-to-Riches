"""Compare the old (v1) and new (v2) confidence scores on the planted bugs.

Step 1: ask the running app each planted-bug question (it records every answer):
    python compare_confidence.py run --cases eval/planted_bugs.json
Step 2: in the app, open "Accuracy results" and mark each answer Correct? Yes / No / Partly.
Step 3: score every reviewed answer with both versions and write the comparison:
    python compare_confidence.py compare

The scores are recomputed from the stored agent findings, so old and new are judged on exactly
the same answers. A better score ranks right answers above wrong ones (ranking), sits close to
1 for right answers and 0 for wrong ones (Brier error, lower is better), and rarely says High
for a wrong answer.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import confidence as new_score
import confidence_legacy as old_score
from schemas import CopilotResponse

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "results" / "accuracy.sqlite3"
LABELS = {"Yes": 1.0, "Partly": 0.5, "No": 0.0}


@dataclass
class Case:
    bug: str
    label: float               # 1 right, 0.5 partly, 0 wrong (from the human review)
    old: int
    old_band: str
    new: int
    new_band: str
    request_id: str


# --------------------------------------------------------------------------- loading reviews

def load_rows(source: str) -> list[dict]:
    """Reviewed runs from the running app (URL), its SQLite file, or a JSON export of /api/accuracy."""
    if source.startswith(("http://", "https://")):
        import httpx
        response = httpx.get(source.rstrip("/") + "/api/accuracy", params={"limit": 200}, timeout=30)
        response.raise_for_status()
        return response.json()
    path = Path(source)
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return [{**dict(row), "response": json.loads(row["response"])}
                for row in connection.execute("SELECT * FROM results ORDER BY created_at")]
    finally:
        connection.close()


def score_rows(rows: Iterable[dict], prefixes: tuple[str, ...]) -> list[Case]:
    cases = []
    for row in rows:
        if row.get("correct") not in LABELS or not str(row.get("bug", "")).startswith(prefixes):
            continue
        response = CopilotResponse.model_validate(row["response"])
        old = old_score.score_confidence(response)
        new = new_score.score_confidence(response)
        cases.append(Case(row["bug"], LABELS[row["correct"]], old.score, old.band, new.score, new.band,
                          response.request_id or row.get("request_id", "")))
    return cases


# --------------------------------------------------------------------------- metrics

def ranking(scores: list[int], labels: list[float]) -> float | None:
    """Share of (more right, less right) answer pairs the score orders correctly; ties count half."""
    wins = pairs = 0.0
    for i, (si, li) in enumerate(zip(scores, labels)):
        for sj, lj in zip(scores[i + 1:], labels[i + 1:]):
            if li == lj:
                continue
            pairs += 1
            hi, lo = (si, sj) if li > lj else (sj, si)
            wins += 1.0 if hi > lo else 0.5 if hi == lo else 0.0
    return wins / pairs if pairs else None


def brier(scores: list[int], labels: list[float]) -> float:
    return sum((s / 100 - y) ** 2 for s, y in zip(scores, labels)) / len(scores)


def summarize(cases: list[Case], which: str) -> dict:
    scores = [getattr(c, which) for c in cases]
    bands = [getattr(c, f"{which}_band") for c in cases]
    labels = [c.label for c in cases]
    right = [s for s, y in zip(scores, labels) if y == 1.0]
    wrong = [s for s, y in zip(scores, labels) if y == 0.0]
    return {
        "ranking": ranking(scores, labels),
        "brier": brier(scores, labels),
        "mean_right": sum(right) / len(right) if right else None,
        "mean_wrong": sum(wrong) / len(wrong) if wrong else None,
        "high_but_wrong": sum(1 for b, y in zip(bands, labels) if y == 0.0 and b == "high"),
        "low_but_right": sum(1 for b, y in zip(bands, labels) if y == 1.0 and b == "low"),
    }


def verdict(old: dict, new: dict) -> tuple[bool, list[str]]:
    checks = []
    if old["ranking"] is not None and new["ranking"] is not None:
        checks.append(("Ranks right answers above wrong ones at least as well",
                       new["ranking"] >= old["ranking"]))
    checks.append(("Lower Brier error (closer to the real outcome)", new["brier"] < old["brier"]))
    checks.append(("No more High scores on wrong answers", new["high_but_wrong"] <= old["high_but_wrong"]))
    return all(ok for _, ok in checks), [f"{'PASS' if ok else 'FAIL'}: {name}" for name, ok in checks]


# --------------------------------------------------------------------------- report

def _fmt(value, pct=False) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0%}" if pct else f"{value:.3f}" if isinstance(value, float) else str(value)


def report(cases: list[Case], source: str) -> tuple[str, bool]:
    old, new = summarize(cases, "old"), summarize(cases, "new")
    better, checks = verdict(old, new)
    right = sum(1 for c in cases if c.label == 1.0)
    wrong = sum(1 for c in cases if c.label == 0.0)
    lines = [
        "# Confidence score: old (v1) vs new (v2) on the planted bugs", "",
        f"Source: `{source}` - {len(cases)} reviewed answers ({right} right, {wrong} wrong, "
        f"{len(cases) - right - wrong} partly). Both scores are recomputed from the same stored "
        "agent findings; the label is the reviewer's \"Correct?\" in Accuracy results.", "",
        "| Measure | Old (v1) | New (v2) | Better is |", "|---|---|---|---|",
        f"| Ranking: right answers scored above wrong ones | {_fmt(old['ranking'], True)} | "
        f"{_fmt(new['ranking'], True)} | higher |",
        f"| Brier error vs. the real outcome | {_fmt(old['brier'])} | {_fmt(new['brier'])} | lower |",
        f"| Average score, right answers | {_fmt(old['mean_right'] and round(old['mean_right']))} | "
        f"{_fmt(new['mean_right'] and round(new['mean_right']))} | higher |",
        f"| Average score, wrong answers | {_fmt(old['mean_wrong'] and round(old['mean_wrong']))} | "
        f"{_fmt(new['mean_wrong'] and round(new['mean_wrong']))} | lower |",
        f"| High score on a wrong answer | {old['high_but_wrong']} | {new['high_but_wrong']} | fewer |",
        f"| Low score on a right answer | {old['low_but_right']} | {new['low_but_right']} | fewer |", "",
        "**Result: the new score is " + ("better" if better else "NOT shown to be better") + ".**", "",
        *[f"- {c}" for c in checks], "",
        "## Per answer", "", "| Case | Reviewer | Old | New | Request |", "|---|---|---|---|---|",
    ]
    names = {1.0: "Yes", 0.5: "Partly", 0.0: "No"}
    for c in sorted(cases, key=lambda c: c.bug):
        lines.append(f"| {c.bug} | {names[c.label]} | {c.old}% {c.old_band} | {c.new}% {c.new_band} | "
                     f"`{c.request_id[:8]}` |")
    if len(cases) < 10:
        lines += ["", f"_Small sample ({len(cases)} answers): rerun with `--repeat` to add trials before "
                      "drawing firm conclusions._"]
    return "\n".join(lines) + "\n", better


def write_csv(cases: list[Case], path: Path):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case", "reviewer_label", "old_score", "old_band", "new_score", "new_band", "request_id"])
        for c in cases:
            writer.writerow([c.bug, c.label, c.old, c.old_band, c.new, c.new_band, c.request_id])


# --------------------------------------------------------------------------- run questions

def run_cases(base_url: str, cases_path: Path, repeat: int) -> int:
    import httpx
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    failures = 0
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=400) as client:
        for case in cases:
            for trial in range(repeat):
                try:
                    answer = client.post("/api/ask", json={"question": case["question"]})
                    answer.raise_for_status()
                    body = answer.json()
                    client.patch(f"/api/accuracy/{body['request_id']}", json={
                        "bug": case["bug"], "expected_cause": case["expected_cause"],
                        "correct": "Not reviewed", "fix_worked": "Not tested",
                        "notes": f"compare_confidence run, trial {trial + 1}"}).raise_for_status()
                    print(f"{case['bug']} trial {trial + 1}: {body['pipeline_status']}, "
                          f"confidence {body.get('confidence')}% - recorded as {body['request_id'][:8]}")
                except httpx.HTTPError as exc:
                    failures += 1
                    print(f"{case['bug']} trial {trial + 1}: FAILED ({type(exc).__name__}: {exc})")
    print("\nNext: open the app -> Accuracy results, set Correct? for each case, then run "
          "`python compare_confidence.py compare`.")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="ask the running app every case question and record the answers")
    run.add_argument("--base-url", default="http://127.0.0.1:8000")
    run.add_argument("--cases", type=Path, default=ROOT / "eval" / "planted_bugs.json")
    run.add_argument("--repeat", type=int, default=1)
    cmp = sub.add_parser("compare", help="score reviewed answers with the old and new definitions")
    cmp.add_argument("--source", default=str(DEFAULT_DB),
                     help="app URL, accuracy.sqlite3 path, or a JSON export of /api/accuracy")
    cmp.add_argument("--cases", default="B-,C-", help="comma-separated case-name prefixes to include")
    cmp.add_argument("--out", type=Path, default=ROOT / "results" / "CONFIDENCE_COMPARISON.md")
    args = parser.parse_args(argv)

    if args.command == "run":
        if args.repeat < 1:
            parser.error("--repeat must be at least 1")
        return run_cases(args.base_url, args.cases, args.repeat)

    cases = score_rows(load_rows(args.source), tuple(p for p in args.cases.split(",") if p))
    if not cases:
        print("No reviewed answers found. Mark Correct? as Yes/No/Partly in Accuracy results first.")
        return 2
    text, better = report(cases, args.source)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    write_csv(cases, args.out.with_suffix(".csv"))
    print(text)
    print(f"Wrote {args.out} and {args.out.with_suffix('.csv')}")
    return 0 if better else 1


if __name__ == "__main__":
    sys.exit(main())
