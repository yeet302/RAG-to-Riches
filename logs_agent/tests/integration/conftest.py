"""Shared fixtures for live Azure integration tests."""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from dotenv import load_dotenv

from connectors import AzureLogAnalyticsConnector, set_connector
from connectors.azure_log_analytics import AzureLogAnalyticsError

load_dotenv()

# Historical OTel rows may sit outside the production 7-day default.
_INTEGRATION_LOOKBACK = timedelta(days=30)


@pytest.fixture(scope="module")
def azure_connector():
    """Production AzureLogAnalyticsConnector, or skip if Azure is not configured."""
    workspace_id = (os.environ.get("AZURE_LOG_ANALYTICS_WORKSPACE_ID") or "").strip()
    if not workspace_id:
        pytest.skip(
            "AZURE_LOG_ANALYTICS_WORKSPACE_ID is not set; skipping live Azure tests"
        )
    try:
        connector = AzureLogAnalyticsConnector(
            workspace_id=workspace_id,
            default_lookback=_INTEGRATION_LOOKBACK,
        )
        connector.list_services()
    except AzureLogAnalyticsError as exc:
        pytest.skip(f"Azure Log Analytics is unavailable: {exc}")
    except Exception as exc:
        pytest.skip(f"Azure Log Analytics is unavailable: {exc}")
    return connector


@pytest.fixture
def azure_as_default(azure_connector):
    """Point get_connector() at Azure for the Foundry smoke test, then restore."""
    import connectors as connectors_mod

    previous = connectors_mod._default_connector
    set_connector(azure_connector)
    try:
        yield azure_connector
    finally:
        connectors_mod._default_connector = previous
