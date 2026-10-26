"""Unit tests for investigation benchmarking metrics (no Foundry calls)."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from foundry_agent import DEFAULT_FOUNDRY_MODEL, get_foundry_model
from investigation_metrics import InvestigationMetricsCollector


class FoundryModelConfigTests(unittest.TestCase):
    def test_prefers_foundry_model_env(self):
        with mock.patch.dict(
            os.environ,
            {
                "FOUNDRY_MODEL": "logs-model-router",
                "MODEL_DEPLOYMENT_NAME": "gpt-4.1-mini",
            },
            clear=False,
        ):
            self.assertEqual(get_foundry_model(), "logs-model-router")

    def test_falls_back_to_legacy_then_default(self):
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"FOUNDRY_MODEL", "MODEL_DEPLOYMENT_NAME"}
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(get_foundry_model(), DEFAULT_FOUNDRY_MODEL)
            os.environ["MODEL_DEPLOYMENT_NAME"] = "legacy-dep"
            self.assertEqual(get_foundry_model(), "legacy-dep")


class InvestigationMetricsCollectorTests(unittest.TestCase):
    def test_records_response_model_and_usage(self):
        collector = InvestigationMetricsCollector("logs-model-router")
        response = SimpleNamespace(
            model="gpt-4.1-2025-04-14",
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
            ),
            output=[
                SimpleNamespace(type="function_call", name="search_logs"),
            ],
        )
        step = collector.record_response(
            response=response,
            latency_seconds=1.5,
            tool_calls=["search_logs"],
        )
        self.assertEqual(step.step, 1)
        self.assertEqual(step.configured_deployment, "logs-model-router")
        self.assertEqual(step.serving_model, "gpt-4.1-2025-04-14")
        self.assertEqual(step.input_tokens, 100)
        self.assertEqual(step.output_tokens, 20)
        self.assertEqual(step.total_tokens, 120)
        self.assertEqual(step.tool_calls, ["search_logs"])

        response2 = SimpleNamespace(
            model="gpt-4.1-mini-2025-04-14",
            usage=SimpleNamespace(
                input_tokens=50,
                output_tokens=10,
                total_tokens=60,
            ),
            output=[],
        )
        collector.record_response(
            response=response2,
            latency_seconds=0.8,
            tool_calls=[],
        )

        metrics = collector.finalize(2.4)
        self.assertEqual(metrics.configured_deployment, "logs-model-router")
        self.assertEqual(
            metrics.underlying_models_used,
            ["gpt-4.1-2025-04-14", "gpt-4.1-mini-2025-04-14"],
        )
        self.assertEqual(metrics.model_call_count, 2)
        self.assertEqual(metrics.tool_call_count, 1)
        self.assertEqual(metrics.input_tokens, 150)
        self.assertEqual(metrics.output_tokens, 30)
        self.assertEqual(metrics.total_tokens, 180)
        self.assertEqual(metrics.elapsed_seconds, 2.4)
        self.assertEqual(len(metrics.steps), 2)
        self.assertIn("serving_model", metrics.to_dict()["steps"][0])


if __name__ == "__main__":
    unittest.main()
