"""
Structured log investigation result for orchestrator consumption.

This schema is intentionally backend-agnostic: it must not depend on local
filenames, line numbers, or a specific LogConnector implementation.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator


class InvestigationStatus(str, Enum):
    EVIDENCE_FOUND = "evidence_found"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NO_EVIDENCE_FOUND = "no_evidence_found"
    ERROR = "error"


class EvidenceItem(BaseModel):
    """One observed log fact. Missing metadata must be null, never invented."""

    timestamp: Optional[str] = None
    service: Optional[str] = None
    severity: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    message: Optional[str] = None


class Hypothesis(BaseModel):
    """An interpretation of evidence — not a directly observed fact."""

    description: str
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_trace_ids: list[str] = Field(default_factory=list)


class LogInvestigationResult(BaseModel):
    """
    Machine-consumable result of a Logs Agent investigation.

    Intended for a future orchestrator, not as a final user-facing answer.
    """

    status: InvestigationStatus
    summary: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    related_trace_ids: list[str] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return value

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_json(self, *, indent: int | None = 2) -> str:
        return self.model_dump_json(indent=indent)


class InvestigationParseError(ValueError):
    """Raised when model output cannot be parsed into LogInvestigationResult."""


_JSON_FENCE = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)


def _extract_json_object(text: str) -> str:
    """
    Pull one JSON object from raw model text (plain or fenced).

    Important: response.output_text concatenates every output_text content
    block with ''.join(...). Two schema-valid objects become '{...}{...}',
    which still starts with '{' and ends with '}'. We must not feed that
    whole string to json.loads — use raw_decode for the first object, or
    prefer per-segment parsing via parse_investigation_result_from_response.
    """
    text = text.strip()
    if not text:
        raise InvestigationParseError("Empty model output")

    fence = _JSON_FENCE.search(text)
    if fence:
        return fence.group(1).strip()

    start = text.find("{")
    if start == -1:
        raise InvestigationParseError(
            "Model output did not contain a JSON object"
        )

    decoder = json.JSONDecoder()
    try:
        _obj, end = decoder.raw_decode(text, start)
    except json.JSONDecodeError as exc:
        raise InvestigationParseError(f"Invalid JSON: {exc}") from exc

    return text[start:end]


def parse_investigation_result(text: str) -> LogInvestigationResult:
    """
    Parse and validate model text into LogInvestigationResult.

    Accepts raw JSON or markdown-fenced JSON. When extra data follows a
    valid object (including a second concatenated object from
    response.output_text aggregation), the first complete object is used.
    Raises InvestigationParseError on failure.
    """
    try:
        payload = _extract_json_object(text)
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise InvestigationParseError(f"Invalid JSON: {exc}") from exc
    except InvestigationParseError:
        raise

    try:
        return LogInvestigationResult.model_validate(data)
    except ValidationError as exc:
        raise InvestigationParseError(str(exc)) from exc


def iter_response_output_text_segments(response: Any) -> list[str]:
    """
    Return each message output_text.content.text separately.

    Mirrors OpenAI Response.output_text aggregation, but without joining,
    so callers can parse schema-constrained segments individually.
    """
    segments: list[str] = []
    for item in getattr(response, "output", None) or []:
        item_type = getattr(item, "type", None)
        if item_type != "message":
            continue
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", None) != "output_text":
                continue
            text = getattr(content, "text", None)
            if text is not None:
                segments.append(text)
    return segments


def describe_response_output_structure(response: Any) -> str:
    """Human-readable summary of response.output item types for debug logs."""
    lines: list[str] = []
    for index, item in enumerate(getattr(response, "output", None) or []):
        item_type = getattr(item, "type", type(item).__name__)
        if item_type == "message":
            content_types = [
                getattr(c, "type", "?") for c in (getattr(item, "content", None) or [])
            ]
            texts = [
                getattr(c, "text", "") or ""
                for c in (getattr(item, "content", None) or [])
                if getattr(c, "type", None) == "output_text"
            ]
            lengths = [len(t) for t in texts]
            lines.append(
                f"[{index}] type=message content_types={content_types} "
                f"output_text_lengths={lengths}"
            )
        else:
            lines.append(f"[{index}] type={item_type}")
    if not lines:
        lines.append("(no output items)")
    return "\n".join(lines)


def parse_investigation_result_from_response(response: Any) -> LogInvestigationResult:
    """
    Prefer SDK-native / per-segment parsing over response.output_text.

    Root cause of 'Extra data': Response.output_text joins every
    output_text block with ''.join(texts). Multiple final-answer segments
    (each a full JSON object) become one invalid concatenated string.

    Strategy:
    1. openai.lib._parsing._responses.parse_response + output_parsed
    2. parse each output_text segment individually
    3. fall back to aggregated output_text (with first-object raw_decode)
    """
    # 1) SDK helper used by responses.parse() — parses each content block.
    try:
        from openai._types import omit
        from openai.lib._parsing._responses import parse_response

        parsed = parse_response(
            text_format=LogInvestigationResult,
            input_tools=omit,
            response=response,
        )
        if parsed.output_parsed is not None:
            return parsed.output_parsed
    except Exception:
        # Fall through to per-segment / aggregated parsing.
        pass

    # 2) Per-segment (avoids ''.join aggregation).
    errors: list[str] = []
    segments = iter_response_output_text_segments(response)
    for index, segment in enumerate(segments):
        try:
            return parse_investigation_result(segment)
        except InvestigationParseError as exc:
            errors.append(f"segment[{index}]: {exc}")

    # 3) Aggregated convenience property (may contain concatenated objects).
    aggregated = getattr(response, "output_text", None) or ""
    if aggregated:
        try:
            return parse_investigation_result(aggregated)
        except InvestigationParseError as exc:
            errors.append(f"output_text: {exc}")

    detail = "; ".join(errors) if errors else "no output_text content"
    raise InvestigationParseError(
        f"Could not parse LogInvestigationResult from response ({detail})"
    )


def investigation_json_schema() -> dict[str, Any]:
    """
    JSON Schema suitable for OpenAI/Foundry strict structured outputs.

    Uses the OpenAI SDK helper so $defs / required / additionalProperties
    match what the Responses API expects.
    """
    from openai.lib._parsing._responses import type_to_text_format_param

    fmt = type_to_text_format_param(LogInvestigationResult)
    return {
        "name": fmt["name"],
        "strict": fmt.get("strict", True),
        "schema": fmt["schema"],
        "description": (
            "Structured log investigation result for an orchestrator. "
            "Separate observed evidence from hypotheses. Never invent "
            "log fields."
        ),
    }


def error_result(summary: str, *, limitations: list[str] | None = None) -> LogInvestigationResult:
    """Build a status=error result when the agent cannot produce valid output."""
    return LogInvestigationResult(
        status=InvestigationStatus.ERROR,
        summary=summary,
        evidence=[],
        related_trace_ids=[],
        hypotheses=[],
        limitations=limitations or [],
        confidence=0.0,
    )


# Phrases that claim a cause from gRPC/status UNAVAILABLE alone — not allowed
# unless logs explicitly support that conclusion.
_UNSUPPORTED_UNAVAILABLE_CAUSE_PATTERNS = (
    "upstream",
    "connectivity",
    "connection refused",
    "network",
    "dependency",
    "downstream",
    "timeout to",
    "could not reach",
    "unreachable",
)


def claims_unsupported_unavailable_cause(text: str) -> bool:
    """
    True when text treats UNAVAILABLE (or similar) as proof of an
    upstream/connectivity failure without requiring further evidence.
    """
    lowered = text.lower()
    mentions_unavailable = "unavailable" in lowered
    if not mentions_unavailable:
        return False
    return any(p in lowered for p in _UNSUPPORTED_UNAVAILABLE_CAUSE_PATTERNS)


def policy_violations_for_root_cause_request(
    result: LogInvestigationResult,
    *,
    logs_establish_root_cause: bool,
) -> list[str]:
    """
    Check policy for requests like "What is the exact root cause of ...?".

    When logs only show a failure symptom (e.g. GetAds UNAVAILABLE) and do
    not establish cause, status must be insufficient_evidence and
    hypotheses must not invent upstream/connectivity explanations.
    """
    violations: list[str] = []

    if logs_establish_root_cause:
        if result.status != InvestigationStatus.EVIDENCE_FOUND:
            violations.append(
                "When logs establish root cause, status should be evidence_found; "
                f"got {result.status.value}"
            )
        return violations

    if result.status == InvestigationStatus.EVIDENCE_FOUND:
        violations.append(
            "Finding failure symptoms (e.g. GetAds UNAVAILABLE) is not enough "
            "for evidence_found when the caller asked for exact root cause; "
            "use insufficient_evidence"
        )
    elif result.status != InvestigationStatus.INSUFFICIENT_EVIDENCE:
        violations.append(
            "When relevant failure evidence exists but root cause is not "
            f"established, status should be insufficient_evidence; got "
            f"{result.status.value}"
        )

    for hypothesis in result.hypotheses:
        if claims_unsupported_unavailable_cause(hypothesis.description):
            violations.append(
                "Hypothesis infers upstream/connectivity cause from "
                f"UNAVAILABLE without log support: {hypothesis.description!r}"
            )

    return violations


# Words that describe failures in natural language but must NOT imply a
# search_logs(severity=...) filter by themselves.
_FAILURE_LANGUAGE = (
    "failure",
    "failed",
    "exception",
    "unavailable",
    "error",
    "errors",
)

_EXPLICIT_SEVERITY_PATTERNS = (
    re.compile(r"\bseverity\s*[:=]?\s*(error|warn|warning|info|debug|severe)\b", re.I),
    re.compile(r"\b(error|warn|warning|info|debug|severe)\s+(events?|logs?|level)\b", re.I),
    re.compile(r"\b(show|list|find|get|only)\s+(error|warn|warning|info)\b", re.I),
)


def user_explicitly_requests_severity(request: str) -> Optional[str]:
    """
    Return a severity token if the user explicitly asked for one; else None.

    Mentions of 'failure' / 'failed' / 'error' in prose do not count.
    """
    for pattern in _EXPLICIT_SEVERITY_PATTERNS:
        match = pattern.search(request)
        if match:
            # Prefer the severity-like group.
            for group in match.groups():
                if group and group.lower() in {
                    "error",
                    "warn",
                    "warning",
                    "info",
                    "debug",
                    "severe",
                }:
                    return group.upper() if group.lower() != "warn" else "WARN"
    return None


def severity_filter_justified(
    *,
    user_request: str,
    severity_filter: Optional[str],
    prior_events_have_severity: bool = False,
) -> bool:
    """
    True when a search_logs severity filter is allowed under agent policy.
    """
    if not severity_filter:
        return True
    if prior_events_have_severity:
        return True
    explicit = user_explicitly_requests_severity(user_request)
    if explicit and explicit.lower() == severity_filter.lower():
        return True
    # Also allow exact match when user said "WARNING" etc. via patterns.
    if explicit and severity_filter.lower().startswith(explicit.lower()[:4]):
        return True
    return False


def search_strategy_violations(
    *,
    user_request: str,
    searches: list[dict[str, Any]],
    prior_events_have_severity: bool = False,
    distinctive_operation: Optional[str] = None,
    concluded_no_evidence: bool = False,
    any_broader_retry_after_empty: bool = False,
    empty_search_indices: Optional[set[int]] = None,
) -> list[str]:
    """
    Check a planned/executed search_logs sequence against investigation rules.

    `searches` is a list of search_logs kwargs in call order (general; not
    tied to any one operation name).
    """
    violations: list[str] = []
    empty_search_indices = empty_search_indices or set()

    if not searches and concluded_no_evidence:
        violations.append(
            "Concluded no_evidence_found without performing any search_logs calls"
        )
        return violations

    for index, search in enumerate(searches):
        severity = search.get("severity")
        query = (search.get("query") or "").strip()

        if severity and not severity_filter_justified(
            user_request=user_request,
            severity_filter=str(severity),
            prior_events_have_severity=prior_events_have_severity,
        ):
            violations.append(
                f"Search {index} used severity={severity!r} without an explicit "
                "user severity request or prior results showing that severity"
            )

        if index == 0 and distinctive_operation:
            op = distinctive_operation.lower()
            generic = query.lower() in {"failed", "failure", "error", "errors", "exception"}
            if generic or (query and op not in query.lower()):
                # First search should prefer the distinctive operation name.
                if op not in query.lower():
                    violations.append(
                        f"Search 0 should query distinctive operation "
                        f"{distinctive_operation!r} first; got query={query!r}"
                    )

    if concluded_no_evidence:
        # Narrow empty searches (e.g. inferred severity) without a broader retry.
        had_empty_narrow = any(
            i in empty_search_indices and searches[i].get("severity")
            for i in range(len(searches))
        )
        if had_empty_narrow and not any_broader_retry_after_empty:
            violations.append(
                "Concluded no_evidence_found after a severity-filtered empty "
                "search without broadening (remove severity / retry)"
            )

    return violations


# Intent classes for how many traces to inspect after a search.
TRACE_SCOPE_EXISTENCE = "existence"  # list/count/exists — no per-trace required
TRACE_SCOPE_SINGLE = "single"  # one named/specific trace
TRACE_SCOPE_DEEP = "deep"  # root-cause / pattern / correlation / why

_DEEP_INVESTIGATION_PATTERNS = (
    re.compile(r"\broot\s*cause\b", re.I),
    re.compile(r"\bwhy\b", re.I),
    re.compile(r"\bpattern\b", re.I),
    re.compile(r"\bcorrelat", re.I),
    re.compile(r"\bwhat(?:'s| is) (?:going on|happening)\b", re.I),
    re.compile(r"\binvestigat", re.I),
)

_EXISTENCE_PATTERNS = (
    re.compile(r"\b(are there|were there|any|list|count|how many|show me)\b", re.I),
    re.compile(r"\bexist\b", re.I),
)

_SINGLE_TRACE_PATTERN = re.compile(
    r"\b(?:trace(?:_id)?|trace id)\s*[:=]?\s*([0-9a-f]{8,})\b",
    re.I,
)

# Keep in sync with foundry_agent.MAX_TRACES_TO_INSPECT
DEFAULT_MAX_TRACES_TO_INSPECT = 3


def classify_trace_inspection_scope(user_request: str) -> str:
    """
    Classify how aggressively the agent should call get_trace_logs.

    Returns one of TRACE_SCOPE_EXISTENCE, TRACE_SCOPE_SINGLE, TRACE_SCOPE_DEEP.
    """
    if _SINGLE_TRACE_PATTERN.search(user_request):
        return TRACE_SCOPE_SINGLE
    if any(p.search(user_request) for p in _DEEP_INVESTIGATION_PATTERNS):
        return TRACE_SCOPE_DEEP
    if any(p.search(user_request) for p in _EXISTENCE_PATTERNS):
        return TRACE_SCOPE_EXISTENCE
    # Default: prefer a light representative probe when traces exist.
    return TRACE_SCOPE_DEEP


def unique_trace_ids_in_order(events: list[dict[str, Any]]) -> list[str]:
    """Distinct non-empty trace_ids preserving first-seen (search) order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for event in events:
        trace_id = event.get("trace_id") if isinstance(event, dict) else None
        if not trace_id:
            continue
        key = str(trace_id)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def expected_traces_to_inspect(
    *,
    user_request: str,
    search_events: list[dict[str, Any]],
    max_traces: int = DEFAULT_MAX_TRACES_TO_INSPECT,
) -> list[str]:
    """
    Trace IDs the agent should inspect for this request, given search hits.

    Caps deep investigations at max_traces; existence questions → [];
    single-trace questions → that id only when present in results (or named).
    """
    scope = classify_trace_inspection_scope(user_request)
    ordered = unique_trace_ids_in_order(search_events)

    if scope == TRACE_SCOPE_EXISTENCE:
        return []

    if scope == TRACE_SCOPE_SINGLE:
        match = _SINGLE_TRACE_PATTERN.search(user_request)
        if match:
            named = match.group(1)
            # Prefer exact / case-insensitive match from results when present.
            for tid in ordered:
                if tid.lower() == named.lower():
                    return [tid]
            return [named]
        return ordered[:1]

    return ordered[:max_traces]


