"""Unit tests for AzureLogAnalyticsConnector (no live Azure calls)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from azure.core.exceptions import HttpResponseError
from azure.monitor.query import LogsQueryStatus

from connectors import AzureLogAnalyticsConnector
from connectors.azure_log_analytics import (
    AzureLogAnalyticsError,
    AzureLogAnalyticsQueryError,
    azure_row_to_log_event,
    escape_kql_string,
    kql_datetime_literal,
)


WORKSPACE_ID = "test-workspace-id"


class FakeTable:
    def __init__(self, rows, columns=None):
        self.columns = columns or [
            "TimeGenerated",
            "ServiceName",
            "SeverityText",
            "TraceId",
            "SpanId",
            "Body",
        ]
        self.rows = rows


class FakeSuccess:
    def __init__(self, rows=None, columns=None, tables=None):
        self.status = LogsQueryStatus.SUCCESS
        if tables is not None:
            self.tables = tables
        elif rows is None:
            self.tables = []
        else:
            self.tables = [FakeTable(rows, columns=columns)]


class FakePartial:
    status = LogsQueryStatus.PARTIAL
    tables = []
    partial_data = []
    partial_error = SimpleNamespace(code="PartialError", message="query timed out")


class RecordingClient:
    def __init__(self, result=None):
        self.result = FakeSuccess([]) if result is None else result
        self.calls: list[dict] = []

    def query_workspace(self, workspace_id, query, *, timespan, **kwargs):
        self.calls.append(
            {
                "workspace_id": workspace_id,
                "query": query,
                "timespan": timespan,
                "kwargs": kwargs,
            }
        )
        if isinstance(self.result, BaseException):
            raise self.result
        if callable(self.result):
            return self.result(workspace_id, query, timespan)
        return self.result


def _dt(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _row(
    timestamp: str,
    service: str = "ad",
    severity: str = "INFO",
    trace_id: str = "abc",
    span_id: str = "def",
    body: str = "hello",
) -> dict:
    return {
        "TimeGenerated": _dt(timestamp),
        "ServiceName": service,
        "SeverityText": severity,
        "TraceId": trace_id,
        "SpanId": span_id,
        "Body": body,
    }


def _connector(client: RecordingClient) -> AzureLogAnalyticsConnector:
    return AzureLogAnalyticsConnector(workspace_id=WORKSPACE_ID, client=client)


class AzureRowConversionTests(unittest.TestCase):
    def test_maps_azure_columns_onto_log_event(self):
        stamp = datetime(2026, 10, 17, 22, 33, 52, tzinfo=timezone.utc)
        event = azure_row_to_log_event(
            {
                "TimeGenerated": stamp,
                "ServiceName": "ad",
                "SeverityText": "ERROR",
                "TraceId": "abc123",
                "SpanId": "def456",
                "Body": "GetAds Failed",
            }
        )
        self.assertEqual(event.timestamp, stamp.isoformat())
        self.assertEqual(event.service, "ad")
        self.assertEqual(event.severity, "ERROR")
        self.assertEqual(event.trace_id, "abc123")
        self.assertEqual(event.span_id, "def456")
        self.assertEqual(event.message, "GetAds Failed")
        self.assertEqual(event.raw_text, "GetAds Failed")

    def test_blank_and_missing_fields_become_none(self):
        event = azure_row_to_log_event(
            {
                "TimeGenerated": None,
                "ServiceName": "  ",
                "SeverityText": "",
                "TraceId": None,
                "SpanId": None,
                "Body": None,
            }
        )
        self.assertIsNone(event.timestamp)
        self.assertIsNone(event.service)
        self.assertIsNone(event.severity)
        self.assertIsNone(event.trace_id)
        self.assertIsNone(event.span_id)
        self.assertIsNone(event.message)
        self.assertEqual(event.raw_text, "")

    def test_sequence_row_uses_projected_column_order(self):
        stamp = datetime(2026, 10, 17, 22, 31, 24, tzinfo=timezone.utc)
        event = azure_row_to_log_event(
            [stamp, "cart", "WARN", "trace", "span", "timeout"]
        )
        self.assertEqual(event.service, "cart")
        self.assertEqual(event.severity, "WARN")
        self.assertEqual(event.message, "timeout")
        self.assertEqual(event.trace_id, "trace")


class KqlFilterConstructionTests(unittest.TestCase):
    def setUp(self):
        self.client = RecordingClient()
        self.connector = _connector(self.client)

    def test_escape_kql_string_doubles_quotes(self):
        self.assertEqual(escape_kql_string("it's broken"), "'it''s broken'")
        self.assertEqual(escape_kql_string("plain"), "'plain'")

    def test_search_kql_includes_all_filters_and_escapes_values(self):
        start = datetime(2026, 10, 17, 22, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 10, 17, 23, 0, 0, tzinfo=timezone.utc)
        kql = self.connector._build_search_kql(
            query="GetAds | it's failed",
            service="ad-service",
            severity="ERROR",
            start_dt=start,
            end_dt=end,
            trace_id="ab'c",
            limit=25,
        )

        self.assertTrue(kql.startswith("OTelLogs"))
        self.assertIn("tostring(Body) contains 'GetAds | it''s failed'", kql)
        self.assertIn("SeverityText =~ 'ERROR'", kql)
        self.assertIn("TraceId =~ 'ab''c'", kql)
        self.assertIn(f"TimeGenerated >= {kql_datetime_literal(start)}", kql)
        self.assertIn(f"TimeGenerated <= {kql_datetime_literal(end)}", kql)
        self.assertIn("ServiceName =~ 'ad-service'", kql)
        self.assertIn("_svc_core == 'ad'", kql)
        self.assertIn("order by TimeGenerated desc", kql)
        self.assertIn("take 25", kql)
        self.assertIn(
            "project TimeGenerated, ServiceName, SeverityText, TraceId, SpanId, Body",
            kql,
        )

    def test_search_logs_sends_constructed_kql_and_explicit_timespan(self):
        self.connector.search_logs(
            query="timeout",
            service="ad",
            severity="WARN",
            start_time="2026-10-17T22:00:00Z",
            end_time="2026-10-17T23:00:00Z",
            trace_id="abc123",
            limit=10,
        )
        self.assertEqual(len(self.client.calls), 1)
        call = self.client.calls[0]
        self.assertEqual(call["workspace_id"], WORKSPACE_ID)
        query = call["query"]
        self.assertIn("tostring(Body) contains 'timeout'", query)
        self.assertIn("SeverityText =~ 'WARN'", query)
        self.assertIn("TraceId =~ 'abc123'", query)
        self.assertIn("order by TimeGenerated desc", query)
        self.assertIn("take 10", query)
        start, end = call["timespan"]
        self.assertEqual(start, _dt("2026-10-17T22:00:00Z"))
        self.assertEqual(end, _dt("2026-10-17T23:00:00Z"))

    def test_unfiltered_search_still_projects_orders_and_limits(self):
        kql = self.connector._build_search_kql(limit=50)
        self.assertNotIn("| where tostring(Body) contains", kql)
        self.assertNotIn("SeverityText", kql.split("project")[0])
        self.assertIn("order by TimeGenerated desc", kql)
        self.assertIn("take 50", kql)


class SearchLogsRecentLimitTests(unittest.TestCase):
    def test_search_logs_returns_most_recent_matches_in_chronological_order(self):
        # Azure would `order by TimeGenerated desc | take 3` and return the
        # newest matches newest-first. The connector reorders them ascending.
        client = RecordingClient(
            FakeSuccess(
                [
                    _row("2026-10-17T22:33:52Z", body="newest"),
                    _row("2026-10-17T22:32:10Z", body="middle"),
                    _row("2026-10-17T22:31:24Z", body="oldest_kept"),
                ]
            )
        )
        connector = _connector(client)
        events = connector.search_logs(query="GetAds", limit=3)

        self.assertEqual(
            [event["message"] for event in events],
            ["oldest_kept", "middle", "newest"],
        )
        query = client.calls[0]["query"]
        self.assertIn("order by TimeGenerated desc", query)
        self.assertIn("take 3", query)
        desc_at = query.index("order by TimeGenerated desc")
        take_at = query.index("take 3")
        self.assertLess(desc_at, take_at)


class TraceQueryOrderingTests(unittest.TestCase):
    def test_get_trace_logs_returns_chronological_order(self):
        client = RecordingClient(
            FakeSuccess(
                [
                    _row("2026-10-17T22:33:52Z", body="third"),
                    _row("2026-10-17T22:31:24Z", body="first"),
                    _row("2026-10-17T22:32:10Z", body="second"),
                ]
            )
        )
        connector = _connector(client)
        events = connector.get_trace_logs("abc")

        self.assertEqual(
            [event["message"] for event in events],
            ["first", "second", "third"],
        )
        self.assertEqual(
            [event["timestamp"] for event in events],
            [
                _dt("2026-10-17T22:31:24Z").isoformat(),
                _dt("2026-10-17T22:32:10Z").isoformat(),
                _dt("2026-10-17T22:33:52Z").isoformat(),
            ],
        )
        kql = client.calls[0]["query"]
        self.assertIn("TraceId =~ 'abc'", kql)
        self.assertIn("order by TimeGenerated asc", kql)
        self.assertNotIn("| take ", kql)

    def test_empty_trace_id_does_not_query_azure(self):
        client = RecordingClient()
        connector = _connector(client)
        self.assertEqual(connector.get_trace_logs(""), [])
        self.assertEqual(client.calls, [])


class EmptyResultTests(unittest.TestCase):
    def test_success_with_no_tables_returns_empty_list(self):
        client = RecordingClient(FakeSuccess(rows=None))
        connector = _connector(client)
        self.assertEqual(connector.search_logs(query="missing"), [])
        self.assertEqual(len(client.calls), 1)

    def test_success_with_empty_rows_returns_empty_list(self):
        client = RecordingClient(FakeSuccess(rows=[]))
        connector = _connector(client)
        self.assertEqual(connector.get_trace_logs("nope"), [])

    def test_limit_less_than_one_returns_empty_without_querying(self):
        client = RecordingClient()
        connector = _connector(client)
        self.assertEqual(connector.search_logs(query="x", limit=0), [])
        self.assertEqual(connector.search_logs(query="x", limit=-5), [])
        self.assertEqual(client.calls, [])

    def test_get_log_context_without_trace_or_timestamp_returns_empty(self):
        client = RecordingClient()
        connector = _connector(client)
        self.assertEqual(connector.get_log_context(), [])
        self.assertEqual(client.calls, [])


class QueryFailureTests(unittest.TestCase):
    def test_partial_result_raises_useful_error(self):
        client = RecordingClient(FakePartial())
        connector = _connector(client)
        with self.assertRaises(AzureLogAnalyticsQueryError) as ctx:
            connector.search_logs(query="error")
        message = str(ctx.exception)
        self.assertIn("partial", message.lower())
        self.assertIn("query timed out", message)

    def test_http_failure_is_wrapped(self):
        client = RecordingClient(HttpResponseError(message="workspace not found"))
        connector = _connector(client)
        with self.assertRaises(AzureLogAnalyticsQueryError) as ctx:
            connector.list_services()
        self.assertIn("workspace not found", str(ctx.exception))

    def test_missing_workspace_id_raises_before_querying(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(AzureLogAnalyticsError):
                AzureLogAnalyticsConnector(client=RecordingClient())


class LogContextSemanticsTests(unittest.TestCase):
    def test_trace_id_only_uses_trace_query(self):
        client = RecordingClient(FakeSuccess([_row("2026-10-17T22:31:24Z")]))
        connector = _connector(client)
        events = connector.get_log_context(trace_id="abc")
        self.assertEqual(len(events), 1)
        self.assertIn("TraceId =~ 'abc'", client.calls[0]["query"])
        self.assertNotIn("TimeGenerated >=", client.calls[0]["query"])

    def test_window_prefers_matching_trace_then_falls_back(self):
        window_rows = [
            _row("2026-10-17T22:31:20Z", trace_id="other", body="other"),
            _row("2026-10-17T22:31:24Z", trace_id="abc", body="match"),
            _row("2026-10-17T22:31:28Z", trace_id="other", body="later"),
        ]
        client = RecordingClient(FakeSuccess(window_rows))
        connector = _connector(client)
        matched = connector.get_log_context(
            trace_id="abc",
            timestamp="2026-10-17T22:31:24Z",
            before_seconds=30,
            after_seconds=30,
        )
        self.assertEqual([event["message"] for event in matched], ["match"])

        client.result = FakeSuccess(
            [
                _row("2026-10-17T22:31:20Z", trace_id="other", body="other"),
                _row("2026-10-17T22:31:28Z", trace_id="other", body="later"),
            ]
        )
        fallback = connector.get_log_context(
            trace_id="abc",
            timestamp="2026-10-17T22:31:24Z",
            before_seconds=30,
            after_seconds=30,
        )
        self.assertEqual(
            [event["message"] for event in fallback],
            ["other", "later"],
        )
        timespan = client.calls[-1]["timespan"]
        center = _dt("2026-10-17T22:31:24Z")
        self.assertEqual(timespan[0], center - timedelta(seconds=30))
        self.assertEqual(timespan[1], center + timedelta(seconds=30))


if __name__ == "__main__":
    unittest.main()
