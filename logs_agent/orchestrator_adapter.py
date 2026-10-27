"""
Map Logs Agent investigation results onto the orchestrator v1 wire contract.

investigate_logs() and LogInvestigationResult stay the investigation engine.
This module is the only place that speaks AgentRequest / AgentFinding.
"""

from __future__ import annotations

import logging
import json
import os
import time
from collections.abc import Callable
from typing import Optional

from azure.core.exceptions import ClientAuthenticationError, HttpResponseError

from connectors.azure_log_analytics import AzureLogAnalyticsError
from investigation_result import (
    EvidenceItem,
    InvestigationStatus,
    LogInvestigationResult,
)
from orchestrator_contract import AgentFinding, AgentRequest, Evidence

logger = logging.getLogger("logs_agent.orchestrator")

AGENT_NAME = "logs"
GENERIC_ERROR_SUMMARY = "Logs investigation could not be completed."
GENERIC_NO_ACCESS_SUMMARY = "Log Analytics is not available."
GENERIC_NOT_CONFIGURED_SUMMARY = "Logs agent is not configured."


def _sources_searched() -> list[str]:
    choice = (os.environ.get("LOG_CONNECTOR") or "").strip().lower()
    if choice == "azure":
        return ["OTelLogs"]
    return ["local logs"]


def _evidence_summary(item: EvidenceItem) -> str:
    parts: list[str] = []
    if item.service:
        parts.append(item.service)
    if item.severity:
        parts.append(item.severity)
    if item.message:
        parts.append(item.message)
    summary = " ".join(parts).strip()
    return summary or "Log event"


def _evidence_ref(item: EvidenceItem, index: int) -> str:
    timestamp = (item.timestamp or "").strip() or "-"
    service = (item.service or "").strip() or "-"
    trace_id = (item.trace_id or "").strip() or "-"
    span_id = (item.span_id or "").strip() or "-"
    if timestamp == "-" and service == "-" and trace_id == "-" and span_id == "-":
        return f"OTelLogs/event-{index + 1}"
    return f"OTelLogs/{timestamp}/{service}/{trace_id}/{span_id}"


def _unique_ref(ref: str, seen: dict[str, int]) -> str:
    count = seen.get(ref, 0) + 1
    seen[ref] = count
    return ref if count == 1 else f"{ref}#{count}"


def _map_evidence(items: list[EvidenceItem]) -> list[Evidence]:
    seen: dict[str, int] = {}
    mapped: list[Evidence] = []
    for index, item in enumerate(items):
        mapped.append(
            Evidence(
                source_type="log",
                summary=_evidence_summary(item),
                ref=_unique_ref(_evidence_ref(item, index), seen),
                link=None,
                snippet=item.message,
                agent=AGENT_NAME,
            )
        )
    return mapped


def _map_hypotheses(result: LogInvestigationResult) -> list[str]:
    descriptions: list[str] = []
    for hypothesis in result.hypotheses:
        text = (hypothesis.description or "").strip()
        if text:
            descriptions.append(text)
    return descriptions


def _map_status(result: LogInvestigationResult) -> str:
    if result.status == InvestigationStatus.EVIDENCE_FOUND:
        return "ok"
    if result.status == InvestigationStatus.INSUFFICIENT_EVIDENCE:
        return "ok" if result.evidence else "no_evidence"
    if result.status == InvestigationStatus.NO_EVIDENCE_FOUND:
        return "no_evidence"
    return "error"


def to_agent_finding(
    result: LogInvestigationResult,
    *,
    latency_ms: int = 0,
) -> AgentFinding:
    """Convert a LogInvestigationResult into an orchestrator AgentFinding."""
    evidence = _map_evidence(result.evidence)
    status = _map_status(result)
    if status == "ok" and not evidence:
        status = "no_evidence"

    # Orchestrator only treats ok+evidence as claims. Keep investigation
    # summary (including insufficiency) as the specialist takeaway.
    summary = (result.summary or "").strip() or GENERIC_ERROR_SUMMARY
    if status == "error":
        summary = GENERIC_ERROR_SUMMARY

    sources_unavailable: list[str] = []
    if status in {"error", "no_access"}:
        sources_unavailable = [f"{AGENT_NAME} agent reported {status}"]

    confidence = result.confidence if status == "ok" else 0.0

    return AgentFinding(
        agent=AGENT_NAME,
        status=status,
        summary=summary,
        hypotheses=_map_hypotheses(result) if status == "ok" else [],
        evidence=evidence if status in {"ok", "no_evidence"} else [],
        sources_searched=_sources_searched(),
        sources_unavailable=sources_unavailable,
        confidence=max(0.0, min(1.0, confidence)),
        latency_ms=max(0, latency_ms),
    )