def related_trace_ids_from_evidence(
    evidence: list[EvidenceItem] | list[dict[str, Any]],
) -> list[str]:
    """Distinct trace IDs actually present on evidence events (order preserved)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for item in evidence:
        if isinstance(item, EvidenceItem):
            tid = item.trace_id
        else:
            tid = item.get("trace_id")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        ordered.append(tid)
    return ordered


def related_trace_ids_policy_violations(
    *,
    related_trace_ids: list[str],
    evidence: list[EvidenceItem] | list[dict[str, Any]],
    caller_supplied_trace_id: Optional[str] = None,
) -> list[str]:
    """
    related_trace_ids may only contain IDs observed on evidence.

    A caller-supplied trace ID alone is not enough.
    """
    violations: list[str] = []
    observed = {t.lower() for t in related_trace_ids_from_evidence(evidence)}
    for tid in related_trace_ids:
        if tid.lower() not in observed:
            violations.append(
                f"related_trace_ids contains {tid!r} which is not on any "
                "evidence event"
            )
    if (
        caller_supplied_trace_id
        and not observed
        and any(
            tid.lower() == caller_supplied_trace_id.lower()
            for tid in related_trace_ids
        )
    ):
        violations.append(
            "Caller-supplied trace_id must not appear in related_trace_ids "
            "when no matching log events were found"
        )
    return violations


def trace_retrieval_status_violations(
    *,
    user_request: str,
    result: LogInvestigationResult,
) -> list[str]:
    """
    For 'What happened for trace <id>?' style questions: if evidence for
    that trace was retrieved, status should be evidence_found (sparse OK).
    """
    if classify_trace_inspection_scope(user_request) != TRACE_SCOPE_SINGLE:
        return []
    match = _SINGLE_TRACE_PATTERN.search(user_request)
    if not match:
        return []
    named = match.group(1).lower()
    evidence_for_trace = [
        e
        for e in result.evidence
        if e.trace_id and e.trace_id.lower() == named
    ]
    if evidence_for_trace and result.status != InvestigationStatus.EVIDENCE_FOUND:
        return [
            "Trace retrieval with matching evidence should use "
            f"evidence_found; got {result.status.value}"
        ]
    if (
        not evidence_for_trace
        and result.status == InvestigationStatus.EVIDENCE_FOUND
    ):
        return [
            "evidence_found without any evidence event for the requested trace"
        ]
    return []


_QUERY_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "were",
        "was",
        "there",
        "any",
        "from",
        "when",
        "with",
        "about",
        "for",
        "to",
        "of",
        "in",
        "on",
        "or",
        "and",
        "failures",
        "failure",
        "failed",
        "errors",
        "error",
        "events",
        "logs",
        "service",
        "open",
        "telemetry",  # keep otel/export terms; "opentelemetry" handled below
    }
)


def distinctive_query_fallbacks(compound_query: str) -> list[str]:
    """
    Candidate narrower queries when a compound NL search returns nothing.

    General heuristic: prefer distinctive nouns/operations over the full
    phrase and over vague words like 'failures'.
    """
    raw = compound_query.strip()
    if not raw:
        return []

    # Phrase-level aliases (general export/telemetry wording).
    lowered = raw.lower()
    fallbacks: list[str] = []
    if "export" in lowered:
        fallbacks.extend(["Failed to export", "export"])
    if "exporter" in lowered and "Failed to export" not in fallbacks:
        fallbacks.append("Failed to export")

    tokens = [
        t
        for t in re.findall(r"[A-Za-z][A-Za-z0-9_-]+", raw)
        if t.lower() not in _QUERY_STOPWORDS and len(t) > 2
    ]
    # Prefer longer / CamelCase-looking tokens first.
    tokens_sorted = sorted(
        tokens,
        key=lambda t: (t[:1].isupper() and any(c.islower() for c in t[1:]), len(t)),
        reverse=True,
    )
    for token in tokens_sorted:
        if token not in fallbacks and token.lower() not in {
            f.lower() for f in fallbacks
        }:
            fallbacks.append(token)

    # Never return the original compound phrase as a "fallback".
    return [f for f in fallbacks if f.lower() != lowered]


def compound_search_broadening_violations(
    *,
    compound_query: str,
    searches: list[dict[str, Any]],
    concluded_no_evidence: bool,
) -> list[str]:
    """
    If the first compound query misses and the agent concludes
    no_evidence_found without trying distinctive fallbacks, flag it.
    """
    if not concluded_no_evidence or not searches:
        return []

    first = (searches[0].get("query") or "").strip()
    if not first:
        return []

    # Only applies when the opening query looks compound (multiple tokens).
    if len(re.findall(r"[A-Za-z0-9_-]+", first)) < 2:
        return []

    fallbacks = {f.lower() for f in distinctive_query_fallbacks(first)}
    if not fallbacks:
        fallbacks = {f.lower() for f in distinctive_query_fallbacks(compound_query)}
    tried = {
        (s.get("query") or "").strip().lower()
        for s in searches
        if (s.get("query") or "").strip()
    }
    if not (fallbacks & tried) and concluded_no_evidence:
        return [
            "Compound/natural-language search returned empty and concluded "
            "no_evidence_found without retrying distinctive component terms "
            f"(e.g. {sorted(fallbacks)[:3]})"
        ]
    return []


def multi_trace_inspection_violations(
    *,
    user_request: str,
    search_events: list[dict[str, Any]],
    inspected_trace_ids: list[str],
    max_traces: int = DEFAULT_MAX_TRACES_TO_INSPECT,
) -> list[str]:
    """
    Validate a get_trace_logs call sequence against multi-trace policy.
    """
    violations: list[str] = []
    expected = expected_traces_to_inspect(
        user_request=user_request,
        search_events=search_events,
        max_traces=max_traces,
    )
    # Normalize inspected: preserve order, drop blanks, flag duplicates.
    seen: set[str] = set()
    deduped: list[str] = []
    for tid in inspected_trace_ids:
        if not tid:
            continue
        key = tid.lower()
        if key in seen:
            violations.append(f"Duplicate get_trace_logs for trace_id={tid!r}")
            continue
        seen.add(key)
        deduped.append(tid)

    if len(deduped) > max_traces:
        violations.append(
            f"Inspected {len(deduped)} traces; cap is {max_traces} distinct traces"
        )

    scope = classify_trace_inspection_scope(user_request)
    if scope == TRACE_SCOPE_EXISTENCE and deduped:
        # Soft: existence questions should not require traces; inspecting some
        # is wasteful but not always wrong — only flag when clearly excessive.
        if len(deduped) > 1:
            violations.append(
                "Existence/list/count questions should not inspect multiple traces"
            )

    if scope in {TRACE_SCOPE_DEEP, TRACE_SCOPE_SINGLE} and expected:
        expected_lower = [t.lower() for t in expected]
        deduped_lower = [t.lower() for t in deduped]
        for tid in expected_lower:
            if tid not in deduped_lower:
                violations.append(
                    f"Deep/single-trace investigation missing get_trace_logs "
                    f"for relevant trace_id={tid!r}"
                )

    return violations
