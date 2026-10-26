"""Log connector abstraction shared by local and future remote backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class LogEvent:
    """Canonical log event returned to the Foundry agent."""

    timestamp: Optional[str]
    service: Optional[str]
    severity: Optional[str]
    trace_id: Optional[str]
    span_id: Optional[str]
    message: Optional[str]
    raw_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LogConnector(ABC):
    """
    Backend-agnostic interface for log investigation tools.

    Concrete implementations (local files today, Azure Log Analytics later)
    must return JSON-serializable LogEvent dictionaries.
    """

    @abstractmethod
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
        """Search and filter log events. All filters are optional and combinable."""

    @abstractmethod
    def get_log_context(
        self,
        trace_id: Optional[str] = None,
        timestamp: Optional[str] = None,
        before_seconds: int = 30,
        after_seconds: int = 30,
    ) -> list[dict[str, Any]]:
        """Return surrounding events for a trace and/or timestamp window."""

    @abstractmethod
    def get_trace_logs(self, trace_id: str) -> list[dict[str, Any]]:
        """Return every log event for a trace ID in chronological order."""

    @abstractmethod
    def list_services(self) -> list[str]:
        """Return distinct service identifiers present in the log source."""