def preflight_finding() -> Optional[AgentFinding]:
    """Return a finding if the specialist cannot run; otherwise None."""
    connector = (os.environ.get("LOG_CONNECTOR") or "").strip().lower()
    if connector == "azure":
        workspace = (os.environ.get("AZURE_LOG_ANALYTICS_WORKSPACE_ID") or "").strip()
        if not workspace:
            return AgentFinding(
                agent=AGENT_NAME,
                status="no_access",
                summary=GENERIC_NO_ACCESS_SUMMARY,
                sources_searched=["OTelLogs"],
                sources_unavailable=["Azure Log Analytics"],
            )

    endpoint = (os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or "").strip()
    agent_name = (os.environ.get("FOUNDRY_AGENT_NAME") or "").strip()
    if not endpoint or not agent_name:
        return AgentFinding(
            agent=AGENT_NAME,
            status="error",
            summary=GENERIC_NOT_CONFIGURED_SUMMARY,
            sources_searched=_sources_searched(),
            sources_unavailable=["logs agent is not configured"],
        )
    return None


def _finding_for_exception(exc: BaseException) -> AgentFinding:
    if isinstance(exc, AzureLogAnalyticsError):
        message = str(exc).lower()
        if "workspace" in message or "not configured" in message:
            return AgentFinding(
                agent=AGENT_NAME,
                status="no_access",
                summary=GENERIC_NO_ACCESS_SUMMARY,
                sources_searched=["OTelLogs"],
                sources_unavailable=["Azure Log Analytics"],
            )
        return AgentFinding(
            agent=AGENT_NAME,
            status="error",
            summary=GENERIC_ERROR_SUMMARY,
            sources_searched=["OTelLogs"],
            sources_unavailable=["Azure Log Analytics"],
        )

    status_code = getattr(exc, "status_code", None)
    if isinstance(exc, ClientAuthenticationError) or (
        isinstance(exc, HttpResponseError) and status_code in (401, 403)
    ):
        return AgentFinding(
            agent=AGENT_NAME,
            status="no_access",
            summary=GENERIC_NO_ACCESS_SUMMARY,
            sources_searched=_sources_searched(),
            sources_unavailable=["Azure Log Analytics"],
        )

    return AgentFinding(
        agent=AGENT_NAME,
        status="error",
        summary=GENERIC_ERROR_SUMMARY,
        sources_searched=_sources_searched(),
        sources_unavailable=["logs agent failed"],
    )


def handle_agent_request(
    request: AgentRequest,
    *,
    investigate: Optional[Callable[[str], LogInvestigationResult]] = None,
) -> AgentFinding:
    """
    Run one orchestrator request through the Logs Agent.

    Delegation fields are supplied as JSON data to the production investigator.
    Injected legacy investigators still receive only the question. Context does
    not change tool policy or establish log evidence. Correlation IDs are logged,
    not used as credentials.
    """
    started = time.perf_counter()
    logger.info(
        "request_id=%s incident_id=%s conversation_id=%s",
        request.request_id,
        request.incident_id,
        request.conversation_id,
    )

    blocked = preflight_finding()
    if blocked is not None:
        blocked.latency_ms = max(0, round((time.perf_counter() - started) * 1000))
        return blocked

    production = investigate is None
    if production:
        from foundry_agent import investigate_logs
        investigate = investigate_logs

    try:
        prompt = request.question
        if production:
            prompt = json.dumps({
                'question': request.question,
                'original_question': request.original_question or request.question,
                'task': request.task,
                'upstream_context': request.evidence_bundle,
            })
        result = investigate(prompt)
        finding = to_agent_finding(result)
    except Exception as exc:
        logger.warning(
            "request_id=%s failure_type=%s",
            request.request_id,
            type(exc).__name__,
        )
        finding = _finding_for_exception(exc)

    finding.latency_ms = max(0, round((time.perf_counter() - started) * 1000))
    return finding
