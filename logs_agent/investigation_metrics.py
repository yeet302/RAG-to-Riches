"""
Benchmarking / routing metrics for a single log investigation.

Kept separate from LogInvestigationResult so orchestrator-facing output
stays free of evaluation or cost-tracking fields.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class ModelStepMetrics:
    """One Foundry Responses API call within an investigation."""

    step: int
    configured_deployment: str
    serving_model: Optional[str]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    total_tokens: Optional[int]
    latency_seconds: float
    tool_calls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InvestigationMetrics:
    """Aggregated metrics for one investigate_logs run."""

    configured_deployment: str
    underlying_models_used: list[str] = field(default_factory=list)
    model_call_count: int = 0
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0
    steps: list[ModelStepMetrics] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured_deployment": self.configured_deployment,
            "underlying_models_used": list(self.underlying_models_used),
            "model_call_count": self.model_call_count,
            "tool_call_count": self.tool_call_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "elapsed_seconds": self.elapsed_seconds,
            "steps": [step.to_dict() for step in self.steps],
        }


class InvestigationMetricsCollector:
    """Mutable collector used while the agent tool loop runs."""

    def __init__(self, configured_deployment: str) -> None:
        self.configured_deployment = configured_deployment
        self.steps: list[ModelStepMetrics] = []
        self._models_seen: list[str] = []
        self._models_seen_set: set[str] = set()
        self.tool_call_count: int = 0
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.total_tokens: int = 0

    def record_response(
        self,
        *,
        response: Any,
        latency_seconds: float,
        tool_calls: Optional[list[str]] = None,
    ) -> ModelStepMetrics:
        """
        Record one Responses API result.

        Token fields come from response.usage when present:
          usage.input_tokens, usage.output_tokens, usage.total_tokens
        Serving model comes from response.model.
        """
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage else None
        output_tokens = getattr(usage, "output_tokens", None) if usage else None
        total_tokens = getattr(usage, "total_tokens", None) if usage else None

        serving_raw = getattr(response, "model", None)
        serving_model = str(serving_raw) if serving_raw is not None else None

        calls = list(tool_calls or [])
        step = ModelStepMetrics(
            step=len(self.steps) + 1,
            configured_deployment=self.configured_deployment,
            serving_model=serving_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            latency_seconds=latency_seconds,
            tool_calls=calls,
        )
        self.steps.append(step)

        if serving_model and serving_model not in self._models_seen_set:
            self._models_seen_set.add(serving_model)
            self._models_seen.append(serving_model)

        self.tool_call_count += len(calls)
        if isinstance(input_tokens, int):
            self.input_tokens += input_tokens
        if isinstance(output_tokens, int):
            self.output_tokens += output_tokens
        if isinstance(total_tokens, int):
            self.total_tokens += total_tokens
        elif isinstance(input_tokens, int) or isinstance(output_tokens, int):
            self.total_tokens += (input_tokens or 0) + (output_tokens or 0)

        return step

    def finalize(self, elapsed_seconds: float) -> InvestigationMetrics:
        return InvestigationMetrics(
            configured_deployment=self.configured_deployment,
            underlying_models_used=list(self._models_seen),
            model_call_count=len(self.steps),
            tool_call_count=self.tool_call_count,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_tokens=self.total_tokens,
            elapsed_seconds=elapsed_seconds,
            steps=list(self.steps),
        )
