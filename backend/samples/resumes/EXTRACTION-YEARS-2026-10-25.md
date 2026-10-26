# Years-of-experience extraction: three misses, root causes and fix (2026-10-25)

On the live resume sample (knowledge rev 241, worker rev 138), three resumes had a `years_of_experience`
that differed from `ground_truth.json`:

| Resume | Ground truth | Extracted |
|---|---|---|
| r03 Daniel Okafor | 16 | 14 |
| r08 Fatima Sheikh | 10 | 7 |
| r15 Kavya Reddy | 3 | 2 |

## What each one was

All three ground-truth values are **correct**: each follows the template's rule and the PDF's own dates
(`tests/unit/test_resume_years_definition.py` recomputes all 24 from the dated work history). The fixture is
unchanged. The extraction was wrong, for three reasons:

1. **No current date in the extraction prompt (date calculation).** The enrichment prompt never said what
   "now" is, so "Present" was measured to whatever year the model assumed. r03's PDF says
   `2010 - Present` with no stated total: 14 is 2024 − 2010. r15 says `2023 - Present`: 2 is 2025 − 2023.
   With today's date in the prompt, r03 came out 16 in 3/3 runs.
2. **Arithmetic with reasoning switched off (model extraction).** All 24 live extractions ran on the
   fallback model, gpt-5.4-nano, because the configured model (gemini-3.1-flash-lite) has failed every
   call since 2026-10-23 with `402 RESOURCE_EXHAUSTED` (prepaid credits depleted). The fallback's registry
   entry had `reasoning_effort: "none"`, so it computes a duration in one pass while filling 15 fields.
   In r15's compact layout the dates are read after the whole page (`2025 - Present`, `2023 - 2025` at
   the end of the text); the model still paired them correctly in `work_experience` in every run, then
   counted only the current role (1 year) once it knew the date.
3. **A partial figure taken as the total (ambiguous resume wording + an underspecified definition).**
   r08's profile says "Seven years of teaching, followed by an intensive data science bootcamp and three
   years in analytics and data science roles". The definition said "compute from the employment history
   when not stated explicitly", and the model took "seven years" as the stated value (7, also 6 and 0 in
   other runs). Ground truth is 10 (teaching 2015–2022 plus data roles 2023–present); 3 (tech only) stays
   an accepted alternative.

Overlapping employment was not a cause: r22's overlapping roles were extracted correctly in every run.

## The fix

| Change | Where |
|---|---|
| Every enrichment prompt gets a **Reference date** section (today, UTC): open ends such as "Present" end on that date | `workers/celery_utils/enrichment/prompts.py` (`reference_date_section`, all strategies) |
| `years_of_experience` definition states the rule the ground truth uses: earliest non-internship job to the latest end, open end = reference date, breaks subtracted, a partial figure is not the total | `backend/utils/config/enrichment_config.py`, `resume` template |
| Fallback model gpt-5.4-nano reasons before answering: `reasoning_effort` `"none"` → `"medium"` | `workers/celery_utils/enrichment/llm_factory.py` |

Nothing is computed or overwritten after extraction: the model still produces the value, from the same
document text.

## Measured locally (`extract_local.py`, all 24 resumes × 3 runs, gpt-5.4-nano, the worker's own code)

| Variant | years correct | mean output tokens |
|---|---|---|
| Baseline (production code) | 63/72 | — |
| + reference date | 67/72 | — |
| + reference date, per-role-sum definition (rejected) | 61/72 | — |
| + reference date, span definition, effort none | 66/72, 68/72 (two runs) | 381 |
| same, effort low | 70/72 | 577 |
| same, effort **medium** | **72/72** | 813 |
| same, effort high | 72/72 | 960 |
| reference date, **old** definition, effort medium | 71/72 (r08 → 11) | 769 |
| **Final code** (registry default, no overrides) | **72/72** | 820 |

Each change is needed: without the date, "Present" drifts; without reasoning, the arithmetic slips; with
the old definition, r08 still miscounts. Medium adds about 370 reasoning tokens per document (≈ $0.0005
at nano output prices).

**Regression check, every field, with `score.py`'s rubric (`score_local.py`, two judge passes, a value
counts only when both say correct):**

| Field | Baseline | Final |
|---|---|---|
| `candidate_email` | 72/72 | 72/72 |
| `seniority_level` | 68/72 | 71/72 |
| `education_level` | 72/72 | 72/72 |
| `years_of_experience` | 63/72 | **72/72** |
| `candidate_name` | 72/72 | 72/72 |
| `current_role` | 70/72 | 70/72 |
| `skills` | 70/72 | 72/72 |
| `certifications` | 72/72 | 72/72 |
| `locations` | 70/72 | 70/72 |
| `industries` | 44/72 | 58/72 |
| `languages` | 72/72 | 72/72 |
| `work_experience` | 71/72 | 70/72 |
| `education` | 71/72 | 72/72 |
| **All** | **887/936** | **915/936** |

The one `work_experience` difference is r03 run 3 pairing the middle role with the wrong years (the hosted
baseline extraction had the same slip); its `years_of_experience` was still 16.

Raw runs: `evals/resume_extraction/2026-10-25/` (every extraction, the exact system prompt, token usage,
and the judged scores).

## Regression tests

