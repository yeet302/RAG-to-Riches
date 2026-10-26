"""Unit tests for LogInvestigationResult schema and parsing (no live Azure calls)."""

from __future__ import annotations

import json
import unittest

from pydantic import ValidationError

from foundry_agent import (
    MAX_TRACES_TO_INSPECT,
    SEARCH_STRATEGY_RULES,
    STATUS_AND_HYPOTHESIS_RULES,
    SYSTEM_PROMPT,
)
from investigation_result import (
    DEFAULT_MAX_TRACES_TO_INSPECT,
    TRACE_SCOPE_DEEP,
    TRACE_SCOPE_EXISTENCE,
    TRACE_SCOPE_SINGLE,
    EvidenceItem,
    Hypothesis,
    InvestigationParseError,
    InvestigationStatus,
    LogInvestigationResult,
    claims_unsupported_unavailable_cause,
    classify_trace_inspection_scope,
    compound_search_broadening_violations,
    describe_response_output_structure,
    distinctive_query_fallbacks,
    error_result,
    expected_traces_to_inspect,
    iter_response_output_text_segments,
    multi_trace_inspection_violations,
    parse_investigation_result,
    parse_investigation_result_from_response,
    policy_violations_for_root_cause_request,
    related_trace_ids_policy_violations,
    search_strategy_violations,
    severity_filter_justified,
    trace_retrieval_status_violations,
    unique_trace_ids_in_order,
    user_explicitly_requests_severity,
)
from log_tools import get_logs_for_traces
from connectors import LocalLogConnector, set_connector


class SchemaValidationTests(unittest.TestCase):
    def test_valid_result_passes(self):
        result = LogInvestigationResult(
            status=InvestigationStatus.EVIDENCE_FOUND,
            summary="Two GetAds failures were found.",
            evidence=[
                EvidenceItem(
                    timestamp="2026-10-17T22:37:47+00:00",
                    service="ad",
                    severity=None,
                    trace_id="abc123",
                    span_id="def456",
                    message="GetAds Failed",
                )
            ],
            related_trace_ids=["abc123"],
            hypotheses=[
                Hypothesis(
                    description="The failure appears to originate in the Ad Service.",
                    confidence=0.75,
                    supporting_trace_ids=["abc123"],
                )
            ],
            limitations=[
                "The available logs do not identify the exact code-level cause."
            ],
            confidence=0.82,
        )
        self.assertEqual(result.status, InvestigationStatus.EVIDENCE_FOUND)
        self.assertEqual(result.confidence, 0.82)
        payload = result.to_dict()
        self.assertEqual(payload["status"], "evidence_found")
        # Round-trip JSON
        again = LogInvestigationResult.model_validate(payload)
        self.assertEqual(again.summary, result.summary)

    def test_invalid_status_fails(self):
        with self.assertRaises(ValidationError):
            LogInvestigationResult(
                status="not_a_real_status",
                summary="x",
                evidence=[],
                related_trace_ids=[],
                hypotheses=[],
                limitations=[],
                confidence=0.5,
            )

    def test_confidence_outside_range_fails(self):
        with self.assertRaises(ValidationError):
            LogInvestigationResult(
                status=InvestigationStatus.EVIDENCE_FOUND,
                summary="x",
                evidence=[],
                related_trace_ids=[],
                hypotheses=[],
                limitations=[],
                confidence=1.5,
            )

        with self.assertRaises(ValidationError):
            Hypothesis(description="x", confidence=-0.1)

    def test_missing_required_fields_fail(self):
        with self.assertRaises(ValidationError):
            LogInvestigationResult.model_validate(
                {
                    "status": "evidence_found",
                    # missing summary, confidence, etc.
                    "evidence": [],
                }
            )


