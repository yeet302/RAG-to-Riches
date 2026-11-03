"""Measured request timings; parallel specialist durations overlap."""
from schemas import PerformanceSummary, StepTiming


def summarize(total_ms: float, steps: list[StepTiming]) -> PerformanceSummary:
    slowest = max(steps, key=lambda step: step.latency_ms)
    if slowest.name == "routing":
        ideas = ["Use a faster planning deployment and reduce the planning prompt.",
                 "Compare keyword routing against semantic routing on the accuracy cases before switching."]
    elif slowest.name.startswith("agent:"):
        ideas = ["Profile retrieval and model calls inside the slowest specialist.",
                 "Narrow retrieval to the service and time range; bound returned evidence.",
                 "Keep independent specialists parallel and skip roles that the question does not need."]
    elif slowest.name == "answer_generation":
        ideas = ["Reduce the evidence context and output length while retaining source coverage.",
                 "Compare a faster synthesis deployment or evidence-only mode against answer accuracy."]
    else:
        ideas = ["Profile local evidence processing and reduce duplicate or oversized findings."]
    return PerformanceSummary(total_ms=round(total_ms, 3), steps=steps,
                              slowest_step=slowest.name, recommendations=ideas)
