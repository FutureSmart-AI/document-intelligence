# Model evaluation

Compares model configurations on extraction accuracy, answer accuracy, latency and cost. It uses
the product's own HTTP API in the standalone stack (`compose.yaml`) with real models: collections
are created with the full field template, PDFs are uploaded, the worker extracts them, and chat
answers the questions. This is not a separate or simplified pipeline.

## Suites

**extraction**: 24 synthetic resumes and 18 synthetic invoices (`backend/samples/{resumes,invoices}`),
13 and 10 fields each, so 312 + 180 field values per repeat.

| Measure | How |
| --- | --- |
| Typed-field correctness | Exact fields (email, enums, integer years, invoice number, currency, amount, dates) are compared mechanically and honour the fixture's `alternatives`. Fields whose correctness depends on meaning (names, roles, skill and location lists, work history, line items, payment terms) are judged by `gpt-5.6-terra` (reasoning medium) with structured output in two passes; gpt-5.4-mini was tried first and returned "wrong" for fields whose own reason said every expected item matched. A value counts only when both passes say correct. Resumes use `backend/samples/resumes/score.py`'s rubric; invoices use `INVOICE_RUBRIC` in `grade.py`. |
| Missing values | Per field: `missing` (truth has a value, extraction has none), `unexpected` (the reverse), or `wrong`. |
| Numeric/date exactness | `years_of_experience`, `total_amount`, `issue_date`, `due_date`. |
| Document completeness | Every field with a ground-truth value is filled. |
| Served model | Contract 2 `processing_telemetry` when the API returns it. Otherwise the document's enrichment-health attempts. Any fallback, or a model other than the config's, fails the run. |

**rag**: 39 cases on the config's own collections, and every expected answer is computed from that
collection's extracted metadata (`cases.py`, the method from `evals/metadata_qa/questions.py`). When the
ground truth would give a different answer, the case is flagged `truth_differs`. That difference is an
extraction error, not a QA failure. Categories: exact, multi-field and negation filters; numeric ranges;
complete counts and aggregates; ranking and comparison; passages (the expected source document must
be returned); multi-document synthesis; hybrid; invoice questions (`backend/samples/invoices/questions.json`);
charts (the chart's rows must equal the expected aggregation on the chart's own groups); and two
multi-turn follow-ups. One of them is the known regression: turn 1 asks "Which candidates know Python
and AWS and have 5+ years of experience?" (8 people on the truth data) and turn 2 in the same session
asks "chart their years". The chart must cover exactly turn 1's population. Answers are judged twice
with `evals/metadata_qa/grade.py`'s rubric, and both passes must pass.

Latency and cost per answer come from the API's `usage` (Contract 1: `latency_ms`, `estimated_cost_usd`,
`cost_complete`, `operations`) when present. Otherwise they come from the turn's own LangSmith trace
in project `document-intelligence-model-eval-server` (LLM runs: model, tokens), priced with the dated
fallback table in `usage.py`, and latency is the client's wall time. Each record says which `source`
it used. The served models must belong to the config, otherwise the run fails.

## Data and identity

`dataset.json` (name `document-intelligence-model-eval`, version `v1`). Its SHA-256 covers the spec,
ground truths, question files, the case builder and graders, and every PDF. Text files are CRLF-normalised.
`run.py prepare` checks that every ground-truth record has its PDF, that every case id is built, and
that the invoice expectations recomputed from ground truth equal the committed `questions.json`.

## Configurations

`configs/<name>.json` = `{"profile": "<deploy/model-profiles.json profile>", "settings": {env overrides}, "suites": [...], "about": "..."}`.
The profile's settings, with the config's settings on top, reach api and worker as environment variables.
There is no new configuration system.

| Config | Extraction | Retrieval agent / answer |
| --- | --- | --- |
| `baseline-nano-medium` | gpt-5.4-nano, reasoning medium (what production serves on 2026-10-26) | gpt-5.6-luna / gpt-5.6-luna |
| `extract-nano-low` | gpt-5.4-nano, reasoning low | as baseline |
| `extract-luna-none` | gpt-5.6-luna, reasoning none | as baseline |
| `extract-luna-low` | gpt-5.6-luna, reasoning low | as baseline |
| `chat-nano` | as baseline | gpt-5.4-nano / gpt-5.4-nano |
| `chat-mini` | as baseline | gpt-5.4-mini / gpt-5.4-mini |
| `profile-luna-low` | gpt-5.6-luna, reasoning low; both suites, so RAG runs on luna-extracted collections (the regression gate for the selected profile) | as baseline |

Every config pins refine nano, verifier nano, follow-up nano, chart extraction gpt-5.4-mini and RAG
fallback gpt-5.4-mini (production's values). The worker's `ENRICHMENT_REASONING_EFFORT` override
(`workers/celery_utils/enrichment/llm_factory.py`) exists for this evaluation. Production leaves it unset.
Only gpt-5.4-nano/mini, gpt-5.6-luna/terra and text-embedding-3-small are used. Provider overrides
(e.g. OpenRouter) are possible through settings, but no OpenRouter key exists and none is run.

## Running

On demand only; nothing runs on a schedule. Either start it in GitHub Actions (**Actions → Model
evaluation → Run workflow**, with inputs `configs`, `repeats` and `suites`), or run `run.py` locally
as shown below. In Actions, each config runs in its own job as a clean single-key install
(`deploy/configure.py` with one `OPENAI_API_KEY`). A final job compares all configs in the step
summary. Secrets: `MODEL_EVAL_OPENAI_API_KEY`, `MODEL_EVAL_LANGSMITH_API_KEY`. Artifacts:
`extraction-<config>.jsonl` (every field of every document, with telemetry), `rag-<config>.jsonl`
(every answer, verdicts, chart comparison, usage, server trace ids), `run-<suite>-<config>.json`
(hashes, git SHA, LangSmith experiment), `rag-cases-<config>.json`, and `summary.{json,md}`.

Locally, with Docker and `OPENAI_API_KEY` / `LANGSMITH_API_KEY` in the environment:

```sh
python deploy/configure.py                                   # once
C="docker compose -f compose.yaml -f evals/model_eval/compose.yaml"
python evals/model_eval/run.py env --config baseline-nano-medium
$C build api worker && $C up -d --wait --scale worker=4 api worker
$C --profile eval run --rm --no-deps model-eval prepare
$C --profile eval run --rm --no-deps model-eval extract --config baseline-nano-medium --repeats 2
$C --profile eval run --rm --no-deps model-eval ask --config baseline-nano-medium --repeats 2
python evals/model_eval/run.py env --config chat-nano        # next config: rewrite env, recreate api + worker
$C up -d --force-recreate --no-deps --wait --scale worker=4 api worker
$C --profile eval run --rm --no-deps model-eval ask --config chat-nano --repeats 2
python evals/model_eval/run.py report
```

`ask` reuses the collections of an earlier config with identical extraction settings in the same
results directory. If there are none, it extracts once first.

LangSmith: datasets are `document-intelligence-model-eval-<suite>-v1-<sha8>`, and each run is an
experiment `<config>-<suite>-<git sha7>`. The metadata holds the config and its SHA-256, the dataset SHA-256,
the git SHA, the judge model and timestamps.

Results recorded in the repository: `RESULTS-<date>.md` and `RESULTS-<date>.json`. One run is
evidence about these documents and questions, not a production decision.

Offline checks: `python -m unittest discover -s tests/unit -p test_model_eval.py`.
