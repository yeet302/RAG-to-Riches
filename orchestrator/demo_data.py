"""
Hard-coded demo scenarios for the Tuesday demo.

Scenario system: the OpenTelemetry Astronomy Shop demo
(https://github.com/open-telemetry/opentelemetry-demo), with the ad service as our
team's service. Failures are injected with the demo's flagd feature flags
(adFailure, adHighCpu, adManualGc), so every scenario here can be reproduced by
running the demo and flipping a flag.

Code references point at real lines in the repo, pinned to one commit so links
don't drift. Log lines are representative samples of what the demo emits;
replace them with real captured logs once the logs agent is connected.
"""
from __future__ import annotations

from schemas import AgentFinding, Evidence, Incident, Step

COMMIT = "e5552ee25bcede2900d89188f076c464cd73c074"
GH = f"https://github.com/open-telemetry/opentelemetry-demo/blob/{COMMIT}"
AD_JAVA = "src/ad/src/main/java/oteldemo/AdService.java"
CPU_JAVA = "src/ad/src/main/java/oteldemo/problempattern/CPULoad.java"
FLAGD = "src/flagd/demo.flagd.json"
DOCS_FLAGS = "https://opentelemetry.io/docs/demo/feature-flags/"
DOCS_AD = "https://opentelemetry.io/docs/demo/services/ad/"
FLAG_UI = "http://localhost:8080/feature"
JAEGER = "http://localhost:8080/jaeger/ui/search?service=ad"


def gh(path: str, start: int, end: int | None = None) -> str:
    return f"{GH}/{path}#L{start}" + (f"-L{end}" if end else "")


# --------------------------------------------------------------------------
# Scenario 1: ad service errors (adFailure flag)  -> logs + docs, then code
# --------------------------------------------------------------------------
AD_FAILURE = {
    "id": "ad-failure",
    "keywords": ["error", "errors", "fail", "failing", "failure", "unavailable",
                 "500", "not loading", "not showing", "missing", "broken", "getads"],
    "findings": {
        "logs": AgentFinding(
            agent="logs",
            summary="About 1 in 10 GetAds calls fail with gRPC UNAVAILABLE since 14:02. No other ad service errors.",
            hypotheses=["Ad service is rejecting a fixed share of requests (not a crash or outage)"],
            evidence=[
                Evidence(source_type="log", agent="logs",
                         summary="Ad service logs 'GetAds Failed with status UNAVAILABLE', starting 14:02.",
                         ref="service=ad level=WARN since 14:02 (412 matches)",
                         link=JAEGER,
                         snippet=("14:02:07 WARN  oteldemo.AdService - GetAds Failed with status "
                                  "Status{code=UNAVAILABLE, description=null, cause=null}\n"
                                  "14:02:11 WARN  oteldemo.AdService - GetAds Failed with status "
                                  "Status{code=UNAVAILABLE, description=null, cause=null}")),
                Evidence(source_type="metric", agent="logs",
                         summary="Error rate on GetAds is steady at ~10%, while CPU and memory are normal.",
                         ref="span status=ERROR / total, service=ad, 15 min window",
                         link=JAEGER),
            ],
            sources_searched=["ad service logs", "frontend logs", "Jaeger traces"],
            confidence=0.8, latency_ms=640,
        ),
        "docs": AgentFinding(
            agent="docs",
            summary="The demo's feature-flag docs list adFailure ('Fail ad service') as a built-in failure scenario.",
            evidence=[
                Evidence(source_type="doc", agent="docs",
                         summary="Feature flag adFailure: 'Fail ad service'. Toggled from the flagd UI.",
                         ref="OpenTelemetry Demo docs > Feature Flags", link=DOCS_FLAGS),
                Evidence(source_type="incident", agent="docs",
                         summary="Similar past incident INC-1007 (demo): adFailure left on after a test; fixed by turning it off.",
                         ref="INC-1007 (demo incident)", link=None),
            ],
            sources_searched=["OpenTelemetry demo docs", "team runbooks", "past incidents"],
            confidence=0.7, latency_ms=410,
        ),
        "code": AgentFinding(
            agent="code",
            summary="AdService.getAds throws UNAVAILABLE on 1 of 10 requests when the adFailure flag is on.",
            evidence=[
                Evidence(source_type="code", agent="code",
                         summary="The failure path: if adFailure is on, 1 in 10 requests throws UNAVAILABLE.",
                         ref=f"{AD_JAVA}:L237-L240", link=gh(AD_JAVA, 237, 240),
                         snippet=("// Throw 1/10 of the time to simulate a failure when the feature flag is enabled\n"
                                  "if (ffClient.getBooleanValue(AD_FAILURE, false, evaluationContext) && random.nextInt(10) == 0) {\n"
                                  "  throw new StatusRuntimeException(Status.UNAVAILABLE);\n}")),
                Evidence(source_type="code", agent="code",
                         summary="The flag is defined in flagd config; default variant is 'off'.",
                         ref=f"{FLAGD}:L4-L12", link=gh(FLAGD, 4, 12)),
            ],
            sources_searched=["opentelemetry-demo repo (src/ad, src/flagd)"],
            confidence=0.9, latency_ms=820,
        ),
    },
    "synthesis": {
        "answer": "The adFailure feature flag is on. The ad service is deliberately failing about 1 in 10 GetAds requests.",
        "likely_cause": ("The adFailure feature flag was switched on in flagd, so AdService.getAds throws "
                         "UNAVAILABLE on roughly 1 in 10 requests. Product pages then show no ads."),
        "confidence": 90,
        "confidence_reasons": [
            "Error rate is ~10%, exactly what the code's random.nextInt(10) check produces",
            "Only UNAVAILABLE errors, no crashes, CPU and memory normal",
            "A past incident (INC-1007) had the same pattern and fix",
        ],
        "not_ruled_out": "a network problem between frontend and ad service. Copilot can't see Envoy proxy logs yet.",
        "not_ruled_out_pct": 10,
        "sources_unavailable": ["frontend-proxy (Envoy) access logs"],
        "steps": [
            Step(title="Turn off the adFailure flag", kind="mitigation", risk="low",
                 why="The code only fails when this flag is on, so turning it off removes the cause.",
                 command=f"Open {FLAG_UI} and set adFailure to off",
                 expect="GetAds errors drop to zero within about a minute.", link=FLAG_UI),
            Step(title="Confirm the flag's default is off in config", kind="fix", risk="low",
                 why="If someone changed the default in demo.flagd.json, the flag turns back on after a restart.",
                 command=f"git diff HEAD~1 -- {FLAGD}",
                 expect='"adFailure" has "defaultVariant": "off".', link=gh(FLAGD, 4, 12)),
            Step(title="Restart the ad service", kind="mitigation", risk="medium",
                 why="Only if errors continue with the flag off; clears any stuck gRPC connections.",
                 command="docker compose restart ad",
                 expect="Ads load on product pages; no new 'GetAds Failed' warnings."),
        ],
    },
}

