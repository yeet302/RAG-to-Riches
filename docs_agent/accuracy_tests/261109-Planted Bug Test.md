# Planted Bug Retrieval Baseline

2026-11-09 · Desktop checkout at `a9a6b1c` · Live document retrieval

The agent ran the four original queries with the existing ranking. The tables
show the five excerpts returned for each query in order. One source can appear
at multiple ranks. Website line numbers refer to extracted text, and GitHub line
numbers refer to the source file. All four queries reached the website crawl
limit. The run measured retrieval only. [Raw run output](<outputs/261109-Planted Bug Test/report.json>)

## B-01 — GetAds UNAVAILABLE

> Why are GetAds requests failing with UNAVAILABLE errors in the Ad Service?

**Expected document or related reference.** The Feature Flags page documents how `adFailure` triggers errors in `GetAds`. [Expected passage](https://opentelemetry.io/docs/demo/feature-flags/#implemented-feature-flags)

**Top 5 result.** Rank 1 contains the expected `adFailure` passage.

| Rank | Source and excerpt | Lines |
| ---: | --- | --- |
| 1 | [Feature Flags · adFailure and adHighCpu](https://opentelemetry.io/docs/demo/feature-flags/#implemented-feature-flags) | 26-37 |
| 2 | [Feature Flags · Payment and Product Catalog](https://opentelemetry.io/docs/demo/feature-flags/#implemented-feature-flags) | 82-93 |
| 3 | [Ad Service · Custom metrics](https://opentelemetry.io/docs/demo/services/ad/#custom-metrics) | 355-366 |
| 4 | [Ad Service · Initializing metrics](https://opentelemetry.io/docs/demo/services/ad/#initializing-metrics) | 217-228 |
| 5 | [Ad Service · Initializing metrics](https://opentelemetry.io/docs/demo/services/ad/#initializing-metrics) | 225-232 |

## B-02 — Targeted ad requests fail

> Why are targeted ad requests failing in the Ad Service, especially requests for binoculars?

**Expected document or related reference.** The Ad Service metrics page describes targeted requests and lists `binoculars` as an ad category. Within the scope of this review, no document was found that directly describes the team's `adTargetedFailure` behavior. [Related documentation](https://opentelemetry.io/docs/demo/services/ad/#custom-metrics) · [Team implementation](https://github.com/yeet302/RAG-to-Riches/blob/4a701124503f86431f52a000c01d2d73322d39e0/src/ad/src/main/java/oteldemo/AdService.java#L263-L272)

**Top 5 result.** Rank 1 contains the related Ad Service metrics passage, and rank 4 contains another metrics passage from the Ad Service README. Exact retrieval correctness is unscored because the review found no directly matching reference.

| Rank | Source and excerpt | Lines |
| ---: | --- | --- |
| 1 | [Ad Service · Custom metrics](https://opentelemetry.io/docs/demo/services/ad/#custom-metrics) | 355-366 |
| 2 | [Ad Service · Initializing metrics](https://opentelemetry.io/docs/demo/services/ad/#initializing-metrics) | 217-228 |
| 3 | [Ad Service · Initializing metrics](https://opentelemetry.io/docs/demo/services/ad/#initializing-metrics) | 225-232 |
| 4 | [Ad Service README · Custom metrics](https://github.com/open-telemetry/opentelemetry-demo/blob/main/src/ad/README.md#L41-L52) | L41-L52 |
| 5 | [Demo · Language feature reference](https://opentelemetry.io/docs/demo/#language-feature-reference) | 27-38 |

## B-03 — High CPU usage

> What could cause unusually high CPU usage in the Ad Service?

**Expected document or related reference.** The Feature Flags page documents how `adHighCpu` triggers high CPU load in the Ad Service. [Expected passage](https://opentelemetry.io/docs/demo/feature-flags/#implemented-feature-flags)

**Top 5 result.** Ranks 1 and 2 both contain the expected `adHighCpu` passage.

| Rank | Source and excerpt | Lines |
| ---: | --- | --- |
| 1 | [Feature Flags · adHighCpu and adFailure](https://opentelemetry.io/docs/demo/feature-flags/#implemented-feature-flags) | 26-37 |
| 2 | [Feature Flags · adHighCpu and adManualGc](https://opentelemetry.io/docs/demo/feature-flags/#implemented-feature-flags) | 34-45 |
| 3 | [Recommendation cache · Memory leak walkthrough](https://opentelemetry.io/docs/demo/feature-flags/recommendation-cache/) | 1-5 |
| 4 | [Demo · Language feature reference](https://opentelemetry.io/docs/demo/#language-feature-reference) | 27-38 |
| 5 | [Demo · Language feature reference](https://opentelemetry.io/docs/demo/#language-feature-reference) | 35-46 |

## B-04 — Empty AdResponse

> Why is the Ad Service returning an empty AdResponse instead of advertisements?

**Expected document or related reference.** The Ad Service README describes normal ad selection and the random-ad fallback. Within the scope of this review, no document was found that directly describes the team's `adEmptyResponse` behavior. [Related documentation](https://github.com/open-telemetry/opentelemetry-demo/blob/main/src/ad/README.md#L1-L5) · [Team implementation](https://github.com/yeet302/RAG-to-Riches/blob/4a701124503f86431f52a000c01d2d73322d39e0/src/ad/src/main/java/oteldemo/AdService.java#L284-L293)

**Top 5 result.** The related README was outside the Top 5. Rank 4 was the service documentation index. Exact retrieval correctness is unscored because the review found no directly matching reference.

| Rank | Source and excerpt | Lines |
| ---: | --- | --- |
| 1 | [Feature Flags · adHighCpu and adManualGc](https://opentelemetry.io/docs/demo/feature-flags/#implemented-feature-flags) | 34-45 |
| 2 | [Demo · Language feature reference](https://opentelemetry.io/docs/demo/#language-feature-reference) | 27-38 |
| 3 | [Demo · Language feature reference](https://opentelemetry.io/docs/demo/#language-feature-reference) | 35-46 |
| 4 | [Demo · Service documentation index](https://opentelemetry.io/docs/demo/#service-documentation) | 64-75 |
| 5 | [Demo · Architecture](https://opentelemetry.io/docs/demo/architecture/) | 1-12 |

Both cases with a directly matching reference had a Top 5 hit (2/2). The
two cases with related references are recorded separately.