class EvidenceStructureTests(unittest.TestCase):
    def test_optional_metadata_may_be_none(self):
        item = EvidenceItem(
            timestamp=None,
            service="ad",
            severity="ERROR",
            trace_id=None,
            span_id=None,
            message="Failed to export logs",
        )
        self.assertIsNone(item.trace_id)
        self.assertIsNone(item.span_id)
        self.assertIsNone(item.timestamp)

    def test_evidence_json_serializable(self):
        result = LogInvestigationResult(
            status=InvestigationStatus.EVIDENCE_FOUND,
            summary="Found an ERROR",
            evidence=[
                EvidenceItem(
                    timestamp="2026-10-17T22:31:37.664000+00:00",
                    service="ad",
                    severity="ERROR",
                    trace_id=None,
                    span_id=None,
                    message="Failed to export logs",
                )
            ],
            related_trace_ids=[],
            hypotheses=[],
            limitations=["No trace_id on javaagent ERROR events."],
            confidence=0.7,
        )
        encoded = result.to_json()
        decoded = json.loads(encoded)
        self.assertIsNone(decoded["evidence"][0]["trace_id"])
        self.assertEqual(decoded["evidence"][0]["severity"], "ERROR")


class StatusShapeTests(unittest.TestCase):
    def test_insufficient_evidence_shape(self):
        result = LogInvestigationResult(
            status=InvestigationStatus.INSUFFICIENT_EVIDENCE,
            summary=(
                "The logs confirm a failure but do not establish the root cause."
            ),
            evidence=[
                EvidenceItem(
                    timestamp="2026-10-17T22:37:47+00:00",
                    service="ad",
                    severity=None,
                    trace_id="3b4949e9e897c7352e518f21f38ad64d",
                    span_id="a8dbe11b47910d7d",
                    message="GetAds Failed with status Status{code=UNAVAILABLE}",
                )
            ],
            related_trace_ids=["3b4949e9e897c7352e518f21f38ad64d"],
            hypotheses=[],
            limitations=["Root cause cannot be determined from available logs."],
            confidence=0.4,
        )
        self.assertEqual(
            result.status, InvestigationStatus.INSUFFICIENT_EVIDENCE
        )
        self.assertEqual(result.hypotheses, [])
        self.assertTrue(result.evidence)
        self.assertTrue(result.limitations)

    def test_no_evidence_found_without_hallucinated_evidence(self):
        result = LogInvestigationResult(
            status=InvestigationStatus.NO_EVIDENCE_FOUND,
            summary="No log events mention the requested engineer or deployment.",
            evidence=[],
            related_trace_ids=[],
            hypotheses=[],
            limitations=[
                "Deployment ownership and engineer identity are not present in the logs."
            ],
            confidence=0.2,
        )
        self.assertEqual(result.status, InvestigationStatus.NO_EVIDENCE_FOUND)
        self.assertEqual(result.evidence, [])
        self.assertEqual(result.related_trace_ids, [])


