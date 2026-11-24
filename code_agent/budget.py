"""Per-investigation deadlines and stage timings for synchronous source/model calls."""
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import threading
import time

log = logging.getLogger("code_agent")
# The shared retriever can serve concurrent investigations. Keep each worker's
# deadline in its own context instead of mutable state on the retriever itself.
_active = ContextVar("code_investigation_budget", default=None)


class CodeDeadlineExceeded(TimeoutError):
    def __init__(self, stage):
        super().__init__("Code investigation deadline exceeded")
        self.stage = stage


class InvestigationBudget:
    """One elapsed-time budget shared by every operation in an investigation."""

    def __init__(self, seconds, request_id="-"):
        # Wall-clock corrections must not extend or shorten an active deadline.
        self.deadline = time.monotonic() + seconds
        self.request_id = request_id
        self.cancelled = threading.Event()
        self.stage = "initialization"

    def timeout(self, maximum=30):
        # Each new request gets the smaller of its own cap and the time left.
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
        # Executor threads are reused; never leak a previous request's budget.
        _active.reset(token)


def network_timeout(maximum=30):
    budget = _active.get()
    return budget.timeout(maximum) if budget else maximum


def azure_options(maximum=30):
    timeout = network_timeout(maximum)
    # SDK retries could otherwise outlive the enclosing investigation deadline.
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
        # A synchronous call may finish after cancellation or deadline expiry.
        # Reject that result before allowing the worker to start another call.
        budget.timeout()
        outcome = "ok"
        return result
    finally:
        # Log fixed stage names and durations, never arguments or source bodies.
        log.info("request_id=%s stage=%s event=finished outcome=%s latency_ms=%s",
                 budget.request_id, stage, outcome, round((time.monotonic()-started)*1000))
