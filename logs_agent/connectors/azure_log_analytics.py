"""Azure Log Analytics backend for the existing LogConnector interface.

KQL is constructed only inside this module/class. The LLM never sees or
generates query language; tool arguments are mapped onto a fixed template.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence, Union

from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.monitor.query import LogsQueryClient, LogsQueryStatus

from .base import LogConnector, LogEvent
from .matching import compact_service_name, core_service_name

OTEL_LOGS_TABLE = "OTelLogs"
DEFAULT_LOOKBACK = timedelta(days=7)
PROJECTED_COLUMNS = (
    "TimeGenerated",
    "ServiceName",
    "SeverityText",
    "TraceId",
    "SpanId",
    "Body",
)
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SERVICE_SUFFIXES = (
    ("microservices", 14),
    ("microservice", 12),
    ("services", 8),
    ("service", 7),
    ("server", 6),
    ("svc", 3),
    ("app", 3),
)


class AzureLogAnalyticsError(Exception):
    """Raised when the Azure Log Analytics connector cannot run a query."""


class AzureLogAnalyticsQueryError(AzureLogAnalyticsError):
    """Raised when an Azure Logs query fails or returns only a partial result."""


def escape_kql_string(value: str) -> str:
    """Return a KQL string literal with embedded quotes escaped."""
    return "'" + value.replace("'", "''") + "'"


def parse_query_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse tool-supplied timestamps into timezone-aware datetimes (UTC default)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    text = str(value).strip()
    if not text:
        return None

    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S:%f %z")
    except ValueError:
        pass

    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    iso = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def kql_datetime_literal(dt: datetime) -> str:
    """Render a datetime as a KQL datetime(...) literal in UTC."""
    if dt.tzinfo is None:
        utc = dt.replace(tzinfo=timezone.utc)
    else:
        utc = dt.astimezone(timezone.utc)
    stamp = utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
    return f"datetime({stamp}Z)"


def _empty_to_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _as_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value).strip()
    return text or None


def _row_get(
    row: Any,
    names: Sequence[str],
    columns: Optional[Sequence[str]] = None,
) -> Any:
    if isinstance(row, Mapping):
        for name in names:
            if name in row:
                return row[name]
        return None

    if columns is not None and isinstance(row, (list, tuple)):
        index = {column: i for i, column in enumerate(columns)}
        for name in names:
            if name in index and index[name] < len(row):
                return row[index[name]]
        return None

    for name in names:
        try:
            return row[name]
        except Exception:
            continue

    if isinstance(row, (list, tuple)) and columns is None:
        projected = {column: i for i, column in enumerate(PROJECTED_COLUMNS)}
        for name in names:
            index = projected.get(name)
            if index is not None and index < len(row):
                return row[index]
    return None


def azure_row_to_log_event(
    row: Any,
    columns: Optional[Sequence[str]] = None,
) -> LogEvent:
    """Normalize an Azure OTelLogs row into the canonical LogEvent model."""
    timestamp = _as_optional_str(
        _row_get(row, ("TimeGenerated", "timestamp"), columns)
    )
    service = _as_optional_str(_row_get(row, ("ServiceName", "service"), columns))
    severity = _as_optional_str(
        _row_get(row, ("SeverityText", "severity"), columns)
    )
    trace_id = _as_optional_str(_row_get(row, ("TraceId", "trace_id"), columns))
    span_id = _as_optional_str(_row_get(row, ("SpanId", "span_id"), columns))
    message = _as_optional_str(_row_get(row, ("Body", "message"), columns))

    raw_text = message or ""
    if not raw_text:
        parts = [
            part
            for part in (timestamp, service, severity, trace_id, span_id)
            if part
        ]
        raw_text = " ".join(parts)

    return LogEvent(
        timestamp=timestamp,
        service=service,
        severity=severity,
        trace_id=trace_id,
        span_id=span_id,
        message=message,
        raw_text=raw_text,
    )


class AzureLogAnalyticsConnector(LogConnector):
    """
    Production LogConnector backed by an Azure Log Analytics OTelLogs table.

    LogsQueryClient is injectable so unit tests never talk to Azure.
    """

    def __init__(
        self,
        workspace_id: Optional[str] = None,
        *,
        client: Optional[Any] = None,
        credential: Optional[Any] = None,
        table: str = OTEL_LOGS_TABLE,
        default_lookback: timedelta = DEFAULT_LOOKBACK,
    ) -> None:
        resolved = workspace_id or os.environ.get("AZURE_LOG_ANALYTICS_WORKSPACE_ID")
        if not resolved or not str(resolved).strip():
            raise AzureLogAnalyticsError(
                "Azure Log Analytics workspace ID is required. "
                "Pass workspace_id or set AZURE_LOG_ANALYTICS_WORKSPACE_ID."
            )
        if not _TABLE_NAME_RE.match(table):
            raise AzureLogAnalyticsError(f"Invalid Log Analytics table name: {table!r}")

        self.workspace_id = str(resolved).strip()
        self.table = table
        self.default_lookback = default_lookback
        if client is not None:
            self._client = client
        else:
            self._client = LogsQueryClient(credential or DefaultAzureCredential())

    def search_logs(
        self,
        query: Optional[str] = None,
        service: Optional[str] = None,
        severity: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        trace_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if int(limit) < 1:
            return []

        start_dt = parse_query_timestamp(start_time)
        end_dt = parse_query_timestamp(end_time)
        kql = self._build_search_kql(
            query=query,
            service=service,
            severity=severity,
            start_dt=start_dt,
            end_dt=end_dt,
            trace_id=trace_id,
            limit=int(limit),
        )
        events = self._execute(kql, self._resolve_timespan(start_dt, end_dt))
        events.sort(key=self._event_sort_key)
        return [event.to_dict() for event in events[: int(limit)]]

    def get_log_context(
        self,
        trace_id: Optional[str] = None,
        timestamp: Optional[str] = None,
        before_seconds: int = 30,
        after_seconds: int = 30,
    ) -> list[dict[str, Any]]:
        if not trace_id and not timestamp:
            return []

        if trace_id and not timestamp:
            return self.get_trace_logs(trace_id)

        center = parse_query_timestamp(timestamp) if timestamp else None
        if trace_id and center is None:
            return self.get_trace_logs(trace_id)
        if center is None:
            return []

        window_start = center - timedelta(seconds=before_seconds)
        window_end = center + timedelta(seconds=after_seconds)
        kql = self._build_context_kql(window_start, window_end)
        events = self._execute(kql, (window_start, window_end))

        if trace_id:
            matched = [
                event
                for event in events
                if event.trace_id and event.trace_id.lower() == trace_id.lower()
            ]
            if matched:
                events = matched

        events.sort(key=self._event_sort_key)
        return [event.to_dict() for event in events]

    def get_trace_logs(self, trace_id: str) -> list[dict[str, Any]]:
        if not trace_id or not str(trace_id).strip():
            return []

        kql = self._build_trace_kql(str(trace_id).strip())
        events = self._execute(kql, self._resolve_timespan(None, None))
        events.sort(key=self._event_sort_key)
        return [event.to_dict() for event in events]

    def list_services(self) -> list[str]:
        kql = self._build_list_services_kql()
        events = self._execute(kql, self._resolve_timespan(None, None))
        names = {event.service for event in events if event.service}
        return sorted(names)

    def _build_search_kql(
        self,
        *,
        query: Optional[str] = None,
        service: Optional[str] = None,
        severity: Optional[str] = None,
        start_dt: Optional[datetime] = None,
        end_dt: Optional[datetime] = None,
        trace_id: Optional[str] = None,
        limit: int = 50,
    ) -> str:
        clauses = self._filter_clauses(
            query=query,
            service=service,
            severity=severity,
            start_dt=start_dt,
            end_dt=end_dt,
            trace_id=trace_id,
        )
        return self._assemble_kql(
            clauses,
            order_by="TimeGenerated desc",
            take=limit,
        )

    def _build_trace_kql(self, trace_id: str) -> str:
        clauses = self._filter_clauses(trace_id=trace_id)
        return self._assemble_kql(clauses, order_by="TimeGenerated asc")

    def _build_context_kql(self, start_dt: datetime, end_dt: datetime) -> str:
        clauses = self._filter_clauses(start_dt=start_dt, end_dt=end_dt)
        return self._assemble_kql(clauses, order_by="TimeGenerated asc")

    def _build_list_services_kql(self) -> str:
        return "\n".join(
            [
                self.table,
                "| where isnotempty(ServiceName)",
                "| distinct ServiceName",
                "| order by ServiceName asc",
            ]
        )

    def _filter_clauses(
        self,
        *,
        query: Optional[str] = None,
        service: Optional[str] = None,
        severity: Optional[str] = None,
        start_dt: Optional[datetime] = None,
        end_dt: Optional[datetime] = None,
        trace_id: Optional[str] = None,
    ) -> list[str]:
        clauses: list[str] = []

        text = _empty_to_none(query)
        if text:
            clauses.append(
                f"| where tostring(Body) contains {escape_kql_string(text)}"
            )

        service_name = _empty_to_none(service)
        if service_name:
            clauses.extend(self._service_filter_clauses(service_name))

        severity_text = _empty_to_none(severity)
        if severity_text:
            clauses.append(
                f"| where SeverityText =~ {escape_kql_string(severity_text)}"
            )

        tid = _empty_to_none(trace_id)
        if tid:
            clauses.append(f"| where TraceId =~ {escape_kql_string(tid)}")

        if start_dt is not None:
            clauses.append(
                f"| where TimeGenerated >= {kql_datetime_literal(start_dt)}"
            )
        if end_dt is not None:
            clauses.append(
                f"| where TimeGenerated <= {kql_datetime_literal(end_dt)}"
            )

        return clauses

    def _service_filter_clauses(self, service: str) -> list[str]:
        """Approximate LocalLogConnector service_matches() in KQL."""
        original = escape_kql_string(service)
        compact = escape_kql_string(compact_service_name(service))
        core = escape_kql_string(core_service_name(service))
        case_arms = []
        for suffix, length in _SERVICE_SUFFIXES:
            case_arms.append(
                "    strlen(_svc_compact) > "
                f"{length} and _svc_compact endswith '{suffix}', "
                f"substring(_svc_compact, 0, strlen(_svc_compact) - {length}),"
            )
        case_body = "\n".join(case_arms)
        return [
            "| extend _svc_compact = replace_regex("
            "tolower(tostring(ServiceName)), @'[-_\\s]+', '')",
            f"| extend _svc_core = case(\n{case_body}\n    _svc_compact)",
            f"| where ServiceName =~ {original} "
            f"or _svc_compact == {compact} "
            f"or _svc_core == {core}",
        ]

    def _assemble_kql(
        self,
        clauses: Sequence[str],
        *,
        order_by: Optional[str] = None,
        take: Optional[int] = None,
    ) -> str:
        parts = [self.table, *clauses]
        parts.append(
            "| project TimeGenerated, ServiceName, SeverityText, TraceId, SpanId, Body"
        )
        if order_by:
            parts.append(f"| order by {order_by}")
        if take is not None:
            parts.append(f"| take {int(take)}")
        return "\n".join(parts)

    def _resolve_timespan(
        self,
        start_dt: Optional[datetime],
        end_dt: Optional[datetime],
    ) -> tuple[datetime, datetime]:
        now = datetime.now(timezone.utc)
        if start_dt is not None and end_dt is not None:
            return (start_dt, end_dt)
        if start_dt is not None:
            return (start_dt, max(start_dt, now))
        if end_dt is not None:
            return (end_dt - self.default_lookback, end_dt)
        return (now - self.default_lookback, now)

    def _execute(
        self,
        kql: str,
        timespan: Union[timedelta, tuple[datetime, datetime]],
    ) -> list[LogEvent]:
        try:
            response = self._client.query_workspace(
                workspace_id=self.workspace_id,
                query=kql,
                timespan=timespan,
            )
        except HttpResponseError as exc:
            raise AzureLogAnalyticsQueryError(
                f"Azure Log Analytics query failed: {exc}"
            ) from exc
        except Exception as exc:
            raise AzureLogAnalyticsQueryError(
                f"Azure Log Analytics query failed: {exc}"
            ) from exc

        return self._events_from_response(response)

    def _events_from_response(self, response: Any) -> list[LogEvent]:
        if response is None:
            return []

        status = getattr(response, "status", None)
        if status in (LogsQueryStatus.PARTIAL, "PartialError"):
            error = getattr(response, "partial_error", None)
            code = getattr(error, "code", None) or "PartialError"
            message = getattr(error, "message", None) or str(error) or "partial result"
            raise AzureLogAnalyticsQueryError(
                f"Azure Log Analytics query returned a partial result ({code}): {message}"
            )
        if status in (LogsQueryStatus.FAILURE, "Failure"):
            raise AzureLogAnalyticsQueryError(
                "Azure Log Analytics query failed: status=Failure"
            )

        tables = getattr(response, "tables", None) or []
        events: list[LogEvent] = []
        for table in tables:
            columns = getattr(table, "columns", None)
            rows = getattr(table, "rows", None) or []
            for row in rows:
                events.append(azure_row_to_log_event(row, columns=columns))
        return events

    @staticmethod
    def _event_sort_key(event: LogEvent) -> tuple[datetime, str, str]:
        dt = parse_query_timestamp(event.timestamp)
        if dt is None:
            dt = datetime.min.replace(tzinfo=timezone.utc)
        return (dt, event.trace_id or "", event.span_id or "")
