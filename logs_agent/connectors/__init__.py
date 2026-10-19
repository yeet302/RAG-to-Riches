"""
Log connector package.

LogConnector
    ├── LocalLogConnector          ← implemented
    └── AzureLogAnalyticsConnector ← future (not implemented)
"""

from .base import LogConnector, LogEvent
from .local import LocalLogConnector, parse_log_events, parse_log_line
from .matching import service_matches

# Default backend used by the Foundry agent tools.
_default_connector: LogConnector | None = None


def get_connector() -> LogConnector:
    """Return the shared LocalLogConnector (swap here for Azure later)."""
    global _default_connector
    if _default_connector is None:
        _default_connector = LocalLogConnector()
    return _default_connector


def set_connector(connector: LogConnector) -> None:
    """Override the default connector (useful for tests)."""
    global _default_connector
    _default_connector = connector


__all__ = [
    "LogConnector",
    "LogEvent",
    "LocalLogConnector",
    "parse_log_line",
    "parse_log_events",
    "service_matches",
    "get_connector",
    "set_connector",
]
