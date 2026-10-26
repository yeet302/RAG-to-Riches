"""
Log connector package.

LogConnector
    ├── LocalLogConnector
    └── AzureLogAnalyticsConnector

Backend selection is controlled by LOG_CONNECTOR (local | azure).
set_connector() still overrides the shared instance for tests.
"""

from __future__ import annotations

import os

from .azure_log_analytics import AzureLogAnalyticsConnector
from .base import LogConnector, LogEvent
from .local import LocalLogConnector, parse_log_events, parse_log_line
from .matching import service_matches

# Default backend used by the Foundry agent tools.
_default_connector: LogConnector | None = None


def _connector_from_env() -> LogConnector:
    raw = os.environ.get("LOG_CONNECTOR")
    choice = (raw or "").strip().lower()
    if choice in ("", "local"):
        return LocalLogConnector()
    if choice == "azure":
        return AzureLogAnalyticsConnector()
    raise ValueError(
        f"Unsupported LOG_CONNECTOR={raw!r}. Expected 'local' or 'azure'."
    )


def get_connector() -> LogConnector:
    """Return the shared connector selected by LOG_CONNECTOR, unless overridden."""
    global _default_connector
    if _default_connector is None:
        _default_connector = _connector_from_env()
    return _default_connector


def set_connector(connector: LogConnector) -> None:
    """Override the default connector (useful for tests)."""
    global _default_connector
    _default_connector = connector


__all__ = [
    "LogConnector",
    "LogEvent",
    "LocalLogConnector",
    "AzureLogAnalyticsConnector",
    "parse_log_line",
    "parse_log_events",
    "service_matches",
    "get_connector",
    "set_connector",
]
