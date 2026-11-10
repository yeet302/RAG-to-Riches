"""Durable investigation results and human accuracy review for local development."""
from __future__ import annotations

from contextlib import contextmanager
import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class AccuracyReview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    bug: str = Field(min_length=1, max_length=300)
    expected_cause: str = Field(min_length=1, max_length=2000)
    correct: Literal["Not reviewed", "Yes", "No", "Partly"] = "Not reviewed"
    fix_worked: Literal["Not tested", "Yes", "No"] = "Not tested"
    notes: str = Field(default="", max_length=2000)


class AccuracyStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                connection.execute("""CREATE TABLE IF NOT EXISTS results (
                    request_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, run_type TEXT NOT NULL,
                    bug TEXT NOT NULL, expected_cause TEXT NOT NULL, correct TEXT NOT NULL,
                    fix_worked TEXT NOT NULL, notes TEXT NOT NULL, response TEXT NOT NULL)""")
                connection.execute("""CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, reviewed_at TEXT NOT NULL,
                    review TEXT NOT NULL)""")
                yield connection
        finally:
            connection.close()

    def record(self, response, run_type: str):
        with self.connect() as connection:
            connection.execute("INSERT OR IGNORE INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                response.request_id, datetime.now(timezone.utc).isoformat(), run_type,
                response.question[:300], "Not established", "Not reviewed", "Not tested", "",
                response.model_copy(update={"accuracy_recorded": True}).model_dump_json()))

    @staticmethod
    def unpack(row):
        result = dict(row)
        result["response"] = json.loads(result["response"])
        return result

    def list(self, limit=100):
        with self.connect() as connection:
            return [self.unpack(row) for row in connection.execute(
                "SELECT * FROM results ORDER BY created_at DESC LIMIT ?", (limit,))]

    def review(self, request_id: str, review: AccuracyReview):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM results WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                return None
            connection.execute("UPDATE results SET bug=?, expected_cause=?, correct=?, fix_worked=?, notes=? WHERE request_id=?",
                (review.bug, review.expected_cause, review.correct, review.fix_worked, review.notes, request_id))
            connection.execute("INSERT INTO reviews(request_id, reviewed_at, review) VALUES (?, ?, ?)",
                (request_id, datetime.now(timezone.utc).isoformat(), review.model_dump_json()))
            return self.unpack(connection.execute("SELECT * FROM results WHERE request_id=?", (request_id,)).fetchone())

    def export(self):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["bug", "expected cause", "agent answer", "correct?", "fix worked?", "time taken",
                         "request id", "run type", "pipeline status", "slowest step", "step timings ms", "notes"])
        with self.connect() as connection:
            for row in connection.execute("SELECT * FROM results ORDER BY created_at"):
                response = json.loads(row["response"])
                performance = response.get("performance") or {}
                values = [row["bug"], row["expected_cause"], response["answer"], row["correct"], row["fix_worked"],
                          str(performance.get("total_ms", "")) + " ms", row["request_id"], row["run_type"],
                          response["pipeline_status"], performance.get("slowest_step", ""),
                          json.dumps(performance.get("steps", [])), row["notes"]]
                # Keep user/retrieved strings as text when opened in spreadsheet apps.
                writer.writerow(["'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
                                 for value in values])
        return stream.getvalue()
