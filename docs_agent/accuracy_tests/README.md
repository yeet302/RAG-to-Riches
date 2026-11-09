# Docs Agent Accuracy Tests (2026-11-09)

Six fixed cases test whether the docs agent finds and uses the right online documentation: four questions with expected supporting documents and two questions the documentation cannot establish an answer to.

## Run

Run from the repository root to fetch online official/GitHub documentation and generate answers with the real Azure model; this is also the default mode and requires configured Azure credentials:

```bash
.venv/bin/python -m docs_agent.accuracy_tests.run_accuracy --live --corpus live
```

## Output

Each run creates `docs_agent/accuracy_tests/outputs/<UTC timestamp>-<unique ID>/`; `mode=live_azure` and `corpus=live` identify the online evaluation in both the report and new CSV exports.

- `accuracy_results.csv`: All answers, timing, expected/returned sources, and reviewed scores for transfer to the shared sheet.
- `D01.json`–`D06.json`: Each question, retrieved online excerpts, final answer/citations, and review details when scored.
- `report.json`: Run settings, all case results, summary metrics, and review provenance when scored.

## Scoring

Automatic checks match the expected source and supporting keywords in the top five excerpts and in the final citations. A match alone does not prove the answer correct.

Review the answer, hypotheses and cited text against each case's `review_rule`. Mark `correct?` Yes or No. Missing a required part of the answer is No. Unsupported cases pass when the agent abstains or clearly states its evidence limits without inventing facts.

Accuracy = Yes / all attempted cases, including errors. New runs start as REVIEW, except agent/source errors start as No. Keep review reasons and reviewer identity. Record every result in the shared accuracy sheet. `fix_worked?` remains Not tested or N/A unless a fix was actually executed and verified.

## Current Results

Six cases were evaluated using online documentation and gpt-4.1-mini. Four passed (**66.7%**); D01 and D04 failed. Average response time was **4.03 seconds**. [Test analysis](<261103-Accuracy Test.md>) · [Raw run output](<outputs/261103-Accuracy Test/report.json>)

## B-01 to B-04 Retrieval Baseline (2026-11-09)

The original ranking returned five excerpts per query. B-01 (GetAds UNAVAILABLE)
and B-03 (high CPU usage) each had a direct match at rank 1.

Expected documents for existing bugs are candidate references found through
manual or LLM-assisted search. Either method may miss a more relevant source.
Within the scope of this review, no directly matching document was found for
B-02 or B-04, so their exact retrieval correctness is unscored.

[Test analysis and Top 5 rankings](<261109-Planted Bug Test.md>) · [Raw run output](<outputs/261109-Planted Bug Test/report.json>)
