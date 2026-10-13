"""
Routing: decide which agents to call, and in what order, for a question.

Architecture choice: Option 1, selective routing (see ROUTING.md).
  Stage 1: logs + docs agents, run IN PARALLEL (whichever the question needs)
  Stage 2: code agent, prompted with the merged stage-1 evidence
  Stage 3: orchestrator synthesizes a grounded response with sources

This version is a transparent keyword classifier so the demo is predictable.
Next iteration: replace classify() with an LLM call (Azure AI Foundry) that
returns the same RoutePlan JSON, and keep this as the fallback.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

INCIDENT_WORDS = {"error", "errors", "fail", "failing", "failed", "failure", "down", "broken", "crash",
                  "slow", "latency", "timeout", "timeouts", "cpu", "memory", "spike", "spiking", "500",
                  "unavailable", "not loading", "not showing", "missing", "why", "what's wrong", "wrong",
                  "incident", "outage", "pausing", "pauses"}
HOWTO_WORDS = {"how do i", "how to", "how can i", "what is", "where is the doc", "runbook", "documentation",
               "explain"}
CODE_WORDS = {"code", "file", "function", "line", "commit", "config", "change", "changed", "deploy",
              "deployed", "flag", "where in"}
LOG_WORDS = {"log", "logs", "error", "errors", "trace", "traces", "latency", "cpu", "memory", "spike",
             "timeout", "rate", "since", "started"}


@dataclass
class RoutePlan:
    intent: str                                   # "incident" | "how_to" | "code_lookup" | "unknown"
    stage1: list[str] = field(default_factory=list)   # run in parallel
    stage2: list[str] = field(default_factory=list)   # run after stage 1, gets its evidence
    reason: str = ""


def _has(text: str, words: set[str]) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def classify(question: str) -> RoutePlan:
    q = question.lower()
    if _has(q, HOWTO_WORDS) and not _has(q, {"error", "errors", "failing", "slow", "down"}):
        return RoutePlan("how_to", ["docs"], [], "How-to question: documentation answers it, no live data needed.")
    if _has(q, INCIDENT_WORDS):
        return RoutePlan("incident", ["logs", "docs"], ["code"],
                         "Incident symptoms: check logs and docs/past incidents in parallel, "
                         "then send that evidence to the code agent to find the change or code path.")
    if _has(q, CODE_WORDS):
        return RoutePlan("code_lookup", ["docs"], ["code"], "Code question: docs for context, then code search.")
    if _has(q, LOG_WORDS):
        return RoutePlan("logs_lookup", ["logs"], [], "Log question: logs agent only.")
    return RoutePlan("unknown", ["logs", "docs"], ["code"], "Unclear intent: default to the full incident path.")
