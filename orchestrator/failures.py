"""Stable error codes for failed dependencies and degraded model stages."""
from schemas import AgentFinding, CopilotResponse, PipelineError


def normalize_failure(finding: AgentFinding) -> AgentFinding:
    if finding.status not in {"error", "no_access"} or finding.error is not None:
        return finding
    denied = finding.status == "no_access"
    finding.error = PipelineError(
        code="AGENT_ACCESS_DENIED" if denied else "AGENT_FAILED", agent=finding.agent,
        stage="dispatch", message=f"The {finding.agent} agent reported an access failure." if denied
                else f"The {finding.agent} agent could not finish the investigation.",
        resolution="Check this agent's source credentials and resource permissions." if denied
                   else "Inspect the agent logs using the request ID, correct the cause, and retry.")
    return finding


def annotate_errors(response: CopilotResponse) -> CopilotResponse:
    errors = [finding.error for finding in response.findings if finding.error is not None]
    if response.analysis and response.analysis.source == "keyword_fallback":
        errors.append(PipelineError(code="PLANNING_FALLBACK", stage="planning",
            message="LLM planning was unavailable; keyword delegation was used.",
            resolution="Check the orchestrator model configuration, Azure access, and planning timeout.",
            retryable=True))
    if response.synthesis and response.synthesis.source == "evidence_fallback":
        errors.append(PipelineError(code="SYNTHESIS_FAILED", stage="synthesis",
            message="The final model answer was unavailable; source evidence was preserved.",
            resolution="Check model access, response validation, and the synthesis timeout, then retry.",
            retryable=True))
    response.errors = errors
    response.partial = response.partial or bool(errors)
    if response.findings and all(f.status in {"error", "no_access"} for f in response.findings):
        response.pipeline_status = "failed"
    elif response.partial or errors:
        response.pipeline_status = "partial"
    elif not response.evidence:
        response.pipeline_status = "no_evidence"
    else:
        response.pipeline_status = "ok"
    return response
