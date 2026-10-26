"""Flexible service-name matching for natural-language filters."""

from __future__ import annotations

import re
from typing import Optional

# Words users often add when referring to a service conversationally.
_SERVICE_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "app",
        "application",
        "log",
        "logs",
        "microservice",
        "server",
        "service",
        "services",
        "svc",
        "the",
    }
)

_SEPARATORS = re.compile(r"[-_\s]+")


def compact_service_name(value: str) -> str:
    """Lowercase and remove separators: 'ad-service' -> 'adservice'."""
    return _SEPARATORS.sub("", value.lower().strip())


def core_service_name(value: str) -> str:
    """
    Compact name with a trailing generic suffix stripped.

    Examples:
        'ad-service' -> 'ad'
        'Ad Service' -> 'ad'
        'cartservice' -> 'cart'
        'frontend' -> 'frontend'
    """
    compact = compact_service_name(value)
    for suffix in (
        "microservices",
        "microservice",
        "services",
        "service",
        "server",
        "svc",
        "app",
    ):
        if compact.endswith(suffix) and len(compact) > len(suffix):
            return compact[: -len(suffix)]
    return compact


def service_matches(event_service: Optional[str], query: Optional[str]) -> bool:
    """
    Return True when a log event's service should match a user/agent filter.

    Accepts exact names and common natural-language variants without being
    tied to any one service (e.g. 'ad', 'ad-service', 'Ad Service' all match
    event service 'ad'; 'cart service' matches 'cart').
    """
    if not query or not query.strip():
        return True
    if not event_service or not event_service.strip():
        return False

    ev = event_service.lower().strip()
    q = query.lower().strip()

    if ev == q:
        return True
    if compact_service_name(ev) == compact_service_name(q):
        return True
    if core_service_name(ev) == core_service_name(q):
        return True

    tokens = [t for t in _SEPARATORS.split(q) if t]
    meaningful = [t for t in tokens if t not in _SERVICE_STOPWORDS] or tokens

    for token in meaningful:
        if ev == token:
            return True
        if compact_service_name(ev) == compact_service_name(token):
            return True
        if core_service_name(ev) == core_service_name(token):
            return True

    return False
