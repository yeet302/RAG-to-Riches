"""Home-page incidents: the team's documented planted bugs, with live status from the logs.

The list comes from incidents_catalog.json (bugs documented in the flag definitions and the team's
planted-bug tests); nothing is invented. Each planted bug writes its own tag to the Ad Service
logs (for example `[inject:ads-unavailable]`), so one Log Analytics query tells us which ones are
actually happening:

  active    tag logged in the last INCIDENTS_ACTIVE_MINUTES (default 60)
  recent    tag logged within INCIDENTS_LOOKBACK_HOURS (default 168 = 7 days)
  not_seen  the logs were checked and the tag didn't appear
  unknown   the logs couldn't be checked (no workspace configured, no Azure sign-in, query failed)

Settings: INCIDENTS_WORKSPACE_ID (falls back to AZURE_LOG_ANALYTICS_WORKSPACE_ID),
INCIDENTS_CATALOG_PATH, INCIDENTS_LOOKBACK_HOURS, INCIDENTS_ACTIVE_MINUTES, INCIDENTS_CACHE_SECONDS.
Results are cached so the home page doesn't run a query on every load.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from schemas import Incident, IncidentFeed

log = logging.getLogger("orchestrator.incidents")
ROOT = Path(__file__).resolve().parent
DEFAULT_CATALOG = ROOT / "incidents_catalog.json"
TAG_PATTERN = re.compile(r"^inject:[a-z0-9-]+$")
STATUS_ORDER = {"active": 0, "recent": 1, "not_seen": 2, "unknown": 3}


@dataclass(frozen=True)
class TagSignal:
    occurrences: int
    last_seen: datetime


@dataclass(frozen=True)
class LiveResult:
    signals: Optional[dict[str, TagSignal]]   # None = the logs couldn't be checked
    detail: str


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(max(value, low), high)


def load_catalog(path: Optional[Path] = None) -> list[dict[str, Any]]:
    """Read and check the catalog. A broken entry is skipped (and logged), never guessed at."""
    path = Path(path or os.getenv("INCIDENTS_CATALOG_PATH") or DEFAULT_CATALOG)
    if not path.exists():
        return []
    entries = json.loads(path.read_text(encoding="utf-8")).get("incidents", [])
    valid, seen = [], set()
    for entry in entries:
        try:
            Incident.model_validate({**entry, "status": "unknown"})
        except Exception as exc:
            log.warning("skipping incident catalog entry %r: %s", entry.get("id"), exc)
            continue
        if not TAG_PATTERN.match(entry["tag"]) or entry["id"] in seen:
            log.warning("skipping incident catalog entry %r: bad or duplicate tag/id", entry.get("id"))
            continue
        seen.add(entry["id"])
        valid.append(entry)
    return valid


def build_kql(tags: list[str]) -> str:
    """One fixed query: count each known inject tag in the Ad Service logs. Tags are validated."""
    if not tags or not all(TAG_PATTERN.match(t) for t in tags):
        raise ValueError("tags must look like inject:name")
    tag_list = ", ".join(f'"{t}"' for t in tags)
    return (
        "OTelLogs\n"
        '| where tostring(Body) has "inject:"\n'
        '| extend tag = extract(@"\\[(inject:[a-z0-9-]+)\\]", 1, tostring(Body))\n'
        f"| where tag in ({tag_list})\n"
        "| summarize occurrences = count(), last_seen = max(TimeGenerated) by tag"
    )


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def query_log_analytics(workspace_id: str, tags: list[str], lookback: timedelta,
                        client: Any = None) -> dict[str, TagSignal]:
    """Run the tag query. Raises on any failure; the caller turns that into status 'unknown'."""
    if client is None:
        from azure.identity import DefaultAzureCredential
        from azure.monitor.query import LogsQueryClient
        client = LogsQueryClient(DefaultAzureCredential())
    response = client.query_workspace(workspace_id=workspace_id, query=build_kql(tags), timespan=lookback)
    status = str(getattr(response, "status", "Success"))
    if "PARTIAL" in status.upper() or "FAILURE" in status.upper():
        raise RuntimeError(f"Log Analytics returned {status}")
    signals: dict[str, TagSignal] = {}
    for table in getattr(response, "tables", None) or []:
        columns = [getattr(c, "name", c) for c in (getattr(table, "columns", None) or [])]
        for row in getattr(table, "rows", None) or []:
            values = dict(zip(columns, row)) if columns else {}
            tag, count, last = values.get("tag"), values.get("occurrences"), _parse_time(values.get("last_seen"))
            if tag in tags and isinstance(count, int) and last is not None:
                signals[tag] = TagSignal(count, last)
    return signals


class IncidentFeedService:
    """Builds the home-page feed and caches the live check."""

    def __init__(self, catalog_path: Optional[Path] = None,
                 query: Callable[[str, list[str], timedelta], dict[str, TagSignal]] = query_log_analytics,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.catalog_path = catalog_path
        self.query = query
        self.clock = clock
        self._cache: Optional[tuple[float, LiveResult, datetime]] = None
        self._lock = threading.Lock()

    @property
    def workspace_id(self) -> str:
        return (os.getenv("INCIDENTS_WORKSPACE_ID") or os.getenv("AZURE_LOG_ANALYTICS_WORKSPACE_ID") or "").strip()

    def _live(self, tags: list[str], lookback: timedelta) -> tuple[LiveResult, datetime]:
        ttl = _int_env("INCIDENTS_CACHE_SECONDS", 60, 0, 3600)
        with self._lock:
            if self._cache and time.monotonic() - self._cache[0] < ttl:
                return self._cache[1], self._cache[2]
        checked_at = self.clock()
        if not self.workspace_id:
            result = LiveResult(None, "Live status is off: set INCIDENTS_WORKSPACE_ID in orchestrator/.env "
                                      "to check the logs for these incidents.")
        else:
            try:
                signals = self.query(self.workspace_id, tags, lookback)
                result = LiveResult(signals, "")
            except Exception as exc:   # sign-in, permission, network: show it, don't hide incidents
                log.warning("incident live status unavailable: %s", type(exc).__name__)
                result = LiveResult(None, "Live status unavailable: the logs couldn't be checked "
                                          f"({type(exc).__name__}). Check az login and Log Analytics access.")
        with self._lock:
            self._cache = (time.monotonic(), result, checked_at)
        return result, checked_at

    def feed(self) -> IncidentFeed:
        catalog = load_catalog(self.catalog_path)
        lookback_hours = _int_env("INCIDENTS_LOOKBACK_HOURS", 168, 1, 24 * 30)
        active_minutes = _int_env("INCIDENTS_ACTIVE_MINUTES", 60, 1, 24 * 60)
        if not catalog:
            return IncidentFeed(incidents=[], live=False, live_detail="No incident catalog is configured.",
                                lookback_hours=lookback_hours)
        live, checked_at = self._live([e["tag"] for e in catalog], timedelta(hours=lookback_hours))
        incidents = []
        for entry in catalog:
            signal = live.signals.get(entry["tag"]) if live.signals is not None else None
            if live.signals is None:
                status = "unknown"
            elif signal is None:
                status = "not_seen"
            elif checked_at - signal.last_seen <= timedelta(minutes=active_minutes):
                status = "active"
            else:
                status = "recent"
            incidents.append(Incident.model_validate({
                **entry, "status": status,
                "occurrences": signal.occurrences if signal else (0 if live.signals is not None else None),
                "last_seen": signal.last_seen.isoformat() if signal else None,
            }))
        incidents.sort(key=lambda i: (STATUS_ORDER[i.status],
                                      -(_parse_time(i.last_seen) or datetime.min.replace(tzinfo=timezone.utc)).timestamp()
                                      if i.last_seen else 0, i.id))
        if live.signals is not None:
            active = sum(i.status == "active" for i in incidents)
            seen = sum(i.status in {"active", "recent"} for i in incidents)
            detail = (f"Live from Log Analytics: {active} active now, {seen} of {len(incidents)} seen in the "
                      f"last {_window(lookback_hours)}.")
        else:
            detail = live.detail
        return IncidentFeed(incidents=incidents, live=live.signals is not None, live_detail=detail,
                            lookback_hours=lookback_hours, checked_at=checked_at.isoformat())


def _window(hours: int) -> str:
    return f"{hours // 24} days" if hours % 24 == 0 and hours >= 48 else f"{hours} hours"
