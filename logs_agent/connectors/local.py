"""Local file-backed LogConnector for OpenTelemetry Demo docker compose logs."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from .base import LogConnector, LogEvent
from .matching import service_matches

# docker compose: "ad  | <body>"
_DOCKER_PREFIX = re.compile(r"^(?P<service>\S+)\s+\|\s?(?P<body>.*)$")

# App logger: "2026-10-17 22:33:52 - oteldemo.AdService - msg. trace_id=... span_id=... trace_flags=..."
_APP_LOG = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+-\s+"
    r"(?P<logger>\S+)\s+-\s+"
    r"(?P<msg>.*?)\s+"
    r"trace_id=(?P<trace_id>[0-9a-fA-F]*)\s+"
    r"span_id=(?P<span_id>[0-9a-fA-F]*)"
    r"(?:\s+trace_flags=(?P<trace_flags>[0-9a-fA-F]*))?\s*$"
)

# Java agent: "[otel.javaagent 2026-10-17 22:31:10:289 +0000] [thread] LEVEL logger - msg"
_JAVAAGENT = re.compile(
    r"^\[otel\.javaagent\s+"
    r"(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}:\d+\s+[+-]\d{4})\]\s+"
    r"\[(?P<thread>[^\]]+)\]\s+"
    r"(?P<severity>\w+)\s+"
    r"(?P<logger>\S+)\s+-\s+"
    r"(?P<msg>.*)$"
)

_JVM_WARN = re.compile(r"^(?P<severity>WARNING|ERROR|INFO|DEBUG|SEVERE):\s+(?P<msg>.*)$")

# Stack / exception continuation lines observed in ad_service.log
_STACK_AT = re.compile(r"^\tat ")
_STACK_ELLIPSIS = re.compile(r"^\t\.\.\. \d+ more\s*$")
_CAUSED_BY = re.compile(r"^Caused by:")
_SUPPRESSED = re.compile(r"^Suppressed:")
# e.g. "java.io.InterruptedIOException: timeout"
_EXCEPTION_TYPE = re.compile(
    r"^(?:[a-zA-Z_]\w*\.)+(?:[A-Z]\w*)(?:Error|Exception|Throwable)(?::\s*.*)?$"
)


def _empty_to_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse timestamps found in ad_service.log into timezone-aware datetimes."""
    if not value:
        return None

    value = value.strip()

    # Java agent: "2026-10-17 22:31:10:289 +0000"
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S:%f %z")
    except ValueError:
        pass

    # App logger: "2026-10-17 22:31:24" (no TZ; treat as UTC)
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        pass

    # ISO-ish inputs from tool callers
    iso = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _normalize_timestamp(value: Optional[str]) -> Optional[str]:
    """Store a stable ISO-8601 string when parsing succeeds; else keep original."""
    dt = _parse_timestamp(value)
    if dt is None:
        return _empty_to_none(value)
    return dt.isoformat()


def _split_docker_line(raw_line: str) -> tuple[Optional[str], str]:
    """Return (service, body) for a docker-compose log line."""
    raw_line = raw_line.rstrip("\n")
    prefix = _DOCKER_PREFIX.match(raw_line)
    if prefix:
        return _empty_to_none(prefix.group("service")), prefix.group("body")
    return None, raw_line


def is_continuation_line(body: str) -> bool:
    """True for stack-trace / exception lines that belong to a prior header."""
    if not body:
        return False
    return bool(
        _STACK_AT.match(body)
        or _STACK_ELLIPSIS.match(body)
        or _CAUSED_BY.match(body)
        or _SUPPRESSED.match(body)
        or _EXCEPTION_TYPE.match(body)
    )


def is_event_header(body: str) -> bool:
    """True when body starts a new logical log event."""
    if not body.strip():
        return False
    if is_continuation_line(body):
        return False
    return True


