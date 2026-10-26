# How chat answers a question over extracted metadata

This page traces one chat turn through the code and states exactly when the
answer comes from **extracted fields**, when from **semantic search**, and when
from both. It was written from hosted traces (each tool call's raw arguments
from the turn's LangSmith run, plus the `rag_context.tool_responses` the
response carries), not from reading prompts. File references are to
`backend/` at the commit that added this page.

## The path of one turn

1. **Query refinement** — `chat/graph.py:285-288` runs
   `query_refiner → rag → chart → follow_up`. `chat/query_refiner.py:154`
   rewrites the question with session history; on a first turn it is usually
   unchanged.
2. **Catalog prefetch** — `chat/rag_node.py:206` loads the collection's field
   catalog (names, types, enumerable values). `chat/rag_agent/agent_execution.py`
   injects it as an already-completed `retrieve_metadata_catalog` call
   (`build_metadata_catalog_tool_result`, `chat/rag_agent/context_utils.py:13`),
   so the agent starts knowing every field name and the low-cardinality values.
3. **Small collections skip the agent.** With ≤ `SMALL_KB_DOC_THRESHOLD` (15)
   documents, `_small_kb_direct` (`chat/rag_node.py:700`) does one hybrid chunk
   search and loads the extracted fields of **every** document in the
   collection, then goes straight to the answer node. No filter is built.
4. **Otherwise the retrieval agent** (`create_agent` with `RAG_TOOLS`,
   `chat/rag_agent/agent_creation.py`) classifies the question into a
   `question_shape` (`lookup | predicate | selection | aggregate | compare |
   deep_read | fact_lookup | metadata_lookup | multi_doc_synthesis`) and passes it
   on every tool call. The shape changes how the executor runs the call.
5. **Retrieval verifier** — after the agent stops, `verify_retrieval`
   (`chat/rag_agent/retrieval_verifier.py:546`) judges sufficiency with an LLM.
   A deterministic ladder gate (`_ladder_gate`, `:462`) forces one global chunk
   search for chunk-level questions that stopped early. If insufficient, the
   agent runs once more from a slim thread; the evidence of **both** attempts is
   kept (`agent_execution.py`, `earlier_attempt_tool_messages`).
6. **Shared aggregation, then the answer node.** Before the answer is written,
   `agent_execution` builds the turn's one aggregation from the chart's own
   code (`chart_generator.build_structured_aggregation`: `build_population` →
   `ChartSpec` (LLM: which field, which reduction) → `aggregate_population`
   (code: the numbers)). It runs when charts are on or the turn retrieved an
   exhaustive population; small-collection direct mode always runs it. The
   table goes into `rag_context["aggregation"]` and to the answer as
   "Computed Figures" with the rule to copy, never recount.
   `generate_rag_answer` (`chat/rag_agent/answer_node.py`) then writes the
   answer from the context block (`_build_context_block`).
   If the answer node produces nothing, the turn returns an explicit failure
   (`error: answer_generation_empty`, `is_answered: no`); the retrieval
   context is never sent as the answer.
7. **Sources** — `_filter_to_cited` (`chat/rag_node.py:444`) keeps the documents
   the agent cited; they become `data.knowledge.relevant_sources`.
8. **Chart** — `chart_node` plots `rag_context["aggregation"]` as-is, so the
   chart and the answer text show the same numbers. Only when no shared
   aggregation exists does it build the population itself
   (`chart_generator.build_population`, last exhaustive `retrieve_documents`
   result).

## Which evidence answers which question

| `question_shape` | Call the agent makes | What runs | Evidence the answer uses |
|---|---|---|---|
| `predicate`, `aggregate`, `selection` (exhaustive) | `retrieve_documents(metadata_filter=…, stats_fields=…, order_by=…)` | `query` is stripped; filter-only listing, AND across keys, every operator evaluated by Qdrant; no filter = the whole collection; up to 500 docs. `retrieve_chunks` refuses `aggregate` (and unscoped `predicate`/`selection`) | **Extracted fields only.** Exact set, `total_match_count`, and code-computed `stats` / `ranking` over the complete population |
| `lookup`, `compare`, `multi_doc_synthesis`, `fact_lookup`, `deep_read` | `retrieve_documents(query=…, metadata_filter=…)` | Hybrid semantic ranking (dense + BM25) inside the filter; top-K (3–10 by shape) | **Both**: the filter narrows by extracted fields, semantic search ranks; summaries + extracted fields of the top-K |
| any, when the text is needed | `retrieve_chunks(query=…, object_ids=…)` | Hybrid chunk search scoped to documents | **Original passages**, scoped by documents chosen from fields or ranking |
| point facts, no field applies | `retrieve_chunks(query=…)` (global) | Hybrid chunk search over the collection | **Semantic search only** |
| ≤ 15 documents | none (direct mode) | One hybrid chunk search + every document's fields | **Both**, unfiltered |

"Both" in a single call means the metadata filter is a hard scope
(`search_documents_by_query`, `utils/db/retrieval_ops.py:442`, puts it in the
Qdrant `must`) and the query ranks inside it. Semantic search never widens a
filter.

## Filter contract

`metadata_filter` is a dict whose top-level keys are AND-ed. Per-field: value,
list / `$in` (any of), `$nin`, `$ne`, `$gt` / `$gte` / `$lt` / `$lte`
(numbers, ISO dates), `$contains` / `$not_contains`; logical `$and`, `$or`,
`$nor`, `$not`. The normalizer (`chat/rag_tools/helpers.py`) keeps logical
operators intact and **refuses** anything else by name
(`UnsupportedMetadataFilter`); a field missing from the catalog is refused too.
The tool returns the refusal to the agent, which rewrites the filter. Nothing
is dropped silently.

Both execution paths build conditions with one builder,
`_build_filter_conditions_from_dict` (`utils/db/retrieval_ops.py`): the query
path, and the filter-only path the chat calls with `match_all=True`
(`utils/files/retrieval_utils.py`, `strict`). The public
`GET /v1/{id}/documents` default `match_all=false` keeps its documented loose-OR
behaviour for existing callers.

## What was measured and fixed (2026-10-25)

On the hosted 24-resume sample, before the fix, the exhaustive path OR-ed
fields (`{"education_level": "masters", "languages": "hindi"}` → 17 documents
instead of 4), matched nothing for ranges and `$ne` (`{"years_of_experience":
{"gte": 10}}` → 0 documents instead of 7), and the agent prompt said only `$in`
existed. So counts, thresholds and maxima were computed by the answer model
over every card, and came out wrong ("11 candidates know Python" for 18). A
verifier retry also discarded the complete population. The regression tests
are in `tests/unit/test_metadata_population.py`, and the hosted question set
and harness are in `evals/metadata_qa/`. The before/after results are in
[improvements/history.md](improvements/history.md#di-006).
