#!/usr/bin/env python3
"""
Runnable verification of LocalLogConnector against data/ad_service.log.

Usage:
    python verify_connector.py
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

from connectors import LocalLogConnector


def _pct(part: int, whole: int) -> str:
    if whole == 0:
        return "0.0%"
    return f"{100.0 * part / whole:.1f}%"


def _preview(events: list[dict], n: int = 3) -> None:
    print(f"  count={len(events)}")
    for event in events[:n]:
        text = json.dumps(event, default=str)
        print("  ", text[:220] + ("..." if len(text) > 220 else ""))
    if len(events) > n:
        print(f"  ... ({len(events) - n} more)")


def _print_event(title: str, event) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(event.to_dict(), indent=2, default=str))


def main() -> None:
    # Avoid stale lru_cache if the module was reloaded in-process.
    LocalLogConnector._load_events.cache_clear()

    connector = LocalLogConnector()
    events = connector._events
    physical = connector.physical_line_count
    logical = len(events)

    with_ts = sum(1 for e in events if e.timestamp)
    with_sev = sum(1 for e in events if e.severity)
    with_trace = sum(1 for e in events if e.trace_id)
    with_span = sum(1 for e in events if e.span_id)
    multiline = sum(1 for e in events if "\n" in e.raw_text)

    severities = Counter(e.severity for e in events if e.severity)
    services = Counter(e.service for e in events if e.service)

    print(f"Log file: {connector.log_path}")
    print(f"Physical lines:          {physical}")
    print(f"Logical events:          {logical}")
    print(
        f"With timestamp:          {with_ts}/{logical} ({_pct(with_ts, logical)})"
    )
    print(
        f"With severity:           {with_sev}/{logical} ({_pct(with_sev, logical)})"
    )
    print(
        f"With trace_id:           {with_trace}/{logical} ({_pct(with_trace, logical)})"
    )
    print(
        f"With span_id:            {with_span}/{logical} ({_pct(with_span, logical)})"
    )
    print(
        f"Multiline events:        {multiline}/{logical} ({_pct(multiline, logical)})"
    )
    print("Services:", dict(services))
    print("Severities:", dict(severities))

    print('\n=== search_logs(query="error") ===')
    _preview(connector.search_logs(query="error", limit=5))

    print('\n=== search_logs(service="ad") ===')
    _preview(connector.search_logs(service="ad", limit=5))

    severity = "ERROR" if severities.get("ERROR") else next(iter(severities), None)
    print(f'\n=== search_logs(severity="{severity}") ===')
    if severity:
        _preview(connector.search_logs(severity=severity, limit=5))
    else:
        print("  (no severity values present)")

    sample_trace = next((e.trace_id for e in events if e.trace_id), None)
    print(f'\n=== search_logs(trace_id="{sample_trace}") ===')
    if sample_trace:
        _preview(connector.search_logs(trace_id=sample_trace, limit=5))
        print(f'\n=== get_trace_logs("{sample_trace}") ===')
        _preview(connector.get_trace_logs(sample_trace))

        counts: dict[str, int] = defaultdict(int)
        for event in events:
            if event.trace_id:
                counts[event.trace_id] += 1
        multi = max(counts, key=counts.get) if counts else None
        if multi and counts[multi] > 1:
            print(
                f'\n=== get_trace_logs("{multi}") '
                f"(multi-event trace, {counts[multi]} events) ==="
            )
            _preview(connector.get_trace_logs(multi), n=5)
    else:
        print("  (no non-empty trace_id values present)")

    timed = next((e for e in events if e.timestamp and e.severity == "ERROR"), None)
    if timed:
        print("\n=== get_log_context(timestamp=..., before/after=60s) ===")
        ctx = connector.get_log_context(
            timestamp=timed.timestamp,
            before_seconds=60,
            after_seconds=60,
        )
        print(f"  center={timed.timestamp}")
        _preview(ctx, n=2)

    # Example events for manual inspection
    java_ex = next(
        (
            e
            for e in events
            if e.severity == "ERROR" and "\n" in e.raw_text and "Exception" in e.raw_text
        ),
        None,
    )
    app_ev = next(
        (
            e
            for e in events
            if e.trace_id and "oteldemo.AdService" in e.raw_text
        ),
        None,
    )

    if java_ex:
        _print_event("Example grouped Java exception event", java_ex)
    if app_ev:
        _print_event("Example oteldemo.AdService event", app_ev)

    print("\nVerification finished.")


if __name__ == "__main__":
    main()
