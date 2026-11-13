# Docs Agent Accuracy Tests

The [historical case file](<261103-Accuracy Test - Cases.json>) contains six fixed cases that test whether the docs agent finds and uses the right online documentation: four questions with expected supporting documents and two questions the documentation cannot establish an answer to.

## Run the November 3 Evaluation

Run from the repository root to fetch online official/GitHub documentation and generate answers with the real Azure model. This historical mode requires configured Azure credentials:

```bash
.venv/bin/python -m docs_agent.accuracy_tests.run_accuracy --legacy --live --corpus live
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

## Online Answer Evaluation (2026-11-03)

Six cases were evaluated using online documentation and gpt-4.1-mini. Four passed (**66.7%**); D01 and D04 failed. Average response time was **4.03 seconds**. [Test analysis](<261103-Accuracy Test.md>) · [Raw run output](<outputs/261103-Accuracy Test/report.json>)

## B-01 to B-04 Retrieval Baseline (2026-11-09)

The original ranking returned five excerpts per query. B-01 (GetAds UNAVAILABLE)
and B-03 (high CPU usage) each had a direct match at rank 1.

Expected documents for existing bugs are candidate references found through
manual or LLM-assisted search. Either method may miss a more relevant source.
Within the scope of this review, no directly matching document was found for
B-02 or B-04, so their exact retrieval correctness is unscored.

[Test analysis and Top 5 rankings](<261109-Planted Bug Test.md>) · [Raw run output](<outputs/261109-Planted Bug Test/report.json>)

## Root Cause Comparison (2026-11-12)

[Case file](<261112-Root Cause Comparison - Cases.json>) and
[scoring protocol](<261112-Root Cause Comparison - Scoring.md>) for D01–D04 and
B01–B04 across No Docs, Top 5, and All Docs. The D cases reuse the November 3
questions. The B cases use symptoms and log markers from the planted bugs in the
[shared Results sheet](https://docs.google.com/spreadsheets/d/1xh9ISOvCvEJxssYZx1dvHBlHR4EbAQ07Mi0Bzk18rVc/edit#gid=584638028).
Send only the question to the Docs Agent. Use the scoring protocol for review.
[Analysis and results](<261112-Root Cause Comparison.md>).

### Run a Docs Agent comparison

From the repository root:

```bash
.venv/bin/python -m docs_agent.accuracy_tests.run_accuracy \
  --cases "261112-Root Cause Comparison - Cases.json" \
  --versions nodoc top5 all-doc
```

Select any subset of the three versions. Omit `--versions` to run all three.
`--retrieval-only` defaults to Top 5 and All Docs because No Docs requires Azure.
Use `--case B02` to run one case. Future case files need only an `id` and a
`question` per row. Pass a filename in this directory or a path.

The runner calls the Docs Agent directly using its Azure configuration. It
retrieves and ranks documentation once per question. Top 5 passes the first
five matching excerpts, and All Docs passes every matching excerpt from the
same retrieval. No Docs sends only the original question and a general debugging
instruction to the same Azure deployment. Its prompt allows a diagnosis without
citations. No Docs alone skips document retrieval. All versions run locally
without the Orchestrator.

Raw results are written to
`outputs/<YYMMDD-HHMMSS>-<unique ID>/raw_results.json` .
Each record includes the case, variant, Docs input, final response, and any
error. The runner does not score answers.

Azure token usage is saved in azure_usage.