class StatusSufficiencyPromptBehaviorTests(unittest.TestCase):
    """
    Prompt/policy behavior: status must reflect whether the caller's
    investigation was answered, not merely whether related logs exist.
    """

    ROOT_CAUSE_QUESTION = (
        "What is the exact root cause of the GetAds failure?"
    )

    def _symptom_only_evidence(self) -> list[EvidenceItem]:
        return [
            EvidenceItem(
                timestamp="2026-10-17T22:37:47+00:00",
                service="ad",
                severity=None,
                trace_id="3b4949e9e897c7352e518f21f38ad64d",
                span_id="a8dbe11b47910d7d",
                message=(
                    "GetAds Failed with status "
                    "Status{code=UNAVAILABLE, description=null, cause=null}"
                ),
            )
        ]

    def test_prompt_defines_status_as_satisfying_the_request(self):
        self.assertIn(STATUS_AND_HYPOTHESIS_RULES, SYSTEM_PROMPT)
        self.assertIn(
            "satisfied the investigation requested by the caller",
            STATUS_AND_HYPOTHESIS_RULES,
        )
        self.assertIn(
            "sufficient log evidence exists to answer the",
            STATUS_AND_HYPOTHESIS_RULES,
        )
        self.assertIn(
            "insufficient to answer the requested question",
            STATUS_AND_HYPOTHESIS_RULES,
        )
        self.assertIn(
            "answer the user's actual question",
            STATUS_AND_HYPOTHESIS_RULES,
        )
        self.assertIn("GetAds", STATUS_AND_HYPOTHESIS_RULES)
        self.assertIn("UNAVAILABLE", STATUS_AND_HYPOTHESIS_RULES)
        self.assertIn(
            "Prefer an empty hypotheses list / no hypothesis over an unsupported",
            STATUS_AND_HYPOTHESIS_RULES,
        )
        self.assertIn(
            "Do NOT infer that UNAVAILABLE means an upstream connectivity",
            STATUS_AND_HYPOTHESIS_RULES,
        )
        self.assertIn(
            "Do not infer causality solely from events appearing in the same",
            STATUS_AND_HYPOTHESIS_RULES,
        )
        collapsed = " ".join(STATUS_AND_HYPOTHESIS_RULES.split())
        self.assertIn(
            "even if the root cause of that event is unknown",
            collapsed,
        )
        self.assertIn(
            "Do not downgrade an existence or listing question to insufficient_evidence merely because root cause is unknown.",
            collapsed,
        )
        self.assertIn(
            "What happened for trace",
            collapsed,
        )
        self.assertIn(
            "Sparse evidence (even a single event",
            collapsed,
        )
        self.assertIn(
            "NOT by downgrading status to insufficient_evidence",
            collapsed,
        )
        self.assertIn(
            "related_trace_ids",
            collapsed,
        )

    def test_sparse_trace_retrieval_is_evidence_found(self):
        question = (
            "What happened for trace 3b4949e9e897c7352e518f21f38ad64d?"
        )
        result = LogInvestigationResult(
            status=InvestigationStatus.EVIDENCE_FOUND,
            summary="Trace shows GetAds Failed with UNAVAILABLE.",
            evidence=self._symptom_only_evidence(),
            related_trace_ids=["3b4949e9e897c7352e518f21f38ad64d"],
            hypotheses=[],
            limitations=["Only one application event is available for this trace."],
            confidence=0.85,
        )
        self.assertEqual(
            trace_retrieval_status_violations(
                user_request=question, result=result
            ),
            [],
        )
        wrong = result.model_copy(
            update={"status": InvestigationStatus.INSUFFICIENT_EVIDENCE}
        )
        violations = trace_retrieval_status_violations(
            user_request=question, result=wrong
        )
        self.assertTrue(
            any("evidence_found" in v for v in violations),
            violations,
        )

    def test_getads_root_cause_symptom_only_is_insufficient_evidence(self):
        # Correct agent behavior for the example question.
        correct = LogInvestigationResult(
            status=InvestigationStatus.INSUFFICIENT_EVIDENCE,
            summary=(
                "GetAds returned UNAVAILABLE, but the logs do not establish "
                "the exact root cause."
            ),
            evidence=self._symptom_only_evidence(),
            related_trace_ids=["3b4949e9e897c7352e518f21f38ad64d"],
            hypotheses=[],
            limitations=[
                "Observed GetAds UNAVAILABLE status only; exact root cause "
                "is not established by the available logs."
            ],
            confidence=0.45,
        )
        self.assertEqual(
            policy_violations_for_root_cause_request(
                correct, logs_establish_root_cause=False
            ),
            [],
        )

        # Wrong: treating symptom evidence as enough for evidence_found.
        wrong_status = correct.model_copy(
            update={"status": InvestigationStatus.EVIDENCE_FOUND, "confidence": 0.9}
        )
        violations = policy_violations_for_root_cause_request(
            wrong_status, logs_establish_root_cause=False
        )
        self.assertTrue(violations)
        self.assertTrue(
            any("insufficient_evidence" in v for v in violations),
            violations,
        )

    def test_prefer_empty_hypotheses_over_unsupported_unavailable_inference(self):
        self.assertTrue(
            claims_unsupported_unavailable_cause(
                "UNAVAILABLE likely means an upstream connectivity problem"
            )
        )
        self.assertFalse(
            claims_unsupported_unavailable_cause(
                "GetAds returned UNAVAILABLE according to the log message"
            )
        )

        unsupported = LogInvestigationResult(
            status=InvestigationStatus.INSUFFICIENT_EVIDENCE,
            summary="GetAds failed with UNAVAILABLE.",
            evidence=self._symptom_only_evidence(),
            related_trace_ids=["3b4949e9e897c7352e518f21f38ad64d"],
            hypotheses=[
                Hypothesis(
                    description=(
                        "UNAVAILABLE indicates an upstream connectivity "
                        "or dependency failure"
                    ),
                    confidence=0.6,
                    supporting_trace_ids=["3b4949e9e897c7352e518f21f38ad64d"],
                )
            ],
            limitations=["Root cause not fully established."],
            confidence=0.4,
        )
        violations = policy_violations_for_root_cause_request(
            unsupported, logs_establish_root_cause=False
        )
        self.assertTrue(
            any("upstream/connectivity" in v for v in violations),
            violations,
        )

        empty_hypotheses = unsupported.model_copy(update={"hypotheses": []})
        self.assertEqual(
            policy_violations_for_root_cause_request(
                empty_hypotheses, logs_establish_root_cause=False
            ),
            [],
        )


