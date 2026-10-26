# Numeric answers vs charts — hosted before / after (2026-10-25)

**Before** = production `fs-agent-knowledge:240` (main `506ec85`). **After** =
`:241` (main `6210d76`, PR 69). Collections: the resume sample `7c37eb7d` and
the invoice sample `87396d15`. Expected numbers are computed from each
collection's **extracted** metadata. The numbers the answer text states are
read by an LLM (structured output); the chart's numbers come from
`data.knowledge.chart_output.chart_data`; the comparison is exact equality
(`evals/metadata_qa/numeric.py`). Two runs per question, fresh sessions.

## Answer text vs data vs chart

| Q | Kind | Question | Before (:240) | After (:241) |
|---|---|---|---|---|
| R1 | grouped count | What is the distribution of years of experience across candidates? Show a chart. | **text wrong on 2/2**: run 1 said 5 years → 1, 7 years → 3; run 2 said 5 years → 1. Data and chart: 2 and 4. Text ≠ chart | 2/2 text = data = chart (5 → 2, 7 → 4, total 24) |
| R2 | grouped count | How many candidates are there at each education level? Show a chart. | 2/2 correct (bachelors 15, masters 8, phd 1) | 2/2 text = data = chart |
| R3 | filtered grouped count | Among candidates with at least 5 years, how many at each seniority level? | 2/2 correct (senior 8, lead 3, principal 2, executive 1, mid 1, junior 1) | 2/2 text = data = chart |
| R4 | count | How many candidates list Python as a skill? | 2/2 = 18 | 2/2 = 18 (no chart: single number) |
| I1 | grouped count | How many invoices does each vendor have? Show a chart. | 2/2 (6 vendors × 3) | 2/2 text = data = chart |
| I2 | filtered grouped sum | Total billed by each US-dollar vendor? Show it as a chart. | 2/2 (Lumen Ridge 35106, Quillmark 27950, Copperleaf 7225.7) | 2/2 text = data = chart |
| I3 | count | How many invoices are billed in euros? | 2/2 = 6 | 2/2 = 6 |

Before, the answer text was produced before the chart had computed anything,
so R1's text was the model's own count. After, the chart's aggregation runs
once before the answer, the answer is given its table as authoritative
figures, and the chart plots the same rows.

## di-charts regression set on :241 (`chart_regression.py`)

| Question | Chart vs expected | Answer draws a chart? |
|---|---|---|
| Seniority distribution | exact (senior 8, mid 6, junior 4, lead 3, principal 2, executive 1) | no |
| Years distribution | numbers exact (= extracted data, total 24); this run grouped by exact year rather than the 8 buckets di-charts recorded (the grouping is the spec LLM's choice) | no |
| Education level | exact (15 / 8 / 1) | no |
| Skills python/aws/sql/docker/rag | exact (18 / 11 / 13 / 5 / 6) | no |
| Invoices: total per USD vendor | exact (35106 / 27950 / 7225.7) | no |

## Set question: "Which candidates are based in or open to Bengaluru?" (`repeat_names.py`)

| | Exact runs | How it was retrieved |
|---|---|---|
| Before (:240) | recorded 5/6 (one run listed 3 of 5); this session 12/12 | `lookup`; 3 of the last 8 runs started with an unfiltered semantic top-20 (the plan behind the 3-of-5 miss) |
| After (:241) | **8/8** (Aarav Mehta, Anjali Nair, Ishaan Chatterjee, Kavya Reddy, Vikram Singh) | every run `predicate` with a `locations` filter, `population_complete=true` |

## Extraction differences (not answer/chart consistency; extraction unchanged)

| Resume | Ground truth | Extracted | Verdict |
|---|---|---|---|
| r03 Daniel Okafor | 16 | 14 | **Extraction error.** Continuous history from 2010 to present; nothing ambiguous. |
| r08 Fatima Sheikh | 10 (alternative 3) | 7 | **Extraction error on an ambiguous profile.** The resume says "seven years of teaching … three years in analytics". Total = 10, tech only = 3, and 7 matches neither. |
| r15 Kavya Reddy | 3 | 2 | **Ambiguous source.** The PDF gives years only ("2023 - 2025", "2025 - Present"), so experience in Oct 2026 is anywhere from under 3 to 3. |

## Tests
- `tests/unit/test_numeric_answer_consistency.py` (5 tests): the answer's figures, the data and the chart CSV are equal, and the numbers are decided once. All 5 fail on pre-fix main ("the answer was given no computed figures").
- `tests/unit/test_set_question_completeness.py` (3 tests): replays the 3-of-5 run through the verifier. 2 fail on pre-fix main; the third is a control that passes on both.
