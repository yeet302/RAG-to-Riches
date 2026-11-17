"""The old-vs-new confidence comparison on reviewed planted-bug answers.

Tests compare_confidence.py: which answers are scored, the ranking / Brier metrics, the verdict,
the Markdown + CSV report, reading the app's SQLite database, and the `run` command.

How these tests work:
  * stored(...) builds an answer the way /api/ask saves it (logs, docs and code findings plus a
    validated AI answer). Only the code agent's finding and its link to the logs change.
  * rows() gives six reviewed rows: r1/r2 right (B-01), r3/r4 wrong (B-04, C-01),
    r5 not reviewed, r6 not a planted-bug case. Both scores are recomputed from these.
  * Network calls are replaced with httpx.MockTransport, so nothing needs the app running.

Run: python -m pytest tests/test_compare_confidence.py -v     (from the orchestrator folder)
"""
from __future__ import annotations

import json

import httpx
import pytest

import compare_confidence as cc
from accuracy import AccuracyReview, AccuracyStore
from schemas import AgentFinding, AnswerSynthesis, CopilotResponse, Evidence, QueryAnalysis

LOG_REF = "OTelLogs/2026-11-09T05:16:05Z/ad/b0ef/1a2b"


def stored(request_id, code_text, linked):
    """An answer as /api/ask stores it: question, findings from all three agents, validated AI answer."""
    logs = AgentFinding(agent="logs", summary="l", confidence=0.8, evidence=[Evidence(
        source_type="log", agent="logs", ref=LOG_REF, summary="GetAds failed",
        snippet="GetAds Failed with status UNAVAILABLE [inject:ads-unavailable]")])
    docs = AgentFinding(agent="docs", summary="d", confidence=0.5, evidence=[Evidence(
        source_type="doc", agent="docs", ref="https://opentelemetry.io/docs/demo/feature-flags/",
        summary="adFailure: Generate an error for GetAds 1/10th of the time")])
    code = AgentFinding(agent="code", summary="c", confidence=0.9,
                        sources_searched=[f"Upstream: {LOG_REF}"] if linked else [],
                        evidence=[Evidence(source_type="code", agent="code", summary=code_text,
                                           ref="src/ad/AdService.java@4a70112:L258-L262")])
    evidence = logs.evidence + docs.evidence + code.evidence
    return CopilotResponse(
        kind="diagnosis", question="Why are GetAds requests failing with UNAVAILABLE errors?", answer="a",
        evidence=evidence, findings=[logs, docs, code], request_id=request_id,
        synthesis=AnswerSynthesis(source="llm", citations=[e.ref for e in evidence]),
        analysis=QueryAnalysis(source="llm", intent="incident", summary="s", reason="r",
                               stage1=["logs", "docs"], stage2=["code"]))


RIGHT = 'Throws Status.UNAVAILABLE.withDescription("inject:ads-unavailable") when adFailure is on'
WRONG = "CPULoad starts busy threads when adHighCpu is enabled"


def rows():
    """Two answers the reviewer marked right (agents agree) and two marked wrong (they don't)."""
    return [
        {"bug": "B-01", "correct": "Yes", "response": stored("r1", RIGHT, True).model_dump()},
        {"bug": "B-01", "correct": "Yes", "response": stored("r2", RIGHT, False).model_dump()},
        {"bug": "B-04", "correct": "No", "response": stored("r3", WRONG, False).model_dump()},
        {"bug": "C-01", "correct": "No", "response": stored("r4", WRONG, False).model_dump()},
        {"bug": "B-02", "correct": "Not reviewed", "response": stored("r5", RIGHT, True).model_dump()},
        {"bug": "CHECKOUT", "correct": "Yes", "response": stored("r6", RIGHT, True).model_dump()},
    ]


def test_only_reviewed_planted_bug_and_control_cases_are_scored():
    """Unreviewed answers (r5) and non-planted-bug cases (r6) are skipped; only r1-r4 are scored."""
    cases = cc.score_rows(rows(), ("B-", "C-"))
    assert [c.request_id for c in cases] == ["r1", "r2", "r3", "r4"]


def test_new_score_separates_right_from_wrong_where_the_old_one_could_not():
    """On the same four answers, v1 gives all of them 95 (ranking 0.5, a coin flip) while v2 ranks every right
    answer above every wrong one (1.0), has lower Brier error and fewer High-but-wrong.
    """
    cases = cc.score_rows(rows(), ("B-", "C-"))
    assert {c.old for c in cases} == {95}                     # old: every answer looks the same
    old, new = cc.summarize(cases, "old"), cc.summarize(cases, "new")
    assert old["ranking"] == 0.5 and new["ranking"] == 1.0
    assert new["brier"] < old["brier"] and new["high_but_wrong"] < old["high_but_wrong"] == 2
    assert cc.verdict(old, new)[0]


def test_ranking_and_brier_math():
    """The metric math on small inputs: ranking 1.0 / 0.0 / 0.5 for right / reversed / tied order, None when
    there is no right-wrong pair; Brier 0.0 for perfect scores and 0.25 for 50 on a right answer.
    """
    assert cc.ranking([90, 10], [1.0, 0.0]) == 1.0
    assert cc.ranking([10, 90], [1.0, 0.0]) == 0.0
    assert cc.ranking([50, 50], [1.0, 0.0]) == 0.5
    assert cc.ranking([50, 60], [1.0, 1.0]) is None           # no right/wrong pair to order
    assert cc.brier([100, 0], [1.0, 0.0]) == 0.0
    assert cc.brier([50], [1.0]) == 0.25