def parse_header_fields(raw_line: str) -> LogEvent:
    """
    Parse structured fields from a single header line.

    Continuation lines are not expected here; callers should only pass headers.
    """
    service, body = _split_docker_line(raw_line)

    app = _APP_LOG.match(body)
    if app:
        return LogEvent(
            timestamp=_normalize_timestamp(app.group("ts")),
            service=service,
            severity=None,
            trace_id=_empty_to_none(app.group("trace_id")),
            span_id=_empty_to_none(app.group("span_id")),
            message=_empty_to_none(app.group("msg")),
            raw_text=raw_line.rstrip("\n"),
        )

    agent = _JAVAAGENT.match(body)
    if agent:
        return LogEvent(
            timestamp=_normalize_timestamp(agent.group("ts")),
            service=service,
            severity=_empty_to_none(agent.group("severity")),
            trace_id=None,
            span_id=None,
            message=_empty_to_none(agent.group("msg")),
            raw_text=raw_line.rstrip("\n"),
        )

    jvm = _JVM_WARN.match(body)
    if jvm:
        return LogEvent(
            timestamp=None,
            service=service,
            severity=_empty_to_none(jvm.group("severity")),
            trace_id=None,
            span_id=None,
            message=_empty_to_none(jvm.group("msg")),
            raw_text=raw_line.rstrip("\n"),
        )

    return LogEvent(
        timestamp=None,
        service=service,
        severity=None,
        trace_id=None,
        span_id=None,
        message=_empty_to_none(body) if body.strip() else None,
        raw_text=raw_line.rstrip("\n"),
    )


# Backwards-compatible alias used by connectors.__init__
parse_log_line = parse_header_fields


def _with_raw_text(event: LogEvent, raw_lines: list[str]) -> LogEvent:
    return LogEvent(
        timestamp=event.timestamp,
        service=event.service,
        severity=event.severity,
        trace_id=event.trace_id,
        span_id=event.span_id,
        message=event.message,
        raw_text="\n".join(raw_lines),
    )


def parse_log_events(lines: list[str]) -> list[LogEvent]:
    """
    Group physical docker-compose lines into logical LogEvents.

    A recognizable header (app log, javaagent, JVM WARNING, or other
    non-continuation line) starts a new event. Stack-trace lines
    (exception type, 'at ...', 'Caused by:', '... N more') append to
    the current event until the next header.
    """
    events: list[LogEvent] = []
    current: Optional[LogEvent] = None
    current_raw: list[str] = []

    def flush() -> None:
        nonlocal current, current_raw
        if current is not None:
            events.append(_with_raw_text(current, current_raw))
        current = None
        current_raw = []

    for line in lines:
        raw = line.rstrip("\n")
        if not raw.strip():
            continue

        _service, body = _split_docker_line(raw)

        # "ad  | " with an empty body is not a stack continuation; skip it.
        if not body.strip():
            continue

        if is_continuation_line(body):
            if current is not None:
                current_raw.append(raw)
            else:
                # Orphan stack/exception line with no preceding header.
                flush()
                current = parse_header_fields(raw)
                current_raw = [raw]
            continue

        # Recognizable header or other non-continuation line → new event.
        flush()
        current = parse_header_fields(raw)
        current_raw = [raw]

    flush()
    return events


