"""
Live Azure Log Analytics connector tests.

These query the real workspace and are excluded from ordinary pytest
collection (tests/integration is not recursed by default).

  pytest -q
  pytest -q -m "not integration"

  pytest -q -m integration tests/integration/test_azure_integration.py
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from connectors.azure_log_analytics import parse_query_timestamp

pytestmark = pytest.mark.integration

_NO_SUCH_LOG = "__azure_integration_test_no_such_log_9f37c2__"
_GETADS_FAILED = "GetAds Failed"
_BINOCULARS = "Targeted ad request received for [binoculars]"


def _require_events(events: list, reason: str) -> list:
    if not events:
        pytest.skip(reason)
    return events


def _haystack(event: dict) -> str:
    return f"{event.get('message') or ''} {event.get('raw_text') or ''}"


class TestAzureServiceDiscovery:
    def test_list_services_includes_ad(self, azure_connector):
        services = azure_connector.list_services()
        lowered = {name.lower() for name in services}
        if "ad" not in lowered:
            pytest.skip(
                "Azure workspace has no 'ad' service in the integration lookback window"
            )
        assert "ad" in lowered


class TestAzureGetAdsFailureRetrieval:
    def test_search_getads_failed_returns_ad_events(self, azure_connector):
        events = _require_events(
            azure_connector.search_logs(
                query=_GETADS_FAILED, service="ad", limit=5
            ),
            "No GetAds Failed events found in Azure (ad service)",
        )
        assert all((event.get("service") or "").lower() == "ad" for event in events)
        assert any(_GETADS_FAILED.lower() in _haystack(event).lower() for event in events)
        present_trace_ids = [
            event.get("trace_id") for event in events if event.get("trace_id")
        ]
        assert all(str(trace_id).strip() for trace_id in present_trace_ids)


class TestAzureSeverityFiltering:
    def test_search_getads_failed_warn(self, azure_connector):
        events = _require_events(
            azure_connector.search_logs(
                query=_GETADS_FAILED,
                service="ad",
                severity="WARN",
                limit=5,
            ),
            "No GetAds Failed WARN events found in Azure (ad service)",
        )
        assert all((event.get("severity") or "").upper() == "WARN" for event in events)


class TestAzureTargetedBinocularRequest:
    def test_search_binoculars_targeted_request(self, azure_connector):
        events = _require_events(
            azure_connector.search_logs(
                query="binoculars", service="ad", limit=10
            ),
            "No binoculars events found in Azure (ad service)",
        )
        assert any(_BINOCULARS.lower() in _haystack(event).lower() for event in events)


class TestAzureTraceReconstruction:
    def test_get_trace_logs_for_discovered_getads_failure(self, azure_connector):
        failures = _require_events(
            azure_connector.search_logs(
                query=_GETADS_FAILED, service="ad", limit=10
            ),
            "No GetAds Failed events found in Azure to discover a trace_id",
        )
        seed = next(
            (
                event
                for event in failures
                if (event.get("trace_id") or "").strip()
            ),
            None,
        )
        if seed is None:
            pytest.skip("GetAds Failed events in Azure have no trace_id values")

        trace_id = seed["trace_id"]
        events = _require_events(
            azure_connector.get_trace_logs(trace_id),
            f"get_trace_logs returned no events for discovered trace_id={trace_id}",
        )
        assert all(
            (event.get("trace_id") or "").lower() == trace_id.lower()
            for event in events
        )
        assert any(_GETADS_FAILED.lower() in _haystack(event).lower() for event in events)


class TestAzureNoEvidence:
    def test_improbable_query_returns_empty_list(self, azure_connector):
        events = azure_connector.search_logs(query=_NO_SUCH_LOG, limit=10)
        assert events == []


class TestAzureTimeFiltering:
    def test_narrow_window_around_known_event(self, azure_connector):
        events = _require_events(
            azure_connector.search_logs(
                query=_GETADS_FAILED, service="ad", limit=5
            ),
            "No GetAds Failed events found in Azure for time-window filtering",
        )
        known = next(
            (event for event in events if parse_query_timestamp(event.get("timestamp"))),
            None,
        )
        if known is None:
            pytest.skip("GetAds Failed events in Azure have no parseable timestamps")

        center = parse_query_timestamp(known["timestamp"])
        window = timedelta(seconds=30)
        start = (center - window).isoformat()
        end = (center + window).isoformat()
        windowed = _require_events(
            azure_connector.search_logs(
                query=_GETADS_FAILED,
                service="ad",
                start_time=start,
                end_time=end,
                limit=50,
            ),
            "Narrow time window around a known Azure event returned no rows",
        )
        known_message = (known.get("message") or "").strip()
        known_trace = (known.get("trace_id") or "").strip()
        matched = [
            event
            for event in windowed
            if (
                known_message
                and (event.get("message") or "").strip() == known_message
            )
            or (
                known_trace
                and (event.get("trace_id") or "").strip().lower() == known_trace.lower()
            )
            or (event.get("timestamp") == known.get("timestamp"))
        ]
        assert matched, (
            "Narrow time window did not return the known event or a matching "
            "GetAds Failed event"
        )