class SearchStrategyPromptBehaviorTests(unittest.TestCase):
    """
    Regression: do not infer severity=ERROR from failure language; start
    with distinctive operation queries; broaden before no_evidence_found.
    """

    ROOT_CAUSE_QUESTION = (
        "What is the exact root cause of the GetAds failure?"
    )

    def test_prompt_includes_search_strategy_rules(self):
        self.assertIn(SEARCH_STRATEGY_RULES, SYSTEM_PROMPT)
        self.assertIn(
            "Do NOT infer a severity filter from words like failure",
            SEARCH_STRATEGY_RULES,
        )
        self.assertIn('severity="ERROR"', SEARCH_STRATEGY_RULES)
        self.assertIn("fewest justified filters", SEARCH_STRATEGY_RULES)
        self.assertIn("get_trace_logs", SEARCH_STRATEGY_RULES)
        self.assertIn(
            "Return no_evidence_found ONLY after sensible broader searches",
            SEARCH_STRATEGY_RULES,
        )
        self.assertIn(
            "Prefer no hypothesis over an unsupported hypothesis",
            SEARCH_STRATEGY_RULES,
        )
        collapsed = " ".join(SEARCH_STRATEGY_RULES.split())
        self.assertIn("search for the relevant failure events first", collapsed)
        self.assertIn("If matching events contain trace IDs", collapsed)
        self.assertIn("Cap at 3 distinct relevant traces", collapsed)
        self.assertIn("Do not inspect duplicate trace IDs", collapsed)
        self.assertIn("existence / list / count", collapsed)
        self.assertIn("get_logs_for_traces", collapsed)
        self.assertIn("compound / natural-language phrase", collapsed)
        self.assertIn("Failed to export", collapsed)
        self.assertEqual(MAX_TRACES_TO_INSPECT, DEFAULT_MAX_TRACES_TO_INSPECT)

    def test_failure_language_does_not_justify_severity_filter(self):
        self.assertIsNone(
            user_explicitly_requests_severity(self.ROOT_CAUSE_QUESTION)
        )
        self.assertFalse(
            severity_filter_justified(
                user_request=self.ROOT_CAUSE_QUESTION,
                severity_filter="ERROR",
            )
        )
        # Explicit severity requests remain allowed.
        self.assertEqual(
            user_explicitly_requests_severity("Show ERROR events for ad"),
            "ERROR",
        )
        self.assertTrue(
            severity_filter_justified(
                user_request="Show ERROR events for ad",
                severity_filter="ERROR",
            )
        )

    def test_regression_inferred_severity_error_excludes_null_severity_failures(self):
        """
        The bad agent path: severity=ERROR on first search → empty →
        no_evidence_found. Policy must reject that sequence.
        """
        bad_searches = [
            {"query": "GetAds", "service": "ad", "severity": "ERROR", "limit": 20},
        ]
        violations = search_strategy_violations(
            user_request=self.ROOT_CAUSE_QUESTION,
            searches=bad_searches,
            distinctive_operation="GetAds",
            concluded_no_evidence=True,
            any_broader_retry_after_empty=False,
            empty_search_indices={0},
        )
        self.assertTrue(
            any("severity=" in v for v in violations),
            violations,
        )
        self.assertTrue(
            any("no_evidence_found" in v for v in violations),
            violations,
        )

    def test_expected_getads_style_flow_has_no_violations(self):
        """
        General expected flow (illustrated with an operation name):
        search distinctive name without severity → then broaden only if needed.
        """
        good_searches = [
            {"query": "GetAds", "service": "ad", "limit": 20},
        ]
        self.assertEqual(
            search_strategy_violations(
                user_request=self.ROOT_CAUSE_QUESTION,
                searches=good_searches,
                distinctive_operation="GetAds",
                concluded_no_evidence=False,
            ),
            [],
        )

    def test_prefer_distinctive_operation_over_generic_failed(self):
        violations = search_strategy_violations(
            user_request=self.ROOT_CAUSE_QUESTION,
            searches=[{"query": "Failed", "service": "ad"}],
            distinctive_operation="GetAds",
        )
        self.assertTrue(
            any("distinctive operation" in v for v in violations),
            violations,
        )


