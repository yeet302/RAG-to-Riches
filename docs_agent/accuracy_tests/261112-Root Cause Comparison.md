# Root Cause Comparison (2026-11-12)

The Docs Agent evaluation used D01–D04 and B01–B04 with the same Azure deployment across three variants. No Docs sent the question with a general debugging prompt. Top 5 and All Docs used the cited-answer prompt and shared one ranked retrieval per question. The review scored final answers from 0 to 2 against the known cause. An answer earned 2 points by identifying the cause and connecting it to the symptom.

## Results after targeted retries

| Variant | Correct | D cases | B cases | Partial credit | Wrong attributions |
| --- | ---: | ---: | ---: | ---: | ---: |
| No Docs | 0/8 (0%) | 0/4 | 0/4 | 0 | 8 |
| Top 5 | 5/8 (62.5%) | 3/4 | 2/4 | 0 | 2 |
| All Docs | 6/8 (75%) | 4/4 | 2/4 | 0 | 2 |

| Case | No Docs | Top 5 | All Docs |
| --- | ---: | ---: | ---: |
| D01 | 0 | 0 | 2 |
| D02 | 0 | 2 | 2 |
| D03 | 0 | 2 | 2 |
| D04 | 0 | 2 | 2 |
| B01 | 0 | 2 | 2 |
| B02 | 0 | 0 | 0 |
| B03 | 0 | 2 | 2 |
| B04 | 0 | 0 | 0 |

Top 5 beat No Docs on six cases and tied on B02 and B04. The D01 win came from avoiding a leading wrong attribution at the same root-cause score. All Docs beat Top 5 on D01 and tied on the other seven cases. B02 and B04 remained unresolved across all three variants. Both Docs variants proposed generic adFailure for fork-specific behavior, although the supplied evidence did not support that attribution.

On D01, adFailure first appeared at rank 8 in the saved retrieval. All Docs cited the documented one-in-ten error behavior. Top 5 offered general CPU, cache, and tracing hypotheses. On D04, both Docs variants identified the JDK mismatch. All Docs gave the exact build command. Both omitted the working directory, so both received partial credit for fix completeness.

## Decision

Choose **All Docs** for this tested suite under the same retry policy. It found one additional root cause and produced the same number of wrong attributions as Top 5. The difference comes from one case. The No Docs comparison also changes the prompt because the Docs prompt requires citations, so the result describes these configurations together.

The first pass produced two format failures for All Docs and one for Top 5. The three failed attempts passed on the first retry with the same excerpts and Azure deployment. With first-pass failures counted as zero, both Docs variants found five roots out of eight. Report this alongside the retry-adjusted result. The D01 ranking miss is a concrete regression case. This run contains one such win, so it does not establish a broader ranking problem.

Run ID: `261112-154532-6becccd8`. [Original responses](outputs/261112-154532-6becccd8/raw_results.json) · [Retries](outputs/261112-154532-6becccd8/261112-154911-retry_results.json) · [Complete responses](outputs/261112-154532-6becccd8/261112-154911-complete_results.json) · [Reviewed scores](outputs/261112-154532-6becccd8/reviewed_scores.json).

## Token and latency replay

Run `261112-161831-e6299cdd` replayed the same saved Top 5 and All Docs inputs on the same configured model. This replay made 16 Azure requests and skipped retrieval. The table averages eight attempts per variant and includes any usage and elapsed time from responses that later failed format validation. Elapsed time includes the model call and local response validation.

| Variant | Prompt tokens | Completion tokens | Total tokens | Mean elapsed | Median elapsed | Format failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| top5 | 965.6 | 122.0 | 1,087.6 | 2.03 s | 1.87 s | 0 |
| all-doc | 10,404.0 | 186.6 | 10,590.6 | 3.28 s | 2.41 s | 1 |

All Docs used 9.74 times as many total tokens and took 1.24 seconds longer on average. Its D01 response failed format validation in this replay. The accuracy scores above come from the separate reviewed run.

[Replay raw results](outputs/261112-161831-e6299cdd/raw_results.json).
