"""
Optional live Foundry Logs Agent smoke test against Azure.

Incurs a model call. Not collected by ordinary pytest (tests/integration
is excluded from recursion). Run explicitly:

  pytest -q -m integration_agent tests/integration/test_azure_agent_smoke.py
"""

from __future__ import annotations

import os

import pytest

from investigation_result import InvestigationStatus, LogInvestigationResult

pytestmark = pytest.mark.integration_agent


def test_investigate_logs_getads_failures_against_azure(azure_as_default):
    endpoint = (os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or "").strip()
    agent_name = (os.environ.get("FOUNDRY_AGENT_NAME") or "").strip()
    if not endpoint or not agent_name:
        pytest.skip(
            "FOUNDRY_PROJECT_ENDPOINT or FOUNDRY_AGENT_NAME is not set; "
            "skipping Foundry agent smoke test"
        )

    from foundry_agent import investigate_logs

    result = investigate_logs("Are there any GetAds failures?")
    assert isinstance(result, LogInvestigationResult)
    assert result.status == InvestigationStatus.EVIDENCE_FOUND
    assert result.evidence
