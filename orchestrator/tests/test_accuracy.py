import csv
import io
import sqlite3

import pytest
from fastapi.testclient import TestClient

import main
import orchestrator
from accuracy import AccuracyStore
from agents import LocalSearchAgent
from settings import Settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    corpus = tmp_path / "source"
    corpus.mkdir()
    (corpus / "guide.md").write_text("Tracing configuration uses the OTEL_EXPORTER_OTLP_ENDPOINT variable.\n")
    monkeypatch.setattr(orchestrator, "AGENTS", {"docs": LocalSearchAgent("docs", corpus)})
    monkeypatch.setattr(orchestrator, "SETTINGS", Settings(mode="local", routing_mode="keyword", answer_mode="evidence"))
    with TestClient(main.app) as instance:
        yield instance


def test_question_to_saved_review_to_export_and_restart(client):
    response = client.post("/api/ask", json={"question": "How do I configure tracing?"})
    assert response.status_code == 200
    body = response.json()
    assert body["accuracy_recorded"] and body["agents_used"] == ["docs"]
    rows = client.get("/api/accuracy").json()
    assert len(rows) == 1 and rows[0]["response"]["answer"] == body["answer"]
    assert rows[0]["correct"] == "Not reviewed" and rows[0]["fix_worked"] == "Not tested"
    request_id = body["request_id"]
    review = dict(bug="TRACING-01", expected_cause="OTLP endpoint configuration", correct="Yes",
                  fix_worked="Not tested", notes="Compared returned citation with guide.md")
    updated = client.patch(f"/api/accuracy/{request_id}", json=review)
    assert updated.status_code == 200 and updated.json()["bug"] == "TRACING-01"
    restarted = AccuracyStore(main.ACCURACY.path)
    assert restarted.list()[0]["correct"] == "Yes"
    with restarted.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1
    exported = client.get("/api/accuracy.csv")
    assert "attachment" in exported.headers["content-disposition"]
    saved = list(csv.DictReader(io.StringIO(exported.text)))
    assert len(saved) == 1 and saved[0]["bug"] == "TRACING-01"
    assert saved[0]["agent answer"] == body["answer"]
    assert saved[0]["fix worked?"] == "Not tested"
    assert saved[0]["time taken"] == str(body["performance"]["total_ms"]) + " ms"


def test_partial_and_failed_investigations_are_recorded(client, monkeypatch):
    monkeypatch.setattr(orchestrator, "AGENTS", {})
    response = client.post("/api/ask", json={"question": "Checkout is down"}).json()
    assert response["pipeline_status"] == "failed" and response["accuracy_recorded"]
    assert client.get("/api/accuracy").json()[0]["response"]["errors"]


def test_csv_export_preserves_text_and_neutralizes_formulas(client):
    body = client.post("/api/ask", json={"question": "How do I configure tracing?"}).json()
    review = dict(bug="=1+1", expected_cause="comma, newline\nquoted cause", notes="@formula")
    assert client.patch(f"/api/accuracy/{body['request_id']}", json=review).status_code == 200
    row = list(csv.DictReader(io.StringIO(client.get("/api/accuracy.csv").text)))[0]
    assert row["bug"] == "'=1+1" and row["notes"] == "'@formula"
    assert row["expected cause"] == review["expected_cause"]


def test_invalid_review_cannot_change_the_recorded_answer(client):
    body = client.post("/api/ask", json={"question": "How do I configure tracing?"}).json()
    assert client.patch(f"/api/accuracy/{body['request_id']}", json={
        "bug": "x", "expected_cause": "x", "correct": "Yes", "answer": "invented"}).status_code == 422
    assert client.patch("/api/accuracy/unknown", json={"bug": "x", "expected_cause": "x"}).status_code == 404
    assert client.get("/api/accuracy?limit=10000").status_code == 422


def test_storage_failure_is_visible_without_losing_the_answer(client, monkeypatch):
    def fail(*args):
        raise sqlite3.OperationalError("disk unavailable")
    monkeypatch.setattr(main.ACCURACY, "record", fail)
    body = client.post("/api/ask", json={"question": "How do I configure tracing?"}).json()
    assert body["answer"] and not body["accuracy_recorded"]