class MultiTraceInspectionPolicyTests(unittest.TestCase):
    """
    When a search returns multiple relevant trace IDs, deep investigations
    should inspect a small representative set (cap 3), not skip traces and
    not duplicate them.
    """

    ROOT_CAUSE_QUESTION = (
        "What is the exact root cause of the GetAds failure?"
    )

    def _two_failed_search_hits(self) -> list[dict]:
        # Mirrors the real connector shape: two distinct failed traces.
        return [
            {
                "timestamp": "2026-10-17T22:37:47+00:00",
                "service": "ad",
                "severity": None,
                "trace_id": "3b4949e9e897c7352e518f21f38ad64d",
                "span_id": "a8dbe11b47910d7d",
                "message": "GetAds Failed with status UNAVAILABLE",
            },
            {
                "timestamp": "2026-10-17T22:38:03+00:00",
                "service": "ad",
                "severity": None,
                "trace_id": "d97e8147efa7fb6ff84a0b82af2077f3",
                "span_id": "d62848dba0e07840",
                "message": "GetAds Failed with status UNAVAILABLE",
            },
        ]

    def test_classify_scope(self):
        self.assertEqual(
            classify_trace_inspection_scope(self.ROOT_CAUSE_QUESTION),
            TRACE_SCOPE_DEEP,
        )
        self.assertEqual(
            classify_trace_inspection_scope("Are there any GetAds failures?"),
            TRACE_SCOPE_EXISTENCE,
        )
        self.assertEqual(
            classify_trace_inspection_scope(
                "What happened for trace 3b4949e9e897c7352e518f21f38ad64d?"
            ),
            TRACE_SCOPE_SINGLE,
        )

    def test_root_cause_with_two_traces_expects_both_inspected(self):
        events = self._two_failed_search_hits()
        expected = expected_traces_to_inspect(
            user_request=self.ROOT_CAUSE_QUESTION,
            search_events=events,
        )
        self.assertEqual(
            expected,
            [
                "3b4949e9e897c7352e518f21f38ad64d",
                "d97e8147efa7fb6ff84a0b82af2077f3",
            ],
        )

        # Desired sequence: search → get_trace_logs(A) → get_trace_logs(B)
        self.assertEqual(
            multi_trace_inspection_violations(
                user_request=self.ROOT_CAUSE_QUESTION,
                search_events=events,
                inspected_trace_ids=expected,
            ),
            [],
        )

        # Skipping the second trace is a policy violation for root-cause.
        violations = multi_trace_inspection_violations(
            user_request=self.ROOT_CAUSE_QUESTION,
            search_events=events,
            inspected_trace_ids=[expected[0]],
        )
        self.assertTrue(
            any("missing get_trace_logs" in v for v in violations),
            violations,
        )

    def test_cap_at_three_distinct_traces_and_no_duplicates(self):
        events = [
            {"trace_id": f"trace{i:04d}", "message": "Failed"}
            for i in range(1, 6)
        ]
        events.insert(1, {"trace_id": "trace0001", "message": "dup"})
        ordered = unique_trace_ids_in_order(events)
        self.assertEqual(len(ordered), 5)
        expected = expected_traces_to_inspect(
            user_request="Why is this failing?",
            search_events=events,
            max_traces=MAX_TRACES_TO_INSPECT,
        )
        self.assertEqual(len(expected), 3)

        violations = multi_trace_inspection_violations(
            user_request="Why is this failing?",
            search_events=events,
            inspected_trace_ids=expected + [expected[0]],
            max_traces=MAX_TRACES_TO_INSPECT,
        )
        self.assertTrue(any("Duplicate" in v for v in violations), violations)

    def test_existence_question_does_not_require_trace_inspection(self):
        events = self._two_failed_search_hits()
        self.assertEqual(
            expected_traces_to_inspect(
                user_request="Are there any GetAds failures?",
                search_events=events,
            ),
            [],
        )
        self.assertEqual(
            multi_trace_inspection_violations(
                user_request="Are there any GetAds failures?",
                search_events=events,
                inspected_trace_ids=[],
            ),
            [],
        )

    def test_single_trace_question_inspects_only_that_trace(self):
        events = self._two_failed_search_hits()
        tid = "3b4949e9e897c7352e518f21f38ad64d"
        request = f"What happened for trace {tid}?"
        self.assertEqual(
            expected_traces_to_inspect(
                user_request=request,
                search_events=events,
            ),
            [tid],
        )
        self.assertEqual(
            multi_trace_inspection_violations(
                user_request=request,
                search_events=events,
                inspected_trace_ids=[tid],
            ),
            [],
        )

    def test_get_logs_for_traces_helper_covers_both_failure_traces(self):
        LocalLogConnector._load_events.cache_clear()
        set_connector(LocalLogConnector())
        events = self._two_failed_search_hits()
        expected = expected_traces_to_inspect(
            user_request=self.ROOT_CAUSE_QUESTION,
            search_events=events,
        )
        bundled = get_logs_for_traces(expected)
        self.assertEqual(bundled["inspected_trace_ids"], expected)
        for tid in expected:
            self.assertTrue(bundled["logs_by_trace_id"][tid])
        self.assertEqual(
            multi_trace_inspection_violations(
                user_request=self.ROOT_CAUSE_QUESTION,
                search_events=events,
                inspected_trace_ids=bundled["inspected_trace_ids"],
            ),
            [],
        )


