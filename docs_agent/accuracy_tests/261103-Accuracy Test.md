# Online Answer Evaluation — 2026-11-03

Run: `20261103T194937Z-8fb54840` (online documentation, gpt-4.1-mini).

**4/6 passed (66.7%)**. D01 and D04 failed; D02, D03, D05 and D06 passed.

## D01 — GetAds intermittent failures

**Question:** GetAds fails about one in ten requests. What documented behavior should we check?

**Actual answer:** Suggested checking CPU, memory, latency and cache hits. Proposed cache misses as a possible cause, citing the Recommendation Service cache walkthrough.

**Expected answer:** Check whether `adFailure` is enabled. This flag is documented to make about 10% of GetAds calls fail. Documentation alone cannot confirm the actual incident cause.

**Why this is expected:** The official [Feature Flags table](https://opentelemetry.io/docs/demo/feature-flags/#implemented-feature-flags) explicitly matches the service, operation and failure rate in the question.

**Why it failed:** The target page was fetched, but the adFailure paragraph did not enter the top five retrieved excerpts. The model then applied another service's cache documentation to Ad Service without sufficient support. This is a retrieval-selection and answer-grounding failure.

[Actual output and evidence](<outputs/261103-Accuracy Test/D01.json>)

## D04 — Local build with JDK 17

**Question:** What Java requirement and build command should a developer check when Ad Service will not build with JDK 17?

**Actual answer:** Correctly stated minimum JDK 21 and recommended using the Gradle wrapper, but omitted the specific build command.

**Expected answer:** Use at least JDK 21 and run `./gradlew installDist` from the Ad Service directory.

**Why this is expected:** The service-specific [README's Building Locally section](https://github.com/open-telemetry/opentelemetry-demo/blob/main/src/ad/README.md) states both the minimum JDK requirement and the exact command. The question asks for both.

**Why it failed:** The cited excerpt covered lines 1–12, including the JDK requirement, but the command was on line 13. The command-containing excerpt did not enter the top five. The answer was partially correct but incomplete under the original scoring rule.

[Actual output and evidence](<outputs/261103-Accuracy Test/D04.json>)

Expected answers are examples, not exact wording requirements. Both failures were judged from the actual answers and evidence, not from keyword checks alone. All six cases reached the website page limit, so coverage was incomplete. No fixes were executed.
