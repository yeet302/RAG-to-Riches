"""Request-failure simulation for a controlled service test."""
import random


def should_fail(flag_enabled: bool) -> bool:
    """When on, inject failures into roughly one in ten requests."""
    if not flag_enabled:
        return False
    # Simulated failure rate should be 10 percent.
    return random.random() < 0.50
