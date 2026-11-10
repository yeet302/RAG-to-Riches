# Confidence score: definition (v2)

User story: *As a developer, I want a confidence score with a clear definition, so that I know how
much to trust the answer.*

## What the number means

**How strongly what the sub-agents returned supports this answer**, from 0% to 95%.

- It is **not** the probability that the cause is right. That's why the UI band reads "High support".
- It is computed in Python (`confidence.py`) from this question's agent findings. The AI model never
  writes a confidence number; an extra `confidence` key makes its whole answer invalid.
- The same inputs always give the same score, and every score comes with its reasons ("Why N%?").

## What goes into it

| Part | Weight | The question it answers | How it is measured |
|---|---|---|---|
| **Logs-code agreement** | 30% | Do the logs and the code point to the same cause? | **1.0:** the code agent tied its code to specific log events (`Upstream:` refs, checked against the logs agent's real refs) **and** both share a specific signal. **0.7:** the link only. **Without a link:** 0.7 for 2+ shared signals, 0.5 for 1, 0.1 for none. **0:** one side returned no evidence. |
| **Docs relevance** | 10% | Do the docs actually talk about this problem? | The share of the question's (or the likely cause's) key terms that the best docs excerpt covers (70%), plus 30% if that excerpt names something the logs/code agents found (for example `adFailure`) |
| **Sub-agent context** | 20% | Did each agent search its whole source and find evidence? | For each agent that was asked: 0 if it failed, timed out or found nothing. Otherwise `0.6 + 0.4 × its own relevance rating`, × 0.8 if it reported a coverage gap (for example "website page limit reached"). Averaged over the agents. |
| **Measured accuracy** | 20% | How often has this agent been right in our tests? | `(correct + 1) / (total + 2)` from `confidence_calibration.json`, averaged over the agents the answer cites. An agent with no measurements counts as 50%. |
| **Grounding** | 20% | Is every claim tied to evidence? | 1.0 if the AI answer's citations were all validated, 0.6 for an evidence list only, 0.4 if the AI answer failed validation. × 0.85 if some evidence didn't fit in the AI's context. |

A **specific signal** is a technical identifier rather than an English word: a camelCase name
(`GetAds`, `adFailure`), a constant (`AD_FAILURE`), a status code (`UNAVAILABLE`), a tag
(`inject:ads-unavailable`) or a file name (`AdService.java`). Spellings are normalised, so
`AD_FAILURE` matches `adFailure`. **Words the user typed in the question never count**: if both
sides only repeat "GetAds UNAVAILABLE", they haven't agreed on anything.

## How the parts combine

1. **Only the parts that apply are used, then re-weighted to 100%.** Agreement applies when logs and
   code were both asked. Docs relevance applies when docs was asked. A docs-only how-to question is
   scored on docs relevance, sub-agent context, measured accuracy and grounding.
2. **Score** = the weighted average × 100, rounded.
3. **Rules applied after the average:**
   - **No evidence at all:** the score is 0%.
   - **AI planning fell back to keywords:** the score is multiplied by 0.95.
   - **Logs-code agreement below 50%:** at most 74%, so the answer can't be High. The parts disagree,
     so the score shouldn't look certain.
   - **Never above 95%.** Automated checks can't rule out every alternative.
4. **Bands:** High is 75% or more, Medium is 50–74%, Low is under 50%.

## Where the team's numbers go

`orchestrator/confidence_calibration.json` holds one entry per test run, in the form
`correct` out of `total`:

| Agent | Who | Current entries | Status |
|---|---|---|---|
| docs | Lantian | D01–D06 accuracy test: 4/6. Planted bugs, expected doc in top 5: 2/2 | **70%** |
| logs | Kalyan | (match rate on planted bugs) | Not measured yet, counts as 50% |
| code | Navya | (match rate on planted bugs) | Not measured yet, counts as 50% |

To add a result, append an entry like this to the agent's list and restart the orchestrator:

```json
{"name": "Planted bugs B-01..B-05 match rate", "metric": "match rate", "correct": 4, "total": 5,
 "measured_by": "Kalyan", "date": "2026-11-13", "source": "logs_agent/stress_eval_injected.py"}
```

The `+1 / +2` in the formula keeps a few test cases from swinging the score to 0% or 100%. For
example, 5/5 gives 86%, not 100%.

## Why these weights

- **Agreement is the largest part (30%).** Two independent sources pointing at the same cause is the
  strongest check we have: the log line and the code that produces it. The old score only counted
  how many agents returned something, so three unrelated findings scored as high as three that agree.
- **Measured accuracy and grounding (20% each)** bring in how the agents have actually performed, and
  whether the answer stayed inside its evidence.
- **Docs relevance is smaller (10%).** For our planted bugs the docs often have no matching page (B-02
  and B-04 in Lantian's test), so docs alone shouldn't move the score much.

Weights and thresholds live in `confidence_policy.json`. An invalid file is rejected (weights must
add up to 1).

## Showing the new score is better

`compare_confidence.py` rescores the same stored answers with the old (v1, `confidence_legacy.py`)
and new (v2) definitions:

```bash
cd orchestrator
python compare_confidence.py run --cases eval/planted_bugs.json   # app must be running
#  -> in the app: Accuracy results -> set Correct? for each B-0x / C-0x answer
python compare_confidence.py compare                              # writes results/CONFIDENCE_COMPARISON.md
```

The new score is **better** if, on the reviewed answers:
1. it ranks right answers above wrong ones at least as well as the old score;
2. it has a lower Brier error, meaning the score/100 is closer to 1 for right answers and 0 for wrong ones;
3. it gives no more High scores to wrong answers.

The command exits with status 0 only when all three hold.

The cases are the five planted bugs:

| Case | Flag | What it does |
|---|---|---|
| B-01 | `adFailure` | GetAds returns UNAVAILABLE |
| B-02 | `adTargetedFailure` | Targeted ad requests fail |
| B-03 | `adHighCpu` | High CPU |
| B-04 | `adEmptyResponse` | Empty AdResponse |
| B-05 | `adManualGc` | GC pauses |

There are also two controls where the right answer is "the evidence doesn't show this": C-01
(checkout failing) and C-02 (who deployed it).

## Old definition (v1), for reference

`0.30 × coverage (agents that returned evidence) + 0.30 × corroboration (number of agents cited) +
0.20 × the agents' own relevance ratings + 0.20 × grounding`, capped at 95.
