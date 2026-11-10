"""Confidence score v2: how strongly what the sub-agents returned supports this answer.

The full definition, with the reasoning behind each weight, is in CONFIDENCE_DEFINITION.md.
The score is computed here, in Python, from this request's AgentFindings and the validated
answer. The model never writes a confidence number (answer_synthesis.SYSTEM_PROMPT forbids it).

Five parts, each 0-1:
  agreement          logs and code point to the same cause (explicit link from the code agent to
                     a log event, plus specific signals both sides share). Only when both were asked.
  docs_relevance     the best docs excerpt covers the question's key terms and mentions what the
                     logs/code agents found. Only when docs was asked.
  agent_context      per sub-agent: returned evidence, searched its full source (no coverage gaps),
                     and its own relevance rating. Averaged over the agents that were asked.
  measured_accuracy  how often each agent the answer relies on was right in the team's planted-bug
                     and accuracy tests (confidence_calibration.json). Unmeasured agents count as 50%.
  grounding          the AI answer's citations were validated against the evidence.

Score = weighted average of the parts that apply (weights in confidence_policy.json), then:
no evidence -> 0; AI planning fell back to keywords -> x0.95; logs and code disagree -> at most
Medium (74); never above 95.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field, fields
from functools import lru_cache
from pathlib import Path
from typing import Optional

from schemas import AgentFinding, CopilotResponse

ROOT = Path(__file__).resolve().parent
DEFAULT_POLICY_PATH = ROOT / "confidence_policy.json"
DEFAULT_CALIBRATION_PATH = ROOT / "confidence_calibration.json"
VERSION = "v2"
COMPONENTS = ("agreement", "docs_relevance", "agent_context", "measured_accuracy", "grounding")
DEFAULT_WEIGHTS = {"agreement": 0.30, "docs_relevance": 0.10, "agent_context": 0.20,
                   "measured_accuracy": 0.20, "grounding": 0.20}
GROUNDING = {"llm": 1.0, "evidence": 0.6, "evidence_fallback": 0.4}
TRUNCATION_FACTOR = 0.85
PLANNING_FALLBACK_FACTOR = 0.95
COVERAGE_GAP_FACTOR = 0.8          # agent said part of its source was out of reach
LABELS = {"agreement": "Logs-code agreement", "docs_relevance": "Docs relevance",
          "agent_context": "Sub-agent context", "measured_accuracy": "Measured accuracy",
          "grounding": "Grounding"}


# --------------------------------------------------------------------------- policy

@dataclass(frozen=True)
class ConfidencePolicy:
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    max_score: int = 95
    high_threshold: int = 75
    medium_threshold: int = 50
    disagreement_cap: int = 74      # logs and code don't agree -> the score can't be High

    def __post_init__(self):
        if set(self.weights) != set(COMPONENTS):
            raise ValueError(f"confidence weights must define exactly {COMPONENTS}")
        if any(not math.isfinite(w) or w < 0 for w in self.weights.values()):
            raise ValueError("confidence weights must be finite and non-negative")
        if abs(sum(self.weights.values()) - 1.0) > 1e-6:
            raise ValueError("confidence weights must add up to 1")
        if not 0 < self.medium_threshold < self.high_threshold <= self.max_score <= 100:
            raise ValueError("thresholds must satisfy 0 < medium < high <= max_score <= 100")
        if not 0 <= self.disagreement_cap <= self.max_score:
            raise ValueError("disagreement_cap must be between 0 and max_score")

    def band(self, score: int) -> str:
        if score >= self.high_threshold:
            return "high"
        return "medium" if score >= self.medium_threshold else "low"

    @classmethod
    def from_file(cls, path: Path) -> ConfidencePolicy:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        data.pop("_comment", None)
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown confidence policy fields: {sorted(unknown)}")
        return cls(**data)


@lru_cache(maxsize=4)
def _load_policy(path: str) -> ConfidencePolicy:
    return ConfidencePolicy.from_file(Path(path)) if Path(path).exists() else ConfidencePolicy()


def load_policy() -> ConfidencePolicy:
    """CONFIDENCE_POLICY_PATH if set, else orchestrator/confidence_policy.json, else defaults."""
    return _load_policy(os.getenv("CONFIDENCE_POLICY_PATH", str(DEFAULT_POLICY_PATH)))


# --------------------------------------------------------------------------- calibration

@dataclass(frozen=True)
class Measurement:
    """One team test result for an agent: `correct` right out of `total` checked cases."""
    name: str
    correct: int
    total: int
    measured_by: str = ""
    date: str = ""
    source: str = ""
    metric: str = ""

    def __post_init__(self):
        if self.total < 1 or not 0 <= self.correct <= self.total:
            raise ValueError(f"measurement '{self.name}': need 0 <= correct <= total and total >= 1")


@dataclass(frozen=True)
class Calibration:
    """Measured accuracy per agent. Reliability = (correct + 1) / (total + 2), so a handful of
    test cases moves the number only part of the way, and an unmeasured agent sits at 50%."""
    measurements: dict[str, tuple[Measurement, ...]] = field(default_factory=dict)
    prior_correct: int = 1
    prior_total: int = 2

    def reliability(self, agent: str) -> float:
        items = self.measurements.get(agent, ())
        correct = sum(m.correct for m in items) + self.prior_correct
        total = sum(m.total for m in items) + self.prior_total
        return correct / total

    def describe(self, agent: str) -> str:
        items = self.measurements.get(agent, ())
        if not items:
            return f"{agent} not measured yet (counted as {_pct(self.reliability(agent))})"
        correct, total = sum(m.correct for m in items), sum(m.total for m in items)
        return f"{agent} {_pct(self.reliability(agent))} ({correct}/{total} team test cases)"

    @classmethod
    def from_file(cls, path: Path) -> Calibration:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        prior = data.get("prior", {"correct": 1, "total": 2})
        agents = data.get("agents", {})
        unknown = set(agents) - {"logs", "docs", "code"}
        if unknown:
            raise ValueError(f"unknown agents in calibration: {sorted(unknown)}")
        allowed = {f.name for f in fields(Measurement)}
        measurements = {}
        for agent, items in agents.items():
            parsed = []
            for item in items:
                extra = set(item) - allowed - {"notes"}
                if extra:
                    raise ValueError(f"unknown measurement fields for {agent}: {sorted(extra)}")
                parsed.append(Measurement(**{k: v for k, v in item.items() if k in allowed}))
            measurements[agent] = tuple(parsed)
        if not 0 < prior["correct"] < prior["total"]:
            raise ValueError("prior must satisfy 0 < correct < total")
        return cls(measurements, prior["correct"], prior["total"])


@lru_cache(maxsize=4)
def _load_calibration(path: str) -> Calibration:
    return Calibration.from_file(Path(path)) if Path(path).exists() else Calibration()


def load_calibration() -> Calibration:
    """CONFIDENCE_CALIBRATION_PATH if set, else orchestrator/confidence_calibration.json."""
    return _load_calibration(os.getenv("CONFIDENCE_CALIBRATION_PATH", str(DEFAULT_CALIBRATION_PATH)))


# --------------------------------------------------------------------------- text signals

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.:/\-]*[A-Za-z0-9]")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_CODE_FILE = re.compile(r"\.(java|py|go|cs|ts|tsx|js|kt|rb|rs|cpp|c|h|php|scala|json|ya?ml|proto)$", re.I)
_GENERIC_CAPS = {"ERROR", "ERRORS", "WARN", "WARNING", "INFO", "DEBUG", "TRACE", "FATAL", "HTTPS",
                 "FALSE", "NULL", "NONE", "TODO", "OTLP", "JSON", "UTF8"}
_STOPWORDS = set("""a an and are as at be been being but by can could did do does doing for from had has
have how i if in into is it its may might my no not of on or our should so some such than that the their
them then there these they this those to too was we were what when where which while who why will with
would you your any about after again also because before especially instead just more most only other
over same sometimes still unusually very via""".split())


def _stem(word: str) -> str:
    for suffix in ("ing", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def key_terms(text: str) -> set[str]:
    """Content words of a question, lower-cased and lightly stemmed ("failing" -> "fail")."""
    return {_stem(w.lower()) for w in _WORD.findall(text or "") if w.lower() not in _STOPWORDS}


def _specific(token: str) -> bool:
    return bool(re.search(r"[a-z][A-Z]", token)                         # camelCase: GetAds, adFailure
                or ("_" in token and re.search(r"[A-Za-z]", token))     # AD_FAILURE
                or ":" in token                                         # inject:ads-unavailable
                or _CODE_FILE.search(token)                             # AdService.java
                or (token.isupper() and len(token) >= 5 and token not in _GENERIC_CAPS))  # UNAVAILABLE


def signal_names(text: str) -> dict[str, str]:
    """Specific technical identifiers in text (names, flags, status codes, tags, file names),
    keyed by a normalised form so AD_FAILURE and adFailure match; values keep the original
    spelling for explanations. Plain English words are ignored."""
    found: dict[str, str] = {}
    for raw in _TOKEN.findall(text or ""):
        token = raw.strip(".:/-")
        parts = [p for p in re.split(r"[./]", token) if p] if "/" in token else [token]
        if "/" not in token and "." in token:
            parts += [p for p in token.split(".") if p and p != token]
        for part in parts:
            if _specific(part):
                found.setdefault(part.lower().replace("_", ""), part)
                if _CODE_FILE.search(part):
                    stem = _CODE_FILE.sub("", part)
                    found.setdefault(stem.lower(), stem)       # AdService.java -> adservice
    return found


def signals(text: str) -> set[str]:
    return set(signal_names(text))


def _finding_text(finding: AgentFinding, include_refs: bool) -> str:
    parts = []
    for item in finding.evidence:
        parts += [item.summary, item.snippet or ""]
        if include_refs:
            parts.append(item.ref.split("@")[0])         # code refs: path@sha:Lx-Ly -> path
    return "\n".join(parts)


# --------------------------------------------------------------------------- components

@dataclass
class Part:
    value: Optional[float]           # None = not applicable to this question
    reason: str


def _usable(finding: Optional[AgentFinding]) -> bool:
    return bool(finding and finding.status == "ok" and finding.evidence)


def agreement_part(response: CopilotResponse, by_agent: dict[str, AgentFinding]) -> Part:
    logs, code = by_agent.get("logs"), by_agent.get("code")
    if not logs or not code:
        return Part(None, "Logs-code agreement: not applicable (logs and code were not both asked).")
    if not _usable(logs) or not _usable(code):
        missing = [f.agent for f in (logs, code) if not _usable(f)]
        return Part(0.0, f"Logs-code agreement 0%: {' and '.join(missing)} returned no usable evidence, "
                         "so nothing confirms the cause from both sides.")
    asked = signals(response.question)
    log_names = signal_names(_finding_text(logs, False))
    shared = sorted(log_names[k] for k in (set(log_names) & signals(_finding_text(code, True))) - asked)
    log_refs = {e.ref for e in logs.evidence}
    linked = [s.removeprefix("Upstream: ") for s in code.sources_searched
              if s.startswith("Upstream: ") and s.removeprefix("Upstream: ") in log_refs]
    if linked and shared:
        value = 1.0
    elif linked:
        value = 0.7
    else:
        value = {0: 0.1, 1: 0.5}.get(len(shared), 0.7)
    detail = []
    if linked:
        detail.append(f"the code agent tied its code to {len(linked)} log event(s)")
    if shared:
        detail.append("both point to " + ", ".join(shared[:4]))
    if not detail:
        detail.append("nothing specific links the log events to the code the code agent found")
    return Part(value, f"Logs-code agreement {_pct(value)}: " + "; ".join(detail) + ".")


def docs_relevance_part(response: CopilotResponse, by_agent: dict[str, AgentFinding]) -> Part:
    docs = by_agent.get("docs")
    if not docs:
        return Part(None, "Docs relevance: not applicable (docs was not asked).")
    if not _usable(docs):
        return Part(0.0, "Docs relevance 0%: the docs agent returned no usable excerpt.")
    excerpts = [f"{e.summary}\n{e.snippet or ''}" for e in docs.evidence]

    def coverage(text: str) -> float:
        terms = key_terms(text)
        return max((len(terms & key_terms(x)) / len(terms) for x in excerpts), default=0.0) if terms else 0.0

    # The docs are relevant if they cover the question, or the cause the answer settled on.
    best = max(coverage(response.question), coverage(response.likely_cause or ""))
    other: dict[str, str] = {}
    for name in ("logs", "code"):
        if _usable(by_agent.get(name)):
            other.update(signal_names(_finding_text(by_agent[name], name == "code")))
    for key in signals(response.question):
        other.pop(key, None)
    mentioned = sorted(other[k] for k in set(other) & signals("\n".join(excerpts)))
    if other:
        value = 0.7 * best + 0.3 * (1.0 if mentioned else 0.0)
        tail = (f"; it mentions {', '.join(mentioned[:3])}, which the other agents found"
                if mentioned else "; it doesn't mention what the logs/code agents found")
    else:
        value, tail = best, ""
    return Part(value, f"Docs relevance {_pct(value)}: the best docs excerpt covers "
                       f"{_pct(best)} of the key terms of the question or likely cause{tail}.")


def _agent_quality(finding: AgentFinding) -> float:
    if not _usable(finding):
        return 0.0
    quality = 0.6 + 0.4 * min(max(finding.confidence, 0.0), 1.0)
    if finding.sources_unavailable:
        quality *= COVERAGE_GAP_FACTOR
    return quality


def agent_context_part(findings: list[AgentFinding]) -> Part:
    value = sum(_agent_quality(f) for f in findings) / len(findings)
    notes = []
    for f in findings:
        if not _usable(f):
            why = f.error.code if f.error else f.status.replace("_", " ")
            notes.append(f"{f.agent}: {why}")
        elif f.sources_unavailable:
            notes.append(f"{f.agent}: partial coverage ({f.sources_unavailable[0]})")
        else:
            notes.append(f"{f.agent}: evidence, rated {_pct(f.confidence)} relevant")
    return Part(value, f"Sub-agent context {_pct(value)}: " + "; ".join(notes) + ".")


def _contributing_agents(response: CopilotResponse) -> list[str]:
    """Agents the answer actually rests on: cited evidence for an AI answer, else all evidence."""
    cited = set()
    if response.synthesis and response.synthesis.source == "llm":
        cited.update(response.synthesis.citations)
        for step in response.steps:
            cited.update(step.evidence_refs)
    agents = [e.agent for e in response.evidence if e.agent and (not cited or e.ref in cited)]
    return list(dict.fromkeys(agents))


def measured_accuracy_part(response: CopilotResponse, calibration: Calibration) -> Part:
    agents = _contributing_agents(response)
    if not agents:
        return Part(0.0, "Measured accuracy 0%: no agent's evidence supports the answer.")
    value = sum(calibration.reliability(a) for a in agents) / len(agents)
    return Part(value, f"Measured accuracy {_pct(value)}: "
                       + "; ".join(calibration.describe(a) for a in agents) + ".")


def grounding_part(response: CopilotResponse) -> Part:
    source = response.synthesis.source if response.synthesis else "evidence"
    value = GROUNDING.get(source, 0.6)
    text = {"llm": "every citation in the AI answer was checked against the evidence",
            "evidence": "evidence list only, no AI explanation of how the sources fit together",
            "evidence_fallback": "the AI answer failed validation, so only raw evidence is shown",
            }.get(source, "evidence list only")
    if response.synthesis and response.synthesis.context_truncated:
        value *= TRUNCATION_FACTOR
        text += "; some evidence didn't fit in the AI's context"
    return Part(value, f"Grounding {_pct(value)}: {text}.")


# --------------------------------------------------------------------------- score

@dataclass
class ConfidenceResult:
    score: int
    band: str
    reasons: list[str]
    components: dict[str, float]


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def score_confidence(response: CopilotResponse, policy: ConfidencePolicy | None = None,
                     calibration: Calibration | None = None) -> ConfidenceResult:
    policy = policy or load_policy()
    calibration = calibration or load_calibration()
    findings = response.findings
    if not response.evidence or not findings:
        reasons = ["No supporting evidence was found, so the answer has nothing to stand on."]
        failed = [f for f in findings if f.status in {"error", "no_access"}]
        if failed:
            reasons.append("Unavailable: " + ", ".join(
                f"{f.agent} ({f.error.code if f.error else f.status})" for f in failed) + ".")
        return ConfidenceResult(0, "low", reasons, {})

    by_agent = {f.agent: f for f in findings}
    parts = {
        "agreement": agreement_part(response, by_agent),
        "docs_relevance": docs_relevance_part(response, by_agent),
        "agent_context": agent_context_part(findings),
        "measured_accuracy": measured_accuracy_part(response, calibration),
        "grounding": grounding_part(response),
    }
    applicable = {name: part for name, part in parts.items() if part.value is not None}
    weight = sum(policy.weights[name] for name in applicable)
    raw = sum(policy.weights[name] * part.value for name, part in applicable.items()) / weight

    caps = []
    planning_fallback = bool(response.analysis and response.analysis.source == "keyword_fallback")
    if planning_fallback:
        raw *= PLANNING_FALLBACK_FACTOR
    score = round(raw * 100)
    agreement = parts["agreement"].value
    if agreement is not None and agreement < 0.5:
        score = min(score, policy.disagreement_cap)
        caps.append(f"At most {policy.disagreement_cap}%: logs and code don't clearly point to the "
                    "same cause, so this can't be High.")
    if score > policy.max_score:
        score = policy.max_score
        caps.append(f"Capped at {policy.max_score}%: automated checks can't rule out every alternative.")

    reasons = [part.reason for part in applicable.values()]
    reasons += [part.reason for part in parts.values() if part.value is None]
    if planning_fallback:
        reasons.append("AI planning was unavailable, so keyword routing chose the agents (x0.95).")
    reasons += caps
    return ConfidenceResult(score, policy.band(score), reasons,
                            {name: round(part.value, 3) for name, part in applicable.items()})


def apply_confidence(response: CopilotResponse, policy: ConfidencePolicy | None = None,
                     calibration: Calibration | None = None) -> CopilotResponse:
    result = score_confidence(response, policy, calibration)
    response.confidence = result.score
    response.confidence_band = result.band
    response.confidence_reasons = result.reasons
    response.confidence_components = result.components
    return response
