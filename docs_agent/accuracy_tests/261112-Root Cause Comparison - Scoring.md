# Root Cause Comparison Scoring Protocol (2026-11-12)

Freeze this protocol before generating answers. Evaluate D01–D04 and B01–B04 with No Docs, Top 5, and All Docs. Each version has eight planned cases. Score each variant’s final answer. No Docs uses the same Azure deployment with a question-only debugging prompt. Keep the known causes and scoring criteria out of the model input.

## Scoring each answer

| Score | Criteria |
| --- | --- |
| 2 | Identifies the known root cause as the leading diagnosis and connects it to the observed symptom. Equivalent wording and a qualified diagnosis are acceptable. The explanation can draw on any retrieved source or combination of sources. |
| 1 | Gives a case-specific clue or a concrete check that narrows the investigation, but falls short of identifying and explaining the known cause. An unranked list containing the right cause qualifies only if it also offers a specific reason or check that narrows the investigation. |
| 0 | Repeats symptoms, offers generic advice, only states that evidence is insufficient, or makes an incorrect leading diagnosis. A run that ends without a usable final answer also scores 0 and is flagged as a run failure. |

Mark **Wrong attribution = Yes** when the answer presents an incorrect cause as the leading or confirmed diagnosis. Mark it **No** for a cautious answer that leaves the cause open and for run failures. Record run failures separately from answer errors. An incomplete comparison with missing planned records is pending. Once all attempts are recorded, the denominator includes failures.

Record which evidence the answer actually used. Document retrieval and keyword matches are diagnostic observations and carry no points. Record fix completeness separately where the question explicitly requests a fix or build step. Use Yes, Partial, No, or N/A. Fix completeness does not change the root-cause score.

## Case-specific criteria for 2 points

| Case | Required diagnosis and symptom connection |
| --- | --- |
| D01 | The `adFailure` feature flag can cause intermittent GetAds errors and accounts for the roughly one-in-ten rate described in this scenario. |
| D02 | The `adHighCpu` feature flag triggers extra CPU work in Ad Service, explaining the high CPU load. |
| D03 | The `adManualGc` feature flag triggers full manual garbage collection, explaining the repeated collections in the scenario. |
| D04 | Ad Service requires JDK 21 or higher, so building with JDK 17 fails the version requirement. Score the build instructions separately. A complete answer also gives `./gradlew installDist` from the Ad Service directory. |
| B01 | The fork's `adFailure` injection path makes GetAds return `UNAVAILABLE`. No trigger rate is required for this case. |
| B02 | The fork's `adTargetedFailure` injection path makes targeted GetAds requests return `UNAVAILABLE`. |
| B03 | The `adHighCpu` flag triggers CPU-generating work in Ad Service, explaining the high CPU load. Naming `CPULoad.execute()` is one way to explain the mechanism. |
| B04 | The fork's `adEmptyResponse` injection path lets GetAds succeed with an empty `AdResponse`. |

## Review and comparison

1. Present the three final answers for each case in randomized order with version names and retrieval counts hidden. The reviewer scores them against this protocol and records a short reason and any evidence cited in the answer. Reveal the variants after scoring.
2. Use code to validate one reviewed record per run ID, case ID, and version. Report root-cause accuracy as the number of 2-point answers divided by eight. Also report the D and B subsets, each out of four, plus counts of 1-point answers, wrong attributions, and run failures.
3. Record per-case wins, losses, and ties for No Docs vs. Top 5 and Top 5 vs. All Docs. A higher root-cause score wins. At equal scores, an answer without wrong attribution wins over one with wrong attribution. Otherwise mark a tie and record any fix-completeness difference separately.
4. For the choice between Top 5 and All Docs, compare root-cause accuracy first, then fewer wrong attributions, then more 1-point answers. If all three measures tie, choose Top 5 because it uses less input.

Retain the case inputs, raw responses, and reviewed scores by run ID. Record both comparisons and the final choice in the local analysis report.
