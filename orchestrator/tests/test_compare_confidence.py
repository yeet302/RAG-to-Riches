"""The old-vs-new confidence comparison on reviewed planted-bug answers."""
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
    cases = cc.score_rows(rows(), ("B-", "C-"))
    assert [c.request_id for c in cases] == ["r1", "r2", "r3", "r4"]


def test_new_score_separates_right_from_wrong_where_the_old_one_could_not():
    cases = cc.score_rows(rows(), ("B-", "C-"))
    assert {c.old for c in cases} == {95}                     # old: every answer looks the same
    old, new = cc.summarize(cases, "old"), cc.summarize(cases, "new")
    assert old["ranking"] == 0.5 and new["ranking"] == 1.0
    assert new["brier"] < old["brier"] and new["high_but_wrong"] < old["high_but_wrong"] == 2
    assert cc.verdict(old, new)[0]


def test_ranking_and_brier_math():
    assert cc.ranking([90, 10], [1.0, 0.0]) == 1.0
    assert cc.ranking([10, 90], [1.0, 0.0]) == 0.0
    assert cc.ranking([50, 50], [1.0, 0.0]) == 0.5
    assert cc.ranking([50, 60], [1.0, 1.0]) is None           # no right/wrong pair to order
    assert cc.brier([100, 0], [1.0, 0.0]) == 0.0
    assert cc.brier([50], [1.0]) == 0.25


def test_compare_writes_markdown_and_csv_from_a_json_export(tmp_path, capsys):
    export = tmp_path / "accuracy.json"
    export.write_text(json.dumps(rows()))
    out = tmp_path / "CONFIDENCE_COMPARISON.md"
    assert cc.main(["compare", "--source", str(export), "--out", str(out)]) == 0
    text = out.read_text()
    assert "Result: the new score is better" in text and "| B-04 | No | 95% high |" in text
    assert out.with_suffix(".csv").read_text().startswith("case,reviewer_label,old_score")


def test_compare_reads_the_apps_own_accuracy_database(tmp_path):
    store = AccuracyStore(tmp_path / "accuracy.sqlite3")
    for row in rows()[:4]:
        response = CopilotResponse.model_validate(row["response"])
        store.record(response, "Configured services")
        store.review(response.request_id, AccuracyReview(bug=row["bug"], expected_cause="x",
                                                         correct=row["correct"]))
    cases = cc.score_rows(cc.load_rows(str(tmp_path / "accuracy.sqlite3")), ("B-", "C-"))
    assert len(cases) == 4


def test_compare_with_nothing_reviewed_says_what_to_do(tmp_path, capsys):
    export = tmp_path / "accuracy.json"
    export.write_text(json.dumps(rows()[4:5]))
    assert cc.main(["compare", "--source", str(export), "--out", str(tmp_path / "x.md")]) == 2
    assert "Mark Correct?" in capsys.readouterr().out


def test_run_asks_each_question_and_labels_the_recorded_answer(tmp_path, monkeypatch, capsys):
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


def test_shipped_planted_bug_cases_cover_all_five_bugs_and_controls():
    from pathlib import Path
    cases = json.loads((Path(cc.__file__).parent / "eval" / "planted_bugs.json").read_text())
    names = [c["bug"] for c in cases]
    assert names[:5] == ["B-01", "B-02", "B-03", "B-04", "B-05"] and any(n.startswith("C-") for n in names)
    assert all({"bug", "question", "expected_cause", "expected_agents"} <= set(c) for c in cases)


@pytest.mark.parametrize("bad", [0, -1])
def test_repeat_must_be_positive(bad):
    with pytest.raises(SystemExit):
        cc.main(["run", "--repeat", str(bad)])