# --------------------------------------------------------------------------
# Scenario 2: ad service slow / high CPU (adHighCpu flag) -> logs + docs, then code
# --------------------------------------------------------------------------
AD_HIGH_CPU = {
    "id": "ad-high-cpu",
    "keywords": ["slow", "latency", "cpu", "high cpu", "timeout", "timeouts", "lag", "p95", "p99",
                 "performance", "load", "spike", "spiking", "hot"],
    "findings": {
        "logs": AgentFinding(
            agent="logs",
            summary="Ad service CPU jumped to ~100% at 13:40; GetAds p95 latency went from 8 ms to 450 ms. No errors.",
            hypotheses=["Something inside the ad service is burning CPU", "Traffic spike (ruled down: request rate flat)"],
            evidence=[
                Evidence(source_type="log", agent="logs",
                         summary="Ad service logs 'High CPU-Load problempattern enabled' at 13:40.",
                         ref="service=ad message~'problempattern' since 13:40", link=JAEGER,
                         snippet="13:40:02 INFO  oteldemo.problempattern.CPULoad - High CPU-Load problempattern enabled"),
                Evidence(source_type="metric", agent="logs",
                         summary="Request rate is flat, so this is not a traffic spike.",
                         ref="span count, service=ad, 1h window", link=JAEGER),
            ],
            sources_searched=["ad service logs", "Jaeger traces", "Prometheus metrics"],
            confidence=0.75, latency_ms=590,
        ),
        "docs": AgentFinding(
            agent="docs",
            summary="adHighCpu is a documented demo flag that 'triggers high cpu load in the ad service'.",
            evidence=[
                Evidence(source_type="doc", agent="docs",
                         summary="Feature flag adHighCpu: 'Triggers high cpu load in the ad service'.",
                         ref="OpenTelemetry Demo docs > Feature Flags", link=DOCS_FLAGS),
            ],
            sources_searched=["OpenTelemetry demo docs", "team runbooks"],
            confidence=0.6, latency_ms=380,
        ),
        "code": AgentFinding(
            agent="code",
            summary="When adHighCpu is on, each GetAds call makes sure 4 CPU-burning worker threads are running.",
            evidence=[
                Evidence(source_type="code", agent="code",
                         summary="getAds checks the adHighCpu flag on every request.",
                         ref=f"{AD_JAVA}:L204-L205", link=gh(AD_JAVA, 204, 205),
                         snippet=("CPULoad cpuload = CPULoad.getInstance();\n"
                                  "cpuload.execute(ffClient.getBooleanValue(AD_HIGH_CPU_FEATURE_FLAG, false, evaluationContext));")),
                Evidence(source_type="code", agent="code",
                         summary="CPULoad.execute starts 4 worker threads when enabled, stops them when disabled.",
                         ref=f"{CPU_JAVA}:L46-L57", link=gh(CPU_JAVA, 46, 57)),
            ],
            sources_searched=["opentelemetry-demo repo (src/ad)"],
            confidence=0.9, latency_ms=760,
        ),
    },
    "synthesis": {
        "answer": "The adHighCpu flag is on. It starts 4 CPU-heavy threads in the ad service, which slows every request.",
        "likely_cause": ("The adHighCpu feature flag was turned on at 13:40. The ad service started 4 worker threads "
                         "that saturate the CPU, so GetAds latency rose from 8 ms to 450 ms."),
        "confidence": 85,
        "confidence_reasons": [
            "CPU and latency rose at the same minute the 'High CPU-Load' log line appeared",
            "Request rate stayed flat, so it is not extra traffic",
            "The code path that starts the threads is gated only by this flag",
        ],
        "not_ruled_out": "a noisy neighbour container on the same host. Copilot can't read host-level metrics.",
        "not_ruled_out_pct": 15,
        "sources_unavailable": ["host / node CPU metrics"],
        "steps": [
            Step(title="Turn off the adHighCpu flag", kind="mitigation", risk="low",
                 why="CPULoad.execute(false) stops the worker threads on the next request.",
                 command=f"Open {FLAG_UI} and set adHighCpu to off",
                 expect="Ad service CPU back under 10% and p95 latency under 20 ms within 2 minutes.", link=FLAG_UI),
            Step(title="Check CPU dropped", kind="verify", risk="low",
                 why="Confirms the threads stopped and nothing else is using CPU.",
                 command="docker stats ad --no-stream",
                 expect="CPU % for the ad container is low."),
            Step(title="Escalate to Platform team with the evidence", kind="escalate", risk="other_team",
                 why="If CPU stays high with the flag off, the cause is likely outside the ad service.",
                 command=None, expect=None),
        ],
    },
}

