"""Per-investigation deadlines and stage timings for synchronous source/model calls."""
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import threading
import time

log = logging.getLogger("code_agent")
_active = ContextVar("code_investigation_budget", default=None)


class CodeDeadlineExceeded(TimeoutError):
    def __init__(self, stage):
        super().__init__("Code investigation deadline exceeded")
        self.stage = stage


class InvestigationBudget:
    def __init__(self, seconds, request_id="-"):
        self.deadline = time.monotonic() + seconds
        self.request_id = request_id
        self.cancelled = threading.Event()
        self.stage = "initialization"

    def timeout(self, maximum=30):
        remaining = self.deadline - time.monotonic()
        if self.cancelled.is_set() or remaining <= 0:
            raise CodeDeadlineExceeded(self.stage)
        return min(maximum, remaining)


@contextmanager
def use_budget(budget):
    token = _active.set(budget)
    try:
        yield
    finally:
        _active.reset(token)


def network_timeout(maximum=30):
    budget = _active.get()
    return budget.timeout(maximum) if budget else maximum


def azure_options(maximum=30):
    timeout = network_timeout(maximum)
    return dict(connection_timeout=timeout, read_timeout=timeout, retry_total=0)


def perform(stage, function, *args, **kwargs):
    budget = _active.get()
    if budget is None:
        return function(*args, **kwargs)
    budget.stage = stage
    budget.timeout()
    started = time.monotonic()
    outcome = "error"
    log.info("request_id=%s stage=%s event=started", budget.request_id, stage)
    try:
        result = function(*args, **kwargs)
        budget.timeout()
        outcome = "ok"
        return result
    finally:
        log.info("request_id=%s stage=%s event=finished outcome=%s latency_ms=%s",
                 budget.request_id, stage, outcome, round((time.monotonic()-started)*1000))