- `tests/worker/test_enrichment_reference_date.py`: every prompt path carries the reference date; the
  default is today UTC; it survives collection instructions and consistency values; the fallback model
  reasons at "medium". 2 failures + 4 errors on `origin/main`, green on the fix.
- `tests/unit/test_resume_years_definition.py`: all 24 ground-truth values equal the definition's rule
  applied to their own work history; r03 = 16, r15 = 3, r08 = 10 pinned; the definition states each rule.
  Fails on `origin/main` (the old definition states none of them).

## Hosted verification (knowledge:242, workers:139)

Released from merge commit `1f6dad3` ([PR 72](https://github.com/FutureSmart-AI/fs-agent-knowledge/pull/72)): API image
`production_knowledge:years-1f6dad3` (CodeBuild), worker image `production_celery:di-years-1f6dad3`; both revisions
registered from the running ones (241, 138) with only the image changed. The sample was rebuilt with the supported
workflow, `python -m scripts.provision_sample samples/resumes/sample.json`: the new collection
`c4d3189f-8f93-4fa9-8f75-07ad08bd71c8` is the `resumes` sample; the previous one (`7c37eb7d-…`) was kept, not deleted.
All 24 documents completed; every enrichment event ran gpt-5.4-nano (Gemini still 402) with 820 mean output
tokens, the medium-reasoning figure measured locally. The stored `years_of_experience` definition is the new one.

**Final field-level accuracy against the complete ground truth** (`live_sample/extracted-242.json`,
`live_sample/scores-242.json`; exact fields exact with alternatives, the rest judged twice, correct only when both
passes agree):

| Field | Before (first hosted run, VERIFICATION.md) | Now (live sample on 242/139) |
|---|---|---|
| `candidate_email` | 24/24 | 24/24 |
| `seniority_level` | 23/24 | 24/24 |
| `education_level` | 24/24 | 24/24 |
| `years_of_experience` | 22/24 | **24/24** |
| `candidate_name` | 24/24 | 24/24 |
| `current_role` | 23/24 | 24/24 |
| `skills` | 24/24 | 24/24 |
| `certifications` | 23/24 | 24/24 |
| `locations` | 24/24 | 23/24 |
| `industries` | 16/24 | 20/24 |
| `languages` | 24/24 | 24/24 |
| `work_experience` | 22/24 | 23/24 |
| `education` | 24/24 | 24/24 |
| **All 312 values** | **297/312 (95.2%)** | **306/312 (98.1%)** |

The six remaining misses: four `industries` lists with one extra, true label (r06 'it services', r10 'developer
tools', r15 'digital health', r22 'analytics'), r19 `locations` adding Amman, and r08 `work_experience` where the two
judge passes split (correct/partial) on company strings with extra context. None is a years value.

**Question answering and charts on the new sample:**

| Check | Result |
|---|---|
| Numeric consistency (`evals/metadata_qa/numeric.py`, R1–R4 resumes, I1–I3 invoices, 2 runs) | 14/14: text = data = chart (`live_sample/numeric-242.json`) |
| Chart regression (`evals/metadata_qa/chart_regression.py`, 5 charts) | 5/5 equal the extracted data. Years, education and skills charts also equal ground truth exactly; seniority differs from the primary ground truth only by the fixture's accepted alternatives (r10 intern, r22 principal) (`live_sample/charts-242.json`) |
| q01 Python + AWS + 5 years, q07 Bengaluru, q08 master's/PhD, q12 Kubernetes with years, Kubernetes and Terraform | correct, both judge passes, 2/2 runs each |
| "Three most experienced" (left out of the suggestions because it printed Daniel Okafor as 14) | correct 2/2: Michael Brennan 18, Daniel Okafor 16, Rohan Deshpande 15 |
| q05 years distribution | the judge says partial because the answer picks its own buckets; recomputed from ground truth on the answer's buckets, both runs are exact, and a chart is returned (`live_sample/answers-242-*.json`) |

The two check scripts now resolve the live samples by key (`evals/metadata_qa/samples.py`) instead of fixed ids,
and compare a numeric field on whatever groups the chart drew.

**Invoices (the fallback-reasoning change applies to every collection):** 18 invoices × 2 runs locally, old code vs
new (`evals/resume_extraction/2026-10-25/invoices_*.json`): `invoice_number`, `total_amount`, `currency`, `issue_date`,
`due_date` are 36/36 in both. `payment_terms` keeps the same meaning but is capitalised ("Net 30" for "net 30") more
often: 24/36 runs against 13/36 before; the definition gives lowercase examples without requiring lowercase. The live
invoice sample was not re-extracted and is unchanged.

## Reproduce

```
python extract_local.py --workers-root ../../../workers --env-file <KEY=json lines with OPENAI_API_KEY> \
    --admin-prompt-file <active enrichment_without_chunks prompt> --fields-jsonl <collection field configs> \
    --template-descriptions --runs 3 --out run.json
OPENAI_API_KEY=... python score_local.py run.json --out scored.json
```

## Not changed, and why

- **The configured model.** Gemini's prepaid credits are a billing action for the account owner; until
  they are topped up, every collection extracts on the fallback.
- **The create-collection preview** (`backend/utils/enrichment/preview_run.py`) uses its own prompt, which
  says "never infer, calculate or invent a value", so a computed field like this one previews as null.
  That is a separate surface from ingest extraction.
