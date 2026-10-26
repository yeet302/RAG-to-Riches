"""
Public tool functions used by the Foundry (and local) log agents.

These functions delegate to the configured LogConnector so the agent never
talks to a concrete storage backend. Swap get_connector() to point at Azure
Log Analytics later without changing tool names or Foundry wiring.
"""

from __future__ import annotations

from typing import Any, Optional

from connectors import get_connector


def search_logs(
    query: Optional[str] = None,
    service: Optional[str] = None,
    severity: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    trace_id: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Search logs with optional text and structured filters.

    Args:
        query: Substring match against message/raw_text (case-insensitive).
        service: Service name or natural-language variant (e.g. "ad",
            "ad-service", "Ad Service"). Matching is flexible.
        severity: Exact severity filter (e.g. "ERROR", "WARN").
        start_time: Inclusive lower bound when timestamps are available.
        end_time: Inclusive upper bound when timestamps are available.
        trace_id: Exact trace ID filter.
        limit: Maximum number of events to return (default 50).
    """
    return get_connector().search_logs(
        query=query,
        service=service,
        severity=severity,
        start_time=start_time,
        end_time=end_time,
        trace_id=trace_id,
        limit=limit,
    )


def list_services() -> list[str]:
    """Return distinct service identifiers present in the configured log source."""
    return get_connector().list_services()


def get_log_context(
    trace_id: Optional[str] = None,
    timestamp: Optional[str] = None,
    before_seconds: int = 30,
    after_seconds: int = 30,
) -> list[dict[str, Any]]:
    """
    Return surrounding log events for a trace and/or timestamp window.

    Prefer trace_id when correlating a request. Supply timestamp to inspect
    a time window (before_seconds / after_seconds).
    """
    return get_connector().get_log_context(
        trace_id=trace_id,
        timestamp=timestamp,
        before_seconds=before_seconds,
        after_seconds=after_seconds,
    )


def get_trace_logs(trace_id: str) -> list[dict[str, Any]]:
    """Return every log event associated with a trace ID, chronologically."""
    return get_connector().get_trace_logs(trace_id=trace_id)


# Keep aligned with foundry_agent.MAX_TRACES_TO_INSPECT.
_DEFAULT_MAX_TRACES = 3


def get_logs_for_traces(
    trace_ids: list[str],
    max_traces: int = _DEFAULT_MAX_TRACES,
) -> dict[str, Any]:
    """
    Fetch logs for up to max_traces distinct IDs via get_trace_logs().

    Deduplicates while preserving order. Intended for root-cause / pattern
    investigations so the agent can inspect a representative set in one call.
    """
    if max_traces < 1:
        return {"inspected_trace_ids": [], "logs_by_trace_id": {}}

    seen: set[str] = set()
    ordered: list[str] = []
    for raw in trace_ids or []:
        if not raw:
            continue
        tid = str(raw).strip()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        ordered.append(tid)
        if len(ordered) >= max_traces:
            break

    connector = get_connector()
    logs_by_trace = {tid: connector.get_trace_logs(tid) for tid in ordered}
    return {
        "inspected_trace_ids": ordered,
        "logs_by_trace_id": logs_by_trace,
    }
