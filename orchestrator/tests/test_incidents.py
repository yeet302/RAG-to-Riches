"""Home-page incidents: documented planted bugs only, with status from the logs (never guessed)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from incidents import IncidentFeedService, TagSignal, build_kql, load_catalog, query_log_analytics

NOW = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)


def service(signals=None, error=None, catalog=None, calls=None):
    def query(workspace, tags, lookback):
        if calls is not None:
            calls.append((workspace, tuple(tags), lookback))
        if error:
            raise error
        return signals or {}
    return IncidentFeedService(catalog_path=catalog, query=query, clock=lambda: NOW)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name in ("INCIDENTS_WORKSPACE_ID", "AZURE_LOG_ANALYTICS_WORKSPACE_ID", "INCIDENTS_CATALOG_PATH",
                 "INCIDENTS_LOOKBACK_HOURS", "INCIDENTS_ACTIVE_MINUTES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("INCIDENTS_CACHE_SECONDS", "0")


def test_catalog_lists_the_five_documented_planted_bugs():
    catalog = load_catalog()
    assert [e["id"] for e in catalog] == ["B-01", "B-02", "B-03", "B-04", "B-05"]
    assert {e["flag"] for e in catalog} == {"adFailure", "adTargetedFailure", "adHighCpu", "adEmptyResponse", "adManualGc"}
    assert all(e["documented_in"] and e["tag"].startswith("inject:ads-") for e in catalog)


def test_without_a_workspace_nothing_is_marked_active_and_the_ui_says_why():
    feed = service(calls=(calls := [])).feed()
    assert calls == [] and feed.live is False
    assert {i.status for i in feed.incidents} == {"unknown"}
    assert "INCIDENTS_WORKSPACE_ID" in feed.live_detail


def test_status_comes_from_the_logs(monkeypatch):
    monkeypatch.setenv("INCIDENTS_WORKSPACE_ID", "ws-1")
    feed = service({
        "inject:ads-unavailable": TagSignal(42, NOW - timedelta(minutes=5)),
        "inject:ads-cpu": TagSignal(3, NOW - timedelta(days=1)),
    }).feed()
    by_id = {i.id: i for i in feed.incidents}
    assert by_id["B-01"].status == "active" and by_id["B-01"].occurrences == 42
    assert by_id["B-03"].status == "recent"
    assert by_id["B-02"].status == "not_seen" and by_id["B-02"].occurrences == 0
    assert [i.id for i in feed.incidents][:2] == ["B-01", "B-03"]       # active first, then recent
    assert feed.live and "1 active now, 2 of 5 seen in the last 7 days" in feed.live_detail


def test_active_window_is_configurable(monkeypatch):
    monkeypatch.setenv("INCIDENTS_WORKSPACE_ID", "ws-1")
    monkeypatch.setenv("INCIDENTS_ACTIVE_MINUTES", "5")
    feed = service({"inject:ads-unavailable": TagSignal(1, NOW - timedelta(minutes=30))}).feed()
    assert feed.incidents[0].status == "recent"


def test_a_failed_log_check_keeps_the_incidents_but_marks_them_unknown(monkeypatch):
    monkeypatch.setenv("AZURE_LOG_ANALYTICS_WORKSPACE_ID", "ws-1")
    feed = service(error=PermissionError("denied")).feed()
    assert len(feed.incidents) == 5 and {i.status for i in feed.incidents} == {"unknown"}
    assert "PermissionError" in feed.live_detail and "az login" in feed.live_detail


def test_the_query_uses_the_workspace_tags_and_lookback(monkeypatch):
    monkeypatch.setenv("INCIDENTS_WORKSPACE_ID", "ws-9")
    monkeypatch.setenv("INCIDENTS_LOOKBACK_HOURS", "24")
    service(calls=(calls := [])).feed()
    workspace, tags, lookback = calls[0]
    assert workspace == "ws-9" and lookback == timedelta(hours=24) and "inject:ads-gc" in tags


def test_kql_is_fixed_and_only_accepts_inject_tags():
    kql = build_kql(["inject:ads-unavailable", "inject:ads-gc"])
    assert 'tag in ("inject:ads-unavailable", "inject:ads-gc")' in kql and "summarize" in kql
    with pytest.raises(ValueError):
        build_kql(['inject:x") | union SecretTable //'])


def test_query_reads_log_analytics_rows():
    class Client:
        def query_workspace(self, workspace_id, query, timespan):
            columns = [SimpleNamespace(name="tag"), SimpleNamespace(name="occurrences"), SimpleNamespace(name="last_seen")]
            return SimpleNamespace(status="Success", tables=[SimpleNamespace(
                columns=columns, rows=[["inject:ads-cpu", 7, "2026-11-09T02:56:41Z"], ["inject:other", 1, NOW]])])
    signals = query_log_analytics("ws", ["inject:ads-cpu"], timedelta(days=7), client=Client())
    assert signals == {"inject:ads-cpu": TagSignal(7, datetime(2026, 11, 9, 2, 56, 41, tzinfo=timezone.utc))}


def test_partial_results_are_treated_as_a_failed_check():
    class Client:
        def query_workspace(self, **kwargs):
            return SimpleNamespace(status="LogsQueryStatus.PARTIAL", tables=[])
    with pytest.raises(RuntimeError):
        query_log_analytics("ws", ["inject:ads-cpu"], timedelta(days=1), client=Client())


def test_broken_catalog_entries_are_skipped(tmp_path):
    path = tmp_path / "catalog.json"
    good = {"id": "X-1", "title": "t", "severity": "SEV3", "service": "ad", "flag": "f", "tag": "inject:x",
            "symptom": "s", "question": "q?", "documented_in": []}
    path.write_text(json.dumps({"incidents": [good, {**good, "id": "X-2", "tag": "not-a-tag"},
                                              {**good, "id": "X-3", "severity": "SEV9"}, good]}))
    assert [e["id"] for e in load_catalog(path)] == ["X-1"]


def test_results_are_cached(monkeypatch):
    monkeypatch.setenv("INCIDENTS_WORKSPACE_ID", "ws-1")
    monkeypatch.setenv("INCIDENTS_CACHE_SECONDS", "60")
    svc = service(calls=(calls := []))
    svc.feed()
    svc.feed()
    assert len(calls) == 1