class RelatedTraceIdsAndSearchBroadeningTests(unittest.TestCase):
    def test_related_trace_ids_only_from_evidence(self):
        evidence = [
            EvidenceItem(
                timestamp="t",
                service="ad",
                severity=None,
                trace_id="abc",
                span_id="s",
                message="ok",
            )
        ]
        self.assertEqual(
            related_trace_ids_policy_violations(
                related_trace_ids=["abc"],
                evidence=evidence,
            ),
            [],
        )
        self.assertTrue(
            related_trace_ids_policy_violations(
                related_trace_ids=["abc", "caller-only"],
                evidence=evidence,
            )
        )

    def test_missing_trace_must_not_echo_caller_id_in_related(self):
        fake = "00000000000000000000000000000000"
        violations = related_trace_ids_policy_violations(
            related_trace_ids=[fake],
            evidence=[],
            caller_supplied_trace_id=fake,
        )
        self.assertTrue(violations)
        self.assertEqual(
            related_trace_ids_policy_violations(
                related_trace_ids=[],
                evidence=[],
                caller_supplied_trace_id=fake,
            ),
            [],
        )

    def test_compound_exporter_search_requires_distinctive_fallback(self):
        question = (
            "Were there OpenTelemetry exporter failures when exporting "
            "logs or metrics from the Ad Service?"
        )
        fallbacks = distinctive_query_fallbacks(
            "OpenTelemetry exporter failures exporting logs metrics"
        )
        self.assertTrue(
            any("export" in f.lower() for f in fallbacks),
            fallbacks,
        )

        bad = compound_search_broadening_violations(
            compound_query=question,
            searches=[
                {
                    "query": "OpenTelemetry exporter failures",
                    "service": "ad",
                }
            ],
            concluded_no_evidence=True,
        )
        self.assertTrue(bad)

        good = compound_search_broadening_violations(
            compound_query=question,
            searches=[
                {
                    "query": "OpenTelemetry exporter failures",
                    "service": "ad",
                },
                {"query": "Failed to export", "service": "ad"},
            ],
            concluded_no_evidence=False,
        )
        self.assertEqual(good, [])