def test_compare_writes_markdown_and_csv_from_a_json_export(tmp_path, capsys):
    """`compare` on a JSON export writes the Markdown report (with the verdict and the B-04 row) and the CSV.

    """
    export = tmp_path / "accuracy.json"
    export.write_text(json.dumps(rows()))
    out = tmp_path / "CONFIDENCE_COMPARISON.md"
    assert cc.main(["compare", "--source", str(export), "--out", str(out)]) == 0
    text = out.read_text()
    assert "Result: the new score is better" in text and "| B-04 | No | 95% high |" in text
    assert out.with_suffix(".csv").read_text().startswith("case,reviewer_label,old_score")


def test_compare_reads_the_apps_own_accuracy_database(tmp_path):
    """`compare` can read reviewed answers straight from the app's SQLite accuracy database."""
    store = AccuracyStore(tmp_path / "accuracy.sqlite3")
    for row in rows()[:4]:
        response = CopilotResponse.model_validate(row["response"])
        store.record(response, "Configured services")
        store.review(response.request_id, AccuracyReview(bug=row["bug"], expected_cause="x",
                                                         correct=row["correct"]))
    cases = cc.score_rows(cc.load_rows(str(tmp_path / "accuracy.sqlite3")), ("B-", "C-"))
    assert len(cases) == 4


def test_compare_with_nothing_reviewed_says_what_to_do(tmp_path, capsys):
    """With nothing reviewed, `compare` exits with code 2 and tells you to mark Correct? in the app."""
    export = tmp_path / "accuracy.json"
    export.write_text(json.dumps(rows()[4:5]))
    assert cc.main(["compare", "--source", str(export), "--out", str(tmp_path / "x.md")]) == 2
    assert "Mark Correct?" in capsys.readouterr().out


def test_run_asks_each_question_and_labels_the_recorded_answer(tmp_path, monkeypatch, capsys):
    """`run` (against a fake server, --repeat 2) posts each question to /api/ask, then labels the recorded
    answer with the bug name and 'Not reviewed' via PATCH /api/accuracy.
    """
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"bug": "B-01", "question": "q?", "expected_cause": "adFailure"}]))
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.method, request.url.path, json.loads(request.content)))
        if request.method == "POST":
            return httpx.Response(200, json={"request_id": "abc12345xyz", "pipeline_status": "ok",
                                             "confidence": 80})
        return httpx.Response(200, json={})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    assert cc.main(["run", "--cases", str(cases), "--repeat", "2"]) == 0
    assert [s[:2] for s in seen] == [("POST", "/api/ask"), ("PATCH", "/api/accuracy/abc12345xyz")] * 2
    assert seen[1][2]["bug"] == "B-01" and seen[1][2]["correct"] == "Not reviewed"


def test_run_can_ask_only_some_cases_and_write_the_orchestrator_tests_sheet(tmp_path, monkeypatch):
    """`run --only B-01 --csv ...` asks just B-01 and writes one row in the Orchestrator tests tab's
    columns: the answer (likely cause + steps with commands), the confidence, its band, and the
    "How N% is calculated" reason.
    """
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([
        {"bug": "B-01", "round": 1, "question": "q1?", "expected_cause": "c", "expected_fix": "flag off"},
        {"bug": "B-02", "round": 1, "question": "q2?", "expected_cause": "c", "expected_fix": "flag off"}]))
    asked = []

    def handler(request: httpx.Request):
        if request.method == "POST":
            asked.append(json.loads(request.content)["question"])
            return httpx.Response(200, json={
                "request_id": "abc12345xyz", "pipeline_status": "ok", "confidence": 83, "confidence_band": "high",
                "likely_cause": "adFailure is on", "answer": "a",
                "steps": [{"title": "Turn off adFailure", "command": "flagd-cli set adFailure off"}],
                "confidence_reasons": ["How 83% is calculated: ... = 83%.", "Logs-code agreement 100%: ..."],
                "confidence_components": {"agreement": 1.0}})
        return httpx.Response(200, json={})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    out = tmp_path / "orchestrator_tests.csv"
    assert cc.main(["run", "--cases", str(cases), "--only", "B-01", "--csv", str(out)]) == 0
    assert asked == ["q1?"]
    import csv as csv_module
    header, row = list(csv_module.reader(out.open(encoding="utf-8")))
    assert header == cc.SHEET_COLUMNS
    assert row[0] == "B-01" and row[3] == "flag off" and row[5] == "83" and row[6] == "high"
    assert "Likely cause: adFailure is on" in row[4]
    assert "Step 1: Turn off adFailure [flagd-cli set adFailure off]" in row[4]
    assert row[7] == "How 83% is calculated: ... = 83%." and row[8] == "100%"


def test_shipped_planted_bug_cases_cover_all_five_bugs_and_controls():
    """eval/planted_bugs.json lists B-01..B-05 first, has a C- control, every case has its four keys,
    and B-01..B-04 are Navya's Round 1 orchestrator tests with their expected fixes."""
    from pathlib import Path
    cases = json.loads((Path(cc.__file__).parent / "eval" / "planted_bugs.json").read_text())
    names = [c["bug"] for c in cases]
    assert names[:5] == ["B-01", "B-02", "B-03", "B-04", "B-05"] and any(n.startswith("C-") for n in names)
    assert all({"bug", "question", "expected_cause", "expected_agents"} <= set(c) for c in cases)
    round_1 = [c for c in cases if c.get("round") == 1]       # Navya's Orchestrator tests, Round 1
    assert [c["bug"] for c in round_1] == ["B-01", "B-02", "B-03", "B-04"]
    assert all(c["expected_fix"] and "demo.flagd.json" in c["expected_fix"] for c in round_1)


@pytest.mark.parametrize("bad", [0, -1])
def test_repeat_must_be_positive(bad):
    """`run --repeat` refuses 0 and negative values."""
    with pytest.raises(SystemExit):
        cc.main(["run", "--repeat", str(bad)])