# --------------------------------------------------------------------------
# Scenario 3: a "how do I" question -> docs agent only (selective routing)
# --------------------------------------------------------------------------
HOW_TO_TRIGGER = {
    "id": "how-to-trigger",
    "keywords": ["how do i", "how to", "how can i", "trigger", "simulate", "inject", "turn on", "enable",
                 "feature flag", "flag", "flags", "test"],
    "findings": {
        "docs": AgentFinding(
            agent="docs",
            summary="Failures are injected with flagd feature flags, toggled in the flagd UI or in demo.flagd.json.",
            evidence=[
                Evidence(source_type="doc", agent="docs",
                         summary="Feature flags page lists each failure scenario and how to toggle it.",
                         ref="OpenTelemetry Demo docs > Feature Flags", link=DOCS_FLAGS),
                Evidence(source_type="doc", agent="docs",
                         summary="Ad service page describes the service and its instrumentation.",
                         ref="OpenTelemetry Demo docs > Services > Ad", link=DOCS_AD),
                Evidence(source_type="code", agent="docs",
                         summary="Flag definitions for the ad service: adFailure, adHighCpu, adManualGc.",
                         ref=f"{FLAGD}:L4-L30", link=gh(FLAGD, 4, 30)),
            ],
            sources_searched=["OpenTelemetry demo docs", "opentelemetry-demo repo"],
            confidence=0.9, latency_ms=350,
        ),
    },
    "synthesis": {
        "answer": ("Run the demo (docker compose up), open the flagd UI at http://localhost:8080/feature and switch "
                   "a flag on. For the ad service: adFailure fails ~1 in 10 requests, adHighCpu adds CPU load, and "
                   "adManualGc forces full garbage collections. You can also change defaultVariant in "
                   "src/flagd/demo.flagd.json."),
        "steps": [],
    },
}

SCENARIOS = [AD_FAILURE, AD_HIGH_CPU, HOW_TO_TRIGGER]

SUGGESTED_QUESTIONS = [
    "Ads aren't loading on product pages and the ad service is returning errors. What's wrong?",
    "The ad service is slow and CPU is spiking. Why?",
    "How do I trigger a failure in the ad service for testing?",
]

INCIDENTS = [
    Incident(id="INC-2481", title="Ads failing to load on product pages", severity="SEV2",
             status="Investigating", service="ad", team="Ads team", started="14 min ago",
             assignee="you", summary_label="Likely cause (90%)",
             summary="the adFailure flag was switched on; 1 in 10 ad requests fail.",
             question=SUGGESTED_QUESTIONS[0]),
    Incident(id="INC-2479", title="Ad service latency and CPU climbing", severity="SEV3",
             status="Open", service="ad", team="Ads team", started="52 min ago",
             assignee=None, summary_label="Likely cause (85%)",
             summary="CPU load started at 13:40 with no rise in traffic.",
             question=SUGGESTED_QUESTIONS[1]),
    Incident(id="INC-2466", title="Recommendations missing on product pages", severity="SEV3",
             status="Open", service="recommendation", team="Recommendations team", started="1 h ago",
             assignee=None, summary_label="Likely cause (64%)",
             summary="the recommendation cache started failing 3 minutes before errors.",
             other_team=True,
             question="Recommendations are missing on product pages. What's wrong?"),
    Incident(id="INC-2440", title="Ad service pauses during checkout traffic", severity="SEV2",
             status="Resolved", service="ad", team="Ads team", started="Yesterday",
             assignee="Marco R.", summary_label="Fix worked",
             summary="turning off adManualGc. Copilot will suggest it first next time.",
             question="Why was the ad service pausing during checkout traffic yesterday?"),
]