class ParseTests(unittest.TestCase):
    def _valid_payload(self, **overrides) -> dict:
        payload = {
            "status": "insufficient_evidence",
            "summary": "GetAds failed; root cause not established.",
            "evidence": [
                {
                    "timestamp": "2026-10-17T22:37:47+00:00",
                    "service": "ad",
                    "severity": None,
                    "trace_id": "abc123",
                    "span_id": "def456",
                    "message": "GetAds Failed with status UNAVAILABLE",
                }
            ],
            "related_trace_ids": ["abc123"],
            "hypotheses": [],
            "limitations": ["Exact root cause is not present in the logs."],
            "confidence": 0.4,
        }
        payload.update(overrides)
        return payload

    def test_parse_plain_json(self):
        text = json.dumps(
            {
                "status": "no_evidence_found",
                "summary": "Nothing matched.",
                "evidence": [],
                "related_trace_ids": [],
                "hypotheses": [],
                "limitations": ["No matching events."],
                "confidence": 0.3,
            }
        )
        result = parse_investigation_result(text)
        self.assertEqual(result.status, InvestigationStatus.NO_EVIDENCE_FOUND)

    def test_parse_fenced_json(self):
        inner = {
            "status": "evidence_found",
            "summary": "Found errors.",
            "evidence": [
                {
                    "timestamp": None,
                    "service": "ad",
                    "severity": "ERROR",
                    "trace_id": None,
                    "span_id": None,
                    "message": "timeout",
                }
            ],
            "related_trace_ids": [],
            "hypotheses": [],
            "limitations": [],
            "confidence": 0.6,
        }
        text = f"Here is the result:\n```json\n{json.dumps(inner)}\n```\n"
        result = parse_investigation_result(text)
        self.assertEqual(result.status, InvestigationStatus.EVIDENCE_FOUND)
        self.assertEqual(len(result.evidence), 1)

    def test_parse_invalid_raises(self):
        with self.assertRaises(InvestigationParseError):
            parse_investigation_result("not json at all")

        with self.assertRaises(InvestigationParseError):
            parse_investigation_result('{"status": "evidence_found"}')

    def test_error_result_helper(self):
        result = error_result("boom", limitations=["detail"])
        self.assertEqual(result.status, InvestigationStatus.ERROR)
        self.assertEqual(result.confidence, 0.0)
        self.assertEqual(result.limitations, ["detail"])

    def test_valid_json_plus_trailing_content_uses_first_object(self):
        """
        Regression for Extra data: line 1 column N.

        Root cause: Response.output_text does ''.join(all output_text
        blocks). Two schema-valid JSON objects become '{...}{...}', which
        still starts with '{' and ends with '}', so a naive json.loads of
        the aggregate fails with Extra data. Parsing must take the first
        complete object (or parse segments separately).
        """
        first = json.dumps(self._valid_payload())
        second = json.dumps(
            self._valid_payload(
                status="no_evidence_found",
                summary="second object should be ignored",
                evidence=[],
                related_trace_ids=[],
                confidence=0.1,
            )
        )
        # Same failure mode as Foundry aggregating multiple output_text parts.
        aggregated = first + second
        with self.assertRaises(json.JSONDecodeError) as ctx:
            json.loads(aggregated)
        self.assertIn("Extra data", str(ctx.exception))

        result = parse_investigation_result(aggregated)
        self.assertEqual(result.status, InvestigationStatus.INSUFFICIENT_EVIDENCE)
        self.assertEqual(result.related_trace_ids, ["abc123"])

        # Trailing non-JSON after one valid object.
        with_trailing = first + "\nThanks!"
        result2 = parse_investigation_result(with_trailing)
        self.assertEqual(result2.status, InvestigationStatus.INSUFFICIENT_EVIDENCE)

    def test_parse_from_response_prefers_individual_output_text_segments(self):
        first = json.dumps(self._valid_payload())
        second = json.dumps(
            self._valid_payload(
                status="evidence_found",
                summary="should not win if first segment parses",
                confidence=0.9,
            )
        )

        class _Content:
            def __init__(self, text: str):
                self.type = "output_text"
                self.text = text

        class _Message:
            def __init__(self, texts: list[str]):
                self.type = "message"
                self.content = [_Content(t) for t in texts]

        class _Response:
            def __init__(self, texts: list[str]):
                self.output = [_Message(texts)]

            @property
            def output_text(self) -> str:
                # Mirror OpenAI SDK aggregation.
                return "".join(
                    c.text
                    for m in self.output
                    for c in m.content
                    if c.type == "output_text"
                )

        response = _Response([first, second])
        with self.assertRaises(json.JSONDecodeError) as ctx:
            json.loads(response.output_text)
        self.assertIn("Extra data", str(ctx.exception))

        self.assertEqual(iter_response_output_text_segments(response), [first, second])
        self.assertIn("output_text_lengths", describe_response_output_structure(response))

        result = parse_investigation_result_from_response(response)
        self.assertEqual(result.status, InvestigationStatus.INSUFFICIENT_EVIDENCE)
        self.assertEqual(result.summary, "GetAds failed; root cause not established.")


if __name__ == "__main__":
    unittest.main()
