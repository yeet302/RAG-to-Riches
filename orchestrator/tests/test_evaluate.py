import asyncio
import csv

import pytest

import evaluate
from settings import Settings


def test_evaluation_appends_each_run_and_keeps_fix_unverified(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    settings = evaluate.fixture_settings(corpus)
    path = tmp_path / "results.csv"
    rows = asyncio.run(evaluate.run_cases(evaluate.DEFAULT_CASES, settings, path, 2, "fixture"))
    assert len(rows) == 8
    assert all(row["correct?"] == "Yes (fixture routing/evidence only)" for row in rows)
    asyncio.run(evaluate.run_cases(evaluate.DEFAULT_CASES[:1], settings, path, 1, "configured services"))
    with path.open() as handle:
        saved = list(csv.DictReader(handle))
    assert len(saved) == 9
    assert saved[-1]["correct?"] == "Not reviewed"
    assert all(row["fix worked?"] == "Not tested" and row["request id"] for row in saved)


def test_failed_evaluation_is_recorded_and_next_case_continues(tmp_path, monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("private service body must not be recorded")
    monkeypatch.setattr(evaluate, "investigate", fail)
    rows = asyncio.run(evaluate.run_cases(evaluate.DEFAULT_CASES[:2], Settings(),
                                        tmp_path / "results.csv", 1, "configured services"))
    assert len(rows) == 2
    assert all(row["errors"] == "RuntimeError" for row in rows)
    assert "private service" not in (tmp_path / "results.csv").read_text()


def test_existing_csv_schema_is_preserved(tmp_path):
    path = tmp_path / "results.csv"
    path.write_text("different,columns\n")
    with pytest.raises(ValueError, match="different columns"):
        evaluate.append_row(path, {})
    assert path.read_text() == "different,columns\n"
