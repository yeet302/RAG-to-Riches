"""Unit tests for LOG_CONNECTOR-based get_connector() selection."""

from __future__ import annotations

import os
import unittest
from unittest import mock

import connectors
from connectors import (
    AzureLogAnalyticsConnector,
    LocalLogConnector,
    LogConnector,
    get_connector,
    set_connector,
)


class ConnectorSelectionTests(unittest.TestCase):
    def setUp(self):
        self._previous = connectors._default_connector
        connectors._default_connector = None

    def tearDown(self):
        connectors._default_connector = self._previous

    def test_log_connector_local_selects_local(self):
        with mock.patch.dict(os.environ, {"LOG_CONNECTOR": "local"}):
            connector = get_connector()
        self.assertIsInstance(connector, LocalLogConnector)
        self.assertNotIsInstance(connector, AzureLogAnalyticsConnector)

    def test_log_connector_azure_selects_azure(self):
        fake = mock.Mock(spec=LogConnector)
        with mock.patch.dict(os.environ, {"LOG_CONNECTOR": "azure"}):
            with mock.patch(
                "connectors.AzureLogAnalyticsConnector", return_value=fake
            ) as ctor:
                connector = get_connector()
        self.assertIs(connector, fake)
        ctor.assert_called_once_with()

    def test_unset_log_connector_defaults_to_local(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("LOG_CONNECTOR", None)
            connector = get_connector()
        self.assertIsInstance(connector, LocalLogConnector)

    def test_blank_log_connector_defaults_to_local(self):
        with mock.patch.dict(os.environ, {"LOG_CONNECTOR": "  "}):
            connector = get_connector()
        self.assertIsInstance(connector, LocalLogConnector)

    def test_unsupported_log_connector_raises_value_error(self):
        with mock.patch.dict(os.environ, {"LOG_CONNECTOR": "cloudwatch"}):
            with self.assertRaises(ValueError) as ctx:
                get_connector()
        message = str(ctx.exception)
        self.assertIn("cloudwatch", message)
        self.assertIn("local", message)
        self.assertIn("azure", message)

    def test_set_connector_overrides_env_selection(self):
        override = mock.Mock(spec=LogConnector)
        with mock.patch.dict(os.environ, {"LOG_CONNECTOR": "azure"}):
            with mock.patch("connectors.AzureLogAnalyticsConnector") as ctor:
                set_connector(override)
                connector = get_connector()
        self.assertIs(connector, override)
        ctor.assert_not_called()

    def test_get_connector_caches_until_set_connector(self):
        with mock.patch.dict(os.environ, {"LOG_CONNECTOR": "local"}):
            first = get_connector()
            with mock.patch.dict(os.environ, {"LOG_CONNECTOR": "azure"}):
                second = get_connector()
        self.assertIs(first, second)
        self.assertIsInstance(first, LocalLogConnector)


if __name__ == "__main__":
    unittest.main()
