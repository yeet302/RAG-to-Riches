"""Evidence-support confidence: how much the evidence the agents returned supports an answer.

The score is computed here, in Python, from what the specialist agents actually returned for this
question. The model never writes a confidence number (answer_synthesis.SYSTEM_PROMPT forbids it).

What the number means
  "How strongly the evidence the agents returned supports this answer", 0-95%.
  It is NOT the probability that a diagnosed cause is correct.

Signals (each 0-1), all taken from this request's AgentFindings and the validated answer
  coverage       share of called agents that returned usable evidence (status "ok" with evidence)
  corroboration  how many independent agents the answer actually cites (1 -> 0.5, 2 -> 0.8, 3 -> 1.0)
  relevance      the agents' own relevance scores for their matches (AgentFinding.confidence), averaged
  grounding      AI answer whose citations were validated (1.0), evidence list only (0.6),
                 AI answer rejected and evidence shown instead (0.4); lower if context was truncated

The weights and band thresholds live in confidence_policy.json (or the file named by
CONFIDENCE_POLICY_PATH), so the team can tune them without code changes.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from schemas import CopilotResponse

ROOT = Path(__file__).resolve().parent
DEFAULT_POLICY_PATH = ROOT / "confidence_policy.json"
COMPONENTS = ("coverage", "corroboration", "relevance", "grounding")
DEFAULT_WEIGHTS = {"coverage": 0.30, "corroboration": 0.30, "relevance": 0.20, "grounding": 0.20}
CORROBORATION = {0: 0.0, 1: 0.5, 2: 0.8}       # 3 or more independent agents -> 1.0
GROUNDING = {"llm": 1.0, "evidence": 0.6, "evidence_fallback": 0.4}
TRUNCATION_FACTOR = 0.85
PLANNING_FALLBACK_FACTOR = 0.95


@dataclass(frozen=True)
class ConfidencePolicy:
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    max_score: int = 95
    high_threshold: int = 75
    medium_threshold: int = 50

    def __post_init__(self):
        if set(self.weights) != set(COMPONENTS):
            raise ValueError(f"confidence weights must define exactly {COMPONENTS}")
        if any(not math.isfinite(w) or w < 0 for w in self.weights.values()):
            raise ValueError("confidence weights must be finite and non-negative")
        if abs(sum(self.weights.values()) - 1.0) > 1e-6:
            raise ValueError("confidence weights must add up to 1")
        if not 0 < self.medium_threshold < self.high_threshold <= self.max_score <= 100:
            raise ValueError("thresholds must satisfy 0 < medium < high <= max_score <= 100")

    def band(self, score: int) -> str:
        if score >= self.high_threshold:
            return "high"
        return "medium" if score >= self.medium_threshold else "low"

    @classmethod
    def from_file(cls, path: Path) -> "ConfidencePolicy":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown confidence policy fields: {sorted(unknown)}")
        return cls(**data)


@lru_cache(maxsize=4)
def _load(path: str) -> ConfidencePolicy:
    return ConfidencePolicy.from_file(Path(path)) if Path(path).exists() else ConfidencePolicy()


def load_policy() -> ConfidencePolicy:
    """CONFIDENCE_POLICY_PATH if set, else orchestrator/confidence_policy.json, else defaults."""
    return _load(os.getenv("CONFIDENCE_POLICY_PATH", str(DEFAULT_POLICY_PATH)))


@dataclass
class ConfidenceResult:
    score: int
    band: str
    reasons: list[str]
    components: dict[str, float]


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def _contributing_agents(response: CopilotResponse) -> list[str]:
    """Agents the answer actually rests on: cited evidence for an AI answer, else all evidence."""
    cited = set()
    if response.synthesis and response.synthesis.source == "llm":
        cited.update(response.synthesis.citations)
        for step in response.steps:
            cited.update(step.evidence_refs)
    agents = [e.agent for e in response.evidence if e.agent and (not cited or e.ref in cited)]
    return list(dict.fromkeys(agents))


def score_confidence(response: CopilotResponse, policy: ConfidencePolicy | None = None) -> ConfidenceResult:
    policy = policy or load_policy()
    findings = response.findings
    failed = [f for f in findings if f.status in {"error", "no_access"}]

    if not response.evidence or not findings:
        reasons = ["No supporting evidence was found, so the answer has nothing to stand on."]
        if failed:
            reasons.append("Unavailable: " + ", ".join(
                f"{f.agent} ({f.error.code if f.error else f.status})" for f in failed) + ".")
        return ConfidenceResult(0, "low", reasons, {c: 0.0 for c in COMPONENTS})

    ok = [f for f in findings if f.status == "ok"]
    contributing = _contributing_agents(response)
    source = response.synthesis.source if response.synthesis else "evidence"

    coverage = len(ok) / len(findings)
    corroboration = CORROBORATION.get(len(contributing), 1.0)
    relevance = sum(min(max(f.confidence, 0.0), 1.0) for f in ok) / len(ok) if ok else 0.0
    grounding = GROUNDING.get(source, 0.6)
    truncated = bool(response.synthesis and response.synthesis.context_truncated)
    if truncated:
        grounding *= TRUNCATION_FACTOR
    components = {"coverage": coverage, "corroboration": corroboration,
                  "relevance": relevance, "grounding": grounding}

    raw = sum(policy.weights[name] * value for name, value in components.items())
    planning_fallback = bool(response.analysis and response.analysis.source == "keyword_fallback")
    if planning_fallback:
        raw *= PLANNING_FALLBACK_FACTOR
    uncapped = round(raw * 100)
    score = min(uncapped, policy.max_score)

    # ---- reasons, most decision-relevant first ----
    reasons = []
    line = f"{len(ok)} of {len(findings)} agents returned usable evidence"
    others = [f for f in findings if f.status != "ok"]
    if others:
        line += " (" + ", ".join(
            f"{f.agent}: {f.error.code if f.error else f.status.replace('_', ' ')}" for f in others) + ")"
    reasons.append(line + ".")
    if len(contributing) >= 2:
        reasons.append(f"The answer rests on {len(contributing)} independent agents "
                       f"({', '.join(contributing)}), so one source's mistake is less likely to mislead.")
    elif contributing:
        reasons.append(f"Only the {contributing[0]} agent supports the answer; no independent source confirms it.")
    reasons.append(f"Agents rated their matches {_pct(relevance)} relevant on average.")
    reasons.append({
        "llm": "The AI answer cites its sources, and every citation was checked against the evidence.",
        "evidence": "Evidence list only: no AI explanation of how the sources fit together.",
        "evidence_fallback": "The AI answer failed validation, so only the raw evidence is shown.",
    }.get(source, "Evidence list only."))
    if truncated:
        reasons.append("Some evidence didn't fit in the AI's context, so the answer may miss it.")
    if planning_fallback:
        reasons.append("AI planning was unavailable, so keyword routing chose the agents.")
    if uncapped > policy.max_score:
        reasons.append(f"Capped at {policy.max_score}%: automated checks can't rule out every alternative.")

    return ConfidenceResult(score, policy.band(score), reasons,
                            {name: round(value, 3) for name, value in components.items()})


def apply_confidence(response: CopilotResponse, policy: ConfidencePolicy | None = None) -> CopilotResponse:
    result = score_confidence(response, policy)
    response.confidence = result.score
    response.confidence_band = result.band
    response.confidence_reasons = result.reasons
    response.confidence_components = result.components
    return response