class LocalLogConnector(LogConnector):
    """
    Reads a local text log file (default: OpenTelemetry Demo ad_service.log).

    Multiline Java stack traces are grouped into the preceding logical event.
    OpenStack *.log files in data/ are intentionally not loaded; point
    log_path at a different file if needed.
    """

    def __init__(self, log_path: str | Path | None = None):
        if log_path is None:
            log_path = Path(__file__).resolve().parent.parent / "data" / "ad_service.log"
        self.log_path = Path(log_path)
        self.physical_line_count, self._events = self._load_events(
            str(self.log_path.resolve())
        )

    @staticmethod
    @lru_cache(maxsize=4)
    def _load_events(resolved_path: str) -> tuple[int, tuple[LogEvent, ...]]:
        path = Path(resolved_path)
        if not path.exists():
            raise FileNotFoundError(f"Log file not found: {path}")

        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()

        physical = len(lines)
        events = tuple(parse_log_events(lines))
        return physical, events

    def _event_dt(self, event: LogEvent) -> Optional[datetime]:
        return _parse_timestamp(event.timestamp)

    def _matches_filters(
        self,
        event: LogEvent,
        *,
        query: Optional[str],
        service: Optional[str],
        severity: Optional[str],
        start_time: Optional[str],
        end_time: Optional[str],
        trace_id: Optional[str],
    ) -> bool:
        if query:
            haystack = f"{event.message or ''} {event.raw_text}".lower()
            if query.lower() not in haystack:
                return False

        if service:
            if not service_matches(event.service, service):
                return False

        if severity:
            if not event.severity or event.severity.lower() != severity.lower():
                return False

        if trace_id:
            if not event.trace_id or event.trace_id.lower() != trace_id.lower():
                return False

        event_dt = self._event_dt(event)
        start_dt = _parse_timestamp(start_time) if start_time else None
        end_dt = _parse_timestamp(end_time) if end_time else None

        # Time filters only apply when the event has a parseable timestamp.
        if start_dt is not None:
            if event_dt is None or event_dt < start_dt:
                return False
        if end_dt is not None:
            if event_dt is None or event_dt > end_dt:
                return False

        return True

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
        if limit < 1:
            return []

        results: list[dict[str, Any]] = []
        for event in self._events:
            if self._matches_filters(
                event,
                query=query,
                service=service,
                severity=severity,
                start_time=start_time,
                end_time=end_time,
                trace_id=trace_id,
            ):
                results.append(event.to_dict())
                if len(results) >= limit:
                    break
        return results

    def get_trace_logs(self, trace_id: str) -> list[dict[str, Any]]:
        if not trace_id:
            return []

        matched: list[tuple[int, LogEvent]] = []
        for index, event in enumerate(self._events):
            if event.trace_id and event.trace_id.lower() == trace_id.lower():
                matched.append((index, event))

        matched.sort(
            key=lambda item: (
                self._event_dt(item[1]) or datetime.min.replace(tzinfo=timezone.utc),
                item[0],
            )
        )
        return [event.to_dict() for _, event in matched]

    def list_services(self) -> list[str]:
        return sorted({event.service for event in self._events if event.service})

    def get_log_context(
        self,
        trace_id: Optional[str] = None,
        timestamp: Optional[str] = None,
        before_seconds: int = 30,
        after_seconds: int = 30,
    ) -> list[dict[str, Any]]:
        if not trace_id and not timestamp:
            return []

        # Prefer the full chronological trace when a trace_id is provided.
        if trace_id and not timestamp:
            return self.get_trace_logs(trace_id)

        center = _parse_timestamp(timestamp) if timestamp else None

        if trace_id and center is None:
            return self.get_trace_logs(trace_id)

        if center is None:
            return []

        window_start = center - timedelta(seconds=before_seconds)
        window_end = center + timedelta(seconds=after_seconds)

        results: list[tuple[int, LogEvent]] = []
        for index, event in enumerate(self._events):
            event_dt = self._event_dt(event)

            if event_dt is not None:
                in_window = window_start <= event_dt <= window_end
            else:
                in_window = False

            if not in_window:
                continue

            if trace_id:
                if event.trace_id and event.trace_id.lower() == trace_id.lower():
                    results.append((index, event))
                continue

            results.append((index, event))

        if trace_id and not results:
            for index, event in enumerate(self._events):
                event_dt = self._event_dt(event)
                if event_dt is not None and window_start <= event_dt <= window_end:
                    results.append((index, event))

        results.sort(
            key=lambda item: (
                self._event_dt(item[1]) or datetime.min.replace(tzinfo=timezone.utc),
                item[0],
            )
        )
        return [event.to_dict() for _, event in results]
