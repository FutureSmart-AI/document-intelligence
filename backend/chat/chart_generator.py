"""Chart generation for the Knowledge Service's chat graph.

Ported from fs-agent-chat/chat_utils/chart_utils_module/ (the refactored,
multi-file version — not the older chart_utils.py monolith). Condensed into
one module here, one function per pipeline step, matching this repo's own
one-file-per-node convention (see chat/rag_node.py). Behavior kept verbatim:
determine which fields to extract (LLM) -> extract structured rows from
chunks (LLM, batched) -> convert to CSV -> POST the CSV to the external
Chart API when CHART_API_URL is configured (the hosted product), else draw it
in-process with chat/local_chart_renderer.py (standalone installs).

Reuses this repo's own infra instead of the donor's:
  - chat/llm_factory.py::get_llm (via chat/byok_fallback.py), which already
    carries the process-wide rate limiter (chat/llm_limiter.py) and resolves the
    owner's BYOK key — not a separate ad hoc ChatOpenAI built from OPENAI_API_KEY.
  - utils/logger.py::logger, not chat_services.logger.
  - utils/setup_env.py::CHART_API_URL, not a local os.getenv default.
"""
from __future__ import annotations

import ast
import asyncio
import csv
import io
import json
import os
import traceback
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

import aiohttp
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from chat.byok_fallback import run_with_key_fallback
from utils.logger import logger
from utils.setup_env import CHART_API_URL, CHART_EXTRACTION_MODEL, CHART_SERVICE_KEY

MAX_CSV_ROWS = 50
MAX_CSV_SIZE = 100_000
# Env-overridable (CHART_EXTRACTION_MODEL). Was a hardcoded literal until
# 2026-08-20, when the platform OpenAI project lost access to it and every chart
# attempt 403'd with `model_not_found` — unfixable without a redeploy.
_EXTRACTION_MODEL = CHART_EXTRACTION_MODEL

# The extraction LLM is now built PER CALL through chat/llm_factory.py::get_llm
# rather than once at import time from OPENAI_API_KEY. Until 2026-08-25 this module
# held a module-level ChatOpenAI, which meant chart extraction was the one chat path
# that silently ignored the owner's BYOK key and billed every chart to the platform
# account — including for owners who had supplied their own OpenAI key precisely to
# avoid that.
#
# Still deliberately pinned to openai/_EXTRACTION_MODEL rather than reading the
# collection's configured provider/model: that would change WHICH model answers
# chart requests, which is a behavioural change with its own eval, whereas paying
# with the right key is a straight bug fix. The per-knowledge-base model override
# remains the separate change the old comment here described.
_EXTRACTION_PROVIDER = "openai"

# Test seam only — production code never sets this. When non-None,
# _invoke_extraction_llm calls it directly instead of resolving a real model
# through chat.byok_fallback, so tests/unit/test_chart_generator_chunk_text.py
# can substitute a fake LLM without also having to fake the BYOK/platform-key
# resolution chain, which is exercised by its own tests elsewhere.
_extraction_llm: Optional[Any] = None


def _chunk_text(chunk: Dict[str, Any]) -> str:
    """The chunk's body text, whatever key retrieval used for it.

    Retrieval emits ``chunk_text`` (utils/files/retrieval_utils.py,
    chat/knowledge_adapter.py); some paths hand back ``text``/``content``
    instead, and _group_chunks passes whichever shape it got straight through
    to us. This module read only ``text`` until 2026-08-24, so on the common
    ``chunk_text`` shape every prompt below was built with an empty
    ``Content:`` — the LLM dutifully extracted blank rows and the Chart API
    then declined to plot them, with nothing logged anywhere. Same fallback
    chain the other chunk consumers use (chat/rag_agent/answer_node.py,
    chat/rag_tools/renderers.py)."""
    return chunk.get("chunk_text") or chunk.get("text") or chunk.get("content") or ""


def _chunk_metadata(chunk: Dict[str, Any]) -> Dict[str, Any]:
    """The chunk's nested document metadata, tolerating a repr'd dict.

    Deliberately not rag_tools/chunk_utils.py::normalized_chunk_metadata: that
    one folds every unrecognised top-level key in for API clients, which here
    would put ids and search_method into the prompt and invite the field-
    determination LLM to propose them as chart columns."""
    metadata = chunk.get("metadata", {})
    if isinstance(metadata, str):
        try:
            metadata = ast.literal_eval(metadata)
        except Exception:
            metadata = {}
    return metadata if isinstance(metadata, dict) else {}


_EMPTY_TOKENS = {"", "nan", "none", "null", "n/a", "na", "-", "unknown"}


def _is_empty_row(result: Dict[str, Any], fields: List[str]) -> bool:
    """True when the LLM found nothing for this chunk — every extracted field
    is blank, zero, or a null-ish placeholder.

    The extraction prompt tells the model to answer with ``""``/``0`` for a
    missing value, so a chunk with nothing relevant in it (boilerplate, a
    footer, an unrelated document in the same collection) still comes back as
    a full row of blanks. Those rows used to reach the Chart API and became
    real plotted bars — a "0 years"/"Unknown" bucket stacked as high as the
    genuine data. Only ``fields`` are judged: document_id/file_name are
    provenance we attach ourselves and are populated on every row."""
    for field in fields:
        value = result.get(field)
        if isinstance(value, bool):
            if value:
                return False
        elif isinstance(value, (int, float)):
            if value != 0:
                return False
        elif isinstance(value, str):
            if value.strip().lower() not in _EMPTY_TOKENS:
                return False
        elif value not in (None, [], {}):
            return False
    return True


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"):
        return text.replace("```json", "").replace("```", "").strip()
    if text.startswith("```"):
        return text.replace("```", "").strip()
    return text


def _accumulate_usage(totals: Dict[str, int], response: Any) -> None:
    """Add one LLM response's usage_metadata (input/output tokens) onto the
    running Agent Health totals for this chart-generation pass. Best-effort —
    a response with no usage_metadata (e.g. a fallback path) contributes
    nothing rather than raising."""
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict):
        totals["input_tokens"] += usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
        totals["output_tokens"] += usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0


async def _invoke_extraction_llm(
    messages: List[Any], owner_id: Optional[str], knowledge_id: Optional[str], source: str
) -> Any:
    """One extraction call, on the owner's OpenAI key when they have one.

    Routed through ``run_with_key_fallback`` so a revoked owner key is demoted and
    the call retried on the platform key, rather than failing the chart — the same
    policy every other chat LLM call in this repo follows.
    """
    if _extraction_llm is not None:  # test seam — see the module-level comment
        return await _extraction_llm.ainvoke(messages)
    return (
        await run_with_key_fallback(
            lambda llm: llm.ainvoke(messages),
            owner_id=owner_id,
            knowledge_id=knowledge_id,
            source=source,
            provider=_EXTRACTION_PROVIDER,
            model=_EXTRACTION_MODEL,
            temperature=0,
        )
    ).value


async def determine_extraction_fields(
    query: str, chunks: List[Dict[str, Any]], usage_totals: Optional[Dict[str, int]] = None,
    owner_id: Optional[str] = None, knowledge_id: Optional[str] = None,
) -> List[str]:
    """Ask the LLM which columns are worth extracting for this query, based
    on a couple of sample chunks. Falls back to a generic 3-column schema on
    any failure so the pipeline never blocks on this step.

    ``usage_totals`` — when given, this call's tokens are added onto it (for
    the Agent Health "chart" node breakdown); omit to skip tracking."""
    try:
        sample_data = ""
        for i, chunk in enumerate(chunks[:2]):
            metadata = _chunk_metadata(chunk)
            text = _chunk_text(chunk)
            sample_data += f"\n--- Sample {i + 1} ---\nMetadata: {metadata}\nContent: {text[:500]}\n"

        system_prompt = """You are a data architect. Your goal is to identify the most relevant fields (columns) to extract from the provided data for a visualization task.

User Query: {query}

Based on the user query and the sample data provided, dynamically determine the best schema for extraction:
1. Identify the core entity being discussed.
2. Identify all quantitative metrics mentioned or implied by the query.
3. Identify categorical dimensions needed for grouping or filtering.
4. Ensure field names are descriptive, written in snake_case, and directly reflect the data found in the samples.
5. Prioritize fields that directly answer the user's specific question while providing enough context for a meaningful chart.

Return ONLY a JSON array of strings representing these dynamic field names. Do not include any explanation or markdown formatting."""

        messages = [
            SystemMessage(content=system_prompt.format(query=query)),
            HumanMessage(content=f"Sample Data:\n{sample_data}"),
        ]
        response = await _invoke_extraction_llm(
            messages, owner_id, knowledge_id, "chart_fields"
        )
        if usage_totals is not None:
            _accumulate_usage(usage_totals, response)
        fields = json.loads(_strip_json_fence(response.content))
        if not isinstance(fields, list):
            fields = ["name", "value", "category"]
        return fields
    except Exception as e:
        logger.warning(f"[chart_generator] determine_extraction_fields failed: {e}")
        return ["name", "description", "category"]


async def extract_structured_data_with_llm(
    chunks: List[Dict[str, Any]], query: str = "", usage_totals: Optional[Dict[str, int]] = None,
    owner_id: Optional[str] = None, knowledge_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Extract structured rows from chunks (batched LLM calls). Each row
    keeps document_id/file_name so the caller can trace it back.

    ``usage_totals`` — when given, every call this function makes (the field-
    determination call plus each extraction batch) adds its tokens onto it."""
    try:
        fields = await determine_extraction_fields(
            query, chunks, usage_totals=usage_totals,
            owner_id=owner_id, knowledge_id=knowledge_id,
        )

        system_prompt = f"""You are a data extraction expert. Extract structured information from the provided data for chart visualization.

User Query: {query}

For each item, extract the following fields in JSON format:
{chr(10).join(f"- {field}" for field in fields)}

Return ONLY a valid JSON array of objects. Do not include any other text or explanation.
Give every object an "item_number" field set to the number of the ITEM it came from.
OMIT an item entirely if its content holds no data relevant to the user query. Do NOT
invent a placeholder object of empty strings and zeros for it — a collection often holds
unrelated documents, and a fabricated zero row becomes a real plotted bar. Returning fewer
objects than there are items, or an empty array, is the correct answer when the data is not there.
If a field is numeric (like years, count, price), return it as a pure number (integer or float), not a string.
If one field is missing but the item is otherwise relevant, use an empty string "" or 0 for that field."""

        structured_results: List[Dict[str, Any]] = []
        dropped_empty = 0
        batch_size = 5
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            batch_text = ""
            for idx, chunk in enumerate(batch):
                metadata = _chunk_metadata(chunk)
                # Retrieval flattens these onto the chunk on some paths.
                title = metadata.get("title") or chunk.get("title") or ""
                summary = metadata.get("summary") or chunk.get("summary") or ""
                text = _chunk_text(chunk)
                file_name = chunk.get("file_name") or metadata.get("file_name") or ""
                batch_text += f"\n\n--- ITEM {idx + 1} ---\n"
                if file_name:
                    batch_text += f"File: {file_name}\n"
                if title:
                    batch_text += f"Title: {title}\n"
                if summary:
                    batch_text += f"Summary: {summary}\n"
                batch_text += f"Content: {text[:1500]}\n"

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Extract structured data from these items:\n{batch_text}"),
            ]
            try:
                response = await _invoke_extraction_llm(
                    messages, owner_id, knowledge_id, "chart_extraction"
                )
                if usage_totals is not None:
                    _accumulate_usage(usage_totals, response)
                batch_results = json.loads(_strip_json_fence(response.content))
                if isinstance(batch_results, dict):
                    batch_results = [batch_results]
                elif not isinstance(batch_results, list):
                    batch_results = []
            except Exception as e:
                logger.warning(f"[chart_generator] LLM extraction batch failed: {e}")
                batch_results = [{} for _ in batch]

            for j, result in enumerate(batch_results):
                if not isinstance(result, dict):
                    continue
                # Map the row back to its chunk by the item_number the model
                # echoes, not by position. Position only held while the model
                # returned exactly one object per item; now that it may omit
                # irrelevant items, a positional read would staple each row to
                # the wrong document. Falls back to position when the model
                # skips the field (older behaviour, still 1:1 in that case).
                offset = j
                raw_number = result.pop("item_number", None)
                try:
                    if raw_number is not None:
                        offset = int(raw_number) - 1
                except (TypeError, ValueError):
                    offset = j
                if not 0 <= offset < len(batch):
                    logger.warning(
                        f"[chart_generator] Extraction row referenced ITEM {raw_number}, "
                        f"outside a batch of {len(batch)}; dropping the row rather than "
                        "attributing it to the wrong document"
                    )
                    continue
                for field in fields:
                    result.setdefault(field, "")
                # Belt and braces: the prompt now asks the model to omit
                # irrelevant items, but a row of the prompt's own ""/0
                # missing-value encoding still must never reach the chart.
                if _is_empty_row(result, fields):
                    dropped_empty += 1
                    continue
                # Same document_id/object_id fallback _group_chunks keys on,
                # so a charted row can still be traced back to its document.
                source = batch[offset]
                result["document_id"] = source.get("document_id") or source.get("object_id") or ""
                result["file_name"] = source.get("file_name") or ""
                structured_results.append(result)

        if dropped_empty:
            logger.info(
                f"[chart_generator] Dropped {dropped_empty} empty row(s) of "
                f"{len(chunks)} chunk(s); {len(structured_results)} row(s) to chart"
            )
        return structured_results
    except Exception as e:
        logger.error(f"[chart_generator] extract_structured_data_with_llm failed: {e}")
        return []


def _normalize_value(value: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


async def convert_chunks_to_csv(
    chunks: List[Dict[str, Any]], query: str = "", max_rows: int = MAX_CSV_ROWS,
    usage_totals: Optional[Dict[str, int]] = None,
    owner_id: Optional[str] = None, knowledge_id: Optional[str] = None,
) -> str:
    """Extract structured rows from chunks (LLM) and render them as CSV,
    truncating rows if the result would exceed MAX_CSV_SIZE."""
    if not chunks:
        return ""

    # Nothing to extract FROM. Without this the pipeline still ran a field-
    # determination call plus one extraction call per batch of 5, all on empty
    # `Content:`, and produced a CSV of blank rows the Chart API then refused
    # to plot — burning latency and tokens with no error anywhere. Loud,
    # because an empty body here means retrieval handed us a chunk shape
    # _chunk_text does not know, not that the documents were empty.
    if not any(_chunk_text(chunk) for chunk in chunks if isinstance(chunk, dict)):
        logger.warning(
            f"[chart_generator] All {len(chunks)} chunk(s) have empty text "
            f"(keys seen: {sorted({k for c in chunks if isinstance(c, dict) for k in c})}); "
            "skipping chart extraction"
        )
        return ""

    structured_data = await extract_structured_data_with_llm(
        chunks[:max_rows], query=query, usage_totals=usage_totals,
        owner_id=owner_id, knowledge_id=knowledge_id,
    )
    # No fallback to `chunks` here. Extraction returning nothing means no chunk
    # held chartable data; the old fallback rendered the RAW chunk dicts to CSV
    # — chunk_text, knowledge_id, internal ids and all — and POSTed them to the
    # external Chart API, which then plotted whatever it could make of them.
    # Nothing to chart is a legitimate outcome; say so and stop.
    if not structured_data:
        logger.info(
            f"[chart_generator] No chartable rows extracted from {len(chunks)} chunk(s); "
            "skipping chart generation"
        )
        return ""
    rows = structured_data

    fieldnames: set = set()
    for row in rows:
        if isinstance(row, dict):
            fieldnames.update(row.keys())
    if not fieldnames:
        return ""
    fieldnames = sorted(fieldnames)

    def _render(rows_subset: List[Dict[str, Any]]) -> str:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows_subset:
            if not isinstance(row, dict):
                continue
            out_row = {}
            for field in fieldnames:
                value = row.get(field, "")
                if isinstance(value, (list, dict)):
                    value_str = str(value)
                    out_row[field] = value_str[:200] if len(value_str) > 200 else value_str
                elif isinstance(value, str) and len(value) > 500:
                    out_row[field] = value[:500] + "..."
                else:
                    out_row[field] = value
            writer.writerow(out_row)
        content = buf.getvalue()
        buf.close()
        return content

    csv_content = _render(rows)
    if len(csv_content) > MAX_CSV_SIZE:
        csv_content = _render(rows[: max_rows // 2])
    return csv_content


# ── Population path: chart the answer's documents, not its top-k chunks ──
# Until 2026-10-25 every chart was built from the retrieved CHUNKS: one LLM row
# per chunk, re-reading values out of chunk text, posted raw to the Chart API.
# Three things followed, all measured on the hosted app with an 8-resume
# collection and the 24-resume sample:
#   * an exhaustive aggregate retrieval (retrieve_documents, shape=aggregate)
#     returns every document's extracted fields and NO chunks — chart_node saw
#     no chunks and returned no chart for "distribution of candidates by years
#     of experience" over all 24 resumes;
#   * a document with N retrieved chunks became N rows, and a chunk missing the
#     fact became an "Unknown" row — counts were per chunk, not per document,
#     and disagreed with the extracted field the answer used;
#   * the raw rows reached the Chart API unaggregated, which plotted one bar
#     per row ("Python" three times, each valued 3) or 500'd trying to read a
#     name as a number.
# The Chart API is a result-table plotter (the Database Agent hands it a SQL
# result). So the table it gets is now the RESULT: one row per document from
# its extracted fields, reduced to the asked aggregate by deterministic code.
# The LLM only decides what to plot (ChartSpec) — counting is not a judgement.

_POPULATION_SKIP_FIELDS = {
    "summary", "title", "document_outline", "temporal_context", "keywords",
    "topics", "entities", "organizations", "object_id", "knowledge_id",
    "user_id", "file_name", "filename", "url", "score", "chunk_text",
}
_MAX_CELL_CHARS = 200
_MAX_SPEC_SAMPLE_VALUES = 8
_NOT_STATED = "Not stated"
# Bound on the collection-size lookup that decides completeness. The chart runs
# after the answer, inside the same chat turn; a slow count must cost the chart
# (treated as "unknown", so aggregates are not drawn), never the turn.
_COUNT_TIMEOUT_S = 10


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) and not (
        isinstance(value, str) and len(value) > _MAX_CELL_CHARS
    )


def _row_fields(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """The chartable part of one document's extracted fields: scalars and lists
    of scalars. Nested records (work_experience) and long prose (summary) are
    not plottable dimensions and would only crowd the spec prompt."""
    fields: Dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if key in _POPULATION_SKIP_FIELDS or value is None:
            continue
        if _is_scalar(value):
            fields[key] = value
        elif isinstance(value, list) and value and all(_is_scalar(v) for v in value):
            fields[key] = value
    return fields


def _document_label(entry: Dict[str, Any], metadata: Dict[str, Any], object_id: str) -> str:
    return str(
        entry.get("file_name") or metadata.get("file_name")
        or entry.get("title") or metadata.get("title") or object_id
    )


# Fields that only say what KIND of document it is. A filter on these alone
# fetches "all resumes"; it does not express the question's condition.
_STRUCTURAL_FILTER_FIELDS = {"document_type"}


def _filter_field_names(node: Any) -> set:
    """Field names an applied_filter constrains, through $and/$or/$not nesting."""
    names: set = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key.startswith("$"):
                names |= _filter_field_names(value)
            else:
                names.add(key)
    elif isinstance(node, list):
        for item in node:
            names |= _filter_field_names(item)
    return names


def _filter_states_the_condition(artifact: Dict[str, Any]) -> bool:
    """True when the retrieval's filter constrains a field beyond the document
    kind — i.e. the retrieval itself applied the question's condition."""
    return bool(_filter_field_names(artifact.get("applied_filter")) - _STRUCTURAL_FILTER_FIELDS)


def _latest_exhaustive_artifact(rag_context: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The last retrieve_documents call the agent made with an exhaustive shape
    (aggregate / predicate / selection). Its documents are the full matching
    population the answer was computed over; the last call wins because a
    re-planned retrieval supersedes the earlier attempt."""
    from chat.rag_tools.consolidated_tools import EXHAUSTIVE_SHAPES

    latest = None
    for response in (rag_context or {}).get("tool_responses") or []:
        result = response.get("result") if isinstance(response, dict) else None
        if (
            isinstance(result, dict)
            and result.get("question_shape") in EXHAUSTIVE_SHAPES
            and isinstance(result.get("documents"), list)
            and result.get("total_match_count") is not None
        ):
            latest = result
    return latest


async def _collection_document_count(knowledge_id: Optional[str]) -> Optional[int]:
    """How many documents the collection holds, or None when it cannot be told
    (no knowledge_id, the lookup failed, or the listing hit its 1000 cap)."""
    if not knowledge_id:
        return None
    try:
        from chat.knowledge_adapter import get_docs

        ids = await asyncio.wait_for(get_docs(knowledge_id), timeout=_COUNT_TIMEOUT_S)
        return len(ids) if ids is not None and len(ids) < 1000 else None
    except asyncio.TimeoutError:
        logger.warning(
            f"[chart_generator] Counting the collection's documents took over {_COUNT_TIMEOUT_S}s; "
            "treating the population as incomplete"
        )
        return None
    except Exception as e:
        logger.warning(f"[chart_generator] Could not count the collection's documents: {e}")
        return None


def _prior_turn_population(rag_context: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """The earlier turn's documents this turn's answer is grounded on, gated
    exactly as the answer node gates them (answer_node._build_context): the
    ones the agent cited when it cited any of them, otherwise all of them."""
    prior_docs = [d for d in ((rag_context or {}).get("prior_turn_docs") or [])
                  if isinstance(d, dict) and d.get("object_id")]
    if not prior_docs:
        return {}
    from chat.rag_agent.answer_node import _cited_ids_from_agent_output

    cited, _ = _cited_ids_from_agent_output((rag_context or {}).get("agent_output") or {})
    chosen = [d for d in prior_docs if str(d["object_id"]) in cited] if cited else []
    return {str(d["object_id"]): {"file_name": d.get("file_name"), "summary": d.get("summary"),
                                  "metadata": d.get("metadata") or {}}
            for d in (chosen or prior_docs)}


async def build_population(
    knowledge_id: Optional[str],
    summaries: Optional[Dict[str, Any]],
    rag_context: Optional[Dict[str, Any]],
    chunks: Optional[List[Dict[str, Any]]] = None,
) -> tuple[List[Dict[str, Any]], str, bool]:
    """The documents the answer was computed over, one entry per document:
    ``{"object_id", "document", "fields"}``. Returns ``(population, source,
    complete)``.

    ``complete`` says whether the population is the WHOLE set the question is
    about, which an aggregate chart (count / sum / average) needs — a
    distribution over a retrieved sample is a wrong chart, not a partial one:
      * exhaustive aggregate retrieval (the agent's last retrieve_documents
        call with shape=aggregate): its documents, complete;
      * exhaustive predicate / selection retrieval: the documents the agent
        selected from it (the cited summaries; all of it when nothing was
        cited) — complete, because the agent saw every candidate. The
        artifact itself is NOT the population here: in semantic-scope mode it
        is every ranked document, matches and non-matches alike (measured:
        21 ranked, 8 matched);
      * no exhaustive retrieval: the cited summaries plus the documents behind
        the cited chunks, complete only when that covers every document in
        the collection (small-KB direct mode usually does; a top-k chunk
        search over 24 resumes that reached 10 does not);
      * no evidence retrieved this turn at all (a follow-up answered from an
        earlier turn's documents): those prior-turn documents, gated by the
        agent's citations as the answer node gates them; complete under the
        same collection-count rule.
    Before all of these: a follow-up the agent said is about exactly the
    previous answer's result set (``carried_population``) is that set,
    complete. Citations reach here already resolved to the ids the agent was
    shown (chat/rag_agent/citations.py).
    An exhaustive retrieval that hit its cap is never complete. Documents whose
    extracted fields did not travel with the retrieval are fetched in one batch.
    """
    rag_summaries = (rag_context or {}).get("summaries") or {}
    artifact = _latest_exhaustive_artifact(rag_context)
    entries: Dict[str, Dict[str, Any]] = {}

    def _add_summaries(source_summaries: Optional[Dict[str, Any]]) -> None:
        for oid, entry in (source_summaries or {}).items():
            if isinstance(entry, dict):
                entries[str(oid)] = entry

    complete = False
    carried = [d for d in (rag_context or {}).get("carried_population") or []
               if isinstance(d, dict) and d.get("object_id")]
    if carried:
        # The question is about exactly the previous answer's documents (the
        # agent said so; chat/rag_agent/result_set.py). That set is the
        # population, complete by definition, whatever this turn retrieved
        # again: re-running turn 1's looser filter ("skills $in [python, aws]")
        # charted 14 people for "chart their years" after an answer of 8.
        source = f"previous_result:{len(carried)}"
        for doc in carried:
            oid = str(doc["object_id"])
            fresh = rag_summaries.get(oid) or {}
            entries[oid] = {"file_name": doc.get("file_name") or fresh.get("file_name"),
                            "summary": doc.get("summary"),
                            "metadata": {**(doc.get("metadata") or {}), **(fresh.get("metadata") or {})}}
        complete = True
    elif artifact is not None:
        source = f"exhaustive:{artifact.get('question_shape')}"
        artifact_docs: Dict[str, Dict[str, Any]] = {}
        for doc in artifact["documents"]:
            if isinstance(doc, dict) and doc.get("object_id"):
                oid = str(doc["object_id"])
                merged = dict(rag_summaries.get(oid) or {})
                merged_meta = dict(merged.get("metadata") or {})
                merged_meta.update(doc.get("metadata") or {})
                merged.setdefault("file_name", doc.get("file_name"))
                merged["metadata"] = merged_meta
                artifact_docs[oid] = merged
        selected = {str(oid) for oid in (summaries or {})} & set(artifact_docs)
        # A complete predicate/selection population whose filter states the
        # condition IS the satisfying set — the answer node keeps every member
        # of it (citation-trimming once answered "more than 10 years" with 2 of
        # 6), so the chart must too. Only a filter that merely narrows the
        # document kind (or no filter) leaves the choice to the agent's
        # judgement, and then its selection is the population.
        if (
            artifact.get("question_shape") != "aggregate"
            and selected
            and not (artifact.get("population_complete") and _filter_states_the_condition(artifact))
        ):
            source += ":selected"
            entries = {oid: artifact_docs[oid] for oid in artifact_docs if oid in selected}
        else:
            entries = artifact_docs
        # The retrieval layer's own verdict when it gives one
        # (consolidated_tools.retrieve_documents: exhaustive shape, not an
        # explicit-ids fetch, uncapped, every match returned); older artifacts
        # only say whether they were truncated.
        complete = bool(artifact.get("population_complete", not artifact.get("truncated")))
        if not complete:
            logger.warning(
                f"[chart_generator] Exhaustive retrieval is not a complete population "
                f"({len(artifact['documents'])} doc(s), total_match_count="
                f"{artifact.get('total_match_count')}, truncated={artifact.get('truncated')})"
            )
    else:
        source = "cited"
        _add_summaries(summaries)
        for chunk in chunks or []:
            if not isinstance(chunk, dict):
                continue
            oid = chunk.get("document_id") or chunk.get("object_id")
            if oid and str(oid) not in entries:
                entries[str(oid)] = {"file_name": chunk.get("file_name"), "metadata": {}}
        if not entries:
            # A follow-up the agent answered from an earlier turn's documents
            # (the reuse gate: no tool call this turn) has no fresh evidence;
            # the answer is grounded on those prior-turn documents, so they
            # are the population. "Plot their experience as a bar chart" after
            # a list of 8 candidates used to get no chart at all.
            prior = _prior_turn_population(rag_context)
            if prior:
                source = "prior_turn"
                entries = prior
        total = await _collection_document_count(knowledge_id)
        complete = total is not None and len(entries) >= total
        source += f":{len(entries)}of{total if total is not None else '?'}"

    missing = [oid for oid, e in entries.items() if not _row_fields(e.get("metadata") or {})]
    if missing and knowledge_id:
        try:
            from chat.knowledge_adapter import get_document_details

            for detail in await get_document_details(knowledge_id=knowledge_id, object_ids=missing):
                oid = str(detail.get("object_id"))
                if oid in entries and isinstance(detail.get("metadata"), dict):
                    entries[oid] = {**entries[oid], "metadata": detail["metadata"]}
                    if not entries[oid].get("file_name"):
                        entries[oid]["file_name"] = detail.get("file_name")
        except Exception as e:
            logger.warning(f"[chart_generator] Could not fetch extracted fields for {len(missing)} doc(s): {e}")

    population = []
    for oid, entry in entries.items():
        metadata = entry.get("metadata") or {}
        population.append({
            "object_id": oid,
            "document": _document_label(entry, metadata, oid),
            "fields": _row_fields(metadata),
        })
    return population, source, complete


class ChartSpec(BaseModel):
    """What to plot, decided by the LLM from the question and the field list.
    The numbers themselves are computed by aggregate_population."""

    chartable: bool = Field(description="True only when the question asks for a comparison, distribution, breakdown, ranking or trend across 2+ groups or documents — judged from the question alone, whether or not the listed fields hold the value.")
    reason: str = Field(description="One sentence: why this spec answers the question, or why no chart.")
    confidence: Literal["high", "medium", "low"]
    operation: Literal["count", "sum", "average", "per_document"] = Field(
        description="count = number of documents per group; sum/average = of value_field per group; per_document = one bar per document showing value_field.")
    asked_dimension: Optional[str] = Field(
        None, description="What the question groups or compares by, in the question's own words (e.g. 'primary skill', 'seniority level', 'candidate').")
    dimension_field_is_exact: bool = Field(
        True, description="True when group_by_field holds exactly asked_dimension. A list field is exact when the question asks about its values ('how many list python' -> skills is exact). False only when the question asks for ONE designated item the field does not single out (primary, main, current, highest) or for a different but related attribute (e.g. the full skills list for 'primary skill').")
    group_by_field: Optional[str] = Field(None, description="Field to group documents by (count/sum/average), or for per_document the field naming each document. Must be one of the listed fields.")
    value_field: Optional[str] = Field(None, description="Numeric field for sum/average/per_document. Must be one of the listed fields.")
    bucket_edges: Optional[List[float]] = Field(
        None, description="Only when group_by_field is numeric and the question asks for ranges or the values are many and spread out: ascending bucket lower bounds, e.g. [0, 3, 6, 10]. Null to group by exact value.")
    only_values: Optional[List[str]] = Field(
        None, description="Only when the question names specific groups to compare (e.g. 'python, aws and sql'): exactly those values. Null to show every group.")
    needs_document_text: bool = Field(
        False, description="True when the needed value is in NONE of the listed fields and must be read from the document text.")
    group_label: str = Field(description="Human column header for the groups, e.g. 'Years of experience'.")
    value_label: str = Field(description="Human column header for the values, e.g. 'Candidates'.")


def _field_catalog(population: List[Dict[str, Any]]) -> str:
    """field -> type and sample values, over the whole population, for the spec prompt."""
    catalog: Dict[str, Dict[str, Any]] = {}
    for doc in population:
        for name, value in doc["fields"].items():
            info = catalog.setdefault(name, {"types": set(), "values": [], "present": 0})
            info["present"] += 1
            info["types"].add("list" if isinstance(value, list) else type(value).__name__)
            for item in value if isinstance(value, list) else [value]:
                if item not in info["values"] and len(info["values"]) < _MAX_SPEC_SAMPLE_VALUES:
                    info["values"].append(item)
    return "\n".join(
        f"- {name} ({'/'.join(sorted(info['types']))}; present on {info['present']} of "
        f"{len(population)} docs): e.g. {info['values']}"
        for name, info in sorted(catalog.items())
    )


_CHART_SPEC_PROMPT = (
    "You decide how to chart a question about a collection of documents. Each document has "
    "extracted fields (listed with types and sample values). Pick the fields and the reduction "
    "that answer the question; code will compute the numbers over every document. Use only "
    "the listed field names, exactly as written. A question about how many documents fall in "
    "each category or range is operation=count grouped by that field (a list field counts a "
    "document once per distinct value). A 'distribution of X' across documents is always "
    "operation=count grouped by X (with bucket_edges when X is numeric and spread over many "
    "values), never one bar per document. A question comparing a numeric value across individual "
    "documents ('compare X for each candidate') is operation=per_document, with group_by_field "
    "the field that names each document. Never substitute a broader field for what was asked: when the question "
    "qualifies the value (primary, current, highest, first, main …) and no listed field "
    "captures that qualifier, set needs_document_text=true instead of grouping by a field that "
    "holds every value (e.g. 'primary skill' is not the full skills list). Set chartable=false "
    "for a single-number or single-document question, or when nothing would be compared."
)


async def _determine_chart_spec_impl(
    query: str, population: List[Dict[str, Any]], usage_totals: Optional[Dict[str, int]] = None,
    owner_id: Optional[str] = None, knowledge_id: Optional[str] = None,
) -> Optional[ChartSpec]:
    """LLM: which field and which reduction answer this chart question. Never
    raises; None means the spec could not be decided (no chart)."""
    messages = [
        SystemMessage(content=_CHART_SPEC_PROMPT),
        HumanMessage(content=(
            f"Question: {query}\n\nDocuments: {len(population)}\nFields:\n{_field_catalog(population)}"
        )),
    ]
    try:
        if _extraction_llm is not None:  # test seam — see the module-level comment
            result = await _extraction_llm.with_structured_output(ChartSpec, include_raw=True).ainvoke(messages)
        else:
            result = (await run_with_key_fallback(
                lambda llm: llm.with_structured_output(ChartSpec, include_raw=True).ainvoke(messages),
                owner_id=owner_id, knowledge_id=knowledge_id, source="chart_spec",
                provider=_EXTRACTION_PROVIDER, model=_EXTRACTION_MODEL, temperature=0,
                for_function_calling=True,
            )).value
        if usage_totals is not None and isinstance(result, dict):
            _accumulate_usage(usage_totals, result.get("raw"))
        spec = result.get("parsed") if isinstance(result, dict) else result
        if not isinstance(spec, ChartSpec):
            error = result.get("parsing_error") if isinstance(result, dict) else result
            logger.warning(f"[chart_generator] Chart spec unparseable: {error}")
            return None
        if (spec.chartable and spec.operation != "per_document" and spec.group_by_field
                and not spec.dimension_field_is_exact):
            # The model's own verdict that the field it picked is only related to
            # what was asked: read the value from the documents instead of
            # charting a substitute (measured: 'count per primary skill' charted
            # every listed skill, 'agile delivery' 8, at confidence=medium).
            spec.needs_document_text = True
        logger.info(
            f"[chart_generator] Chart spec decision: chartable={spec.chartable} "
            f"asked={spec.asked_dimension!r} exact={spec.dimension_field_is_exact} "
            f"op={spec.operation} group_by={spec.group_by_field} value={spec.value_field} "
            f"buckets={spec.bucket_edges} needs_text={spec.needs_document_text} "
            f"confidence={spec.confidence} reason={spec.reason!r}"
        )
        return spec
    except Exception as e:
        logger.warning(f"[chart_generator] determine_chart_spec failed: {e}")
        return None


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_number(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def _bucket_label(value: float, edges: List[float]) -> str:
    """'3-5' for [3, 6) on integer edges, '10+' for the open last bucket."""
    if value < edges[0]:
        return f"<{_fmt_number(edges[0])}"
    for i, lower in enumerate(edges):
        upper = edges[i + 1] if i + 1 < len(edges) else None
        if upper is None:
            return f"{_fmt_number(lower)}+"
        if lower <= value < upper:
            if float(lower).is_integer() and float(upper).is_integer():
                return f"{_fmt_number(lower)}-{_fmt_number(upper - 1)}"
            return f"{_fmt_number(lower)}-{_fmt_number(upper)}"
    return f"{_fmt_number(edges[-1])}+"


def _group_keys(value: Any, edges: Optional[List[float]]) -> List[Any]:
    """The group(s) one document falls in. A list counts once per distinct value."""
    if value is None or value == [] or (
        isinstance(value, str) and value.strip().lower() in _EMPTY_TOKENS
    ):
        return [_NOT_STATED]
    keys: List[Any] = []
    for item in value if isinstance(value, list) else [value]:
        if edges:
            number = _as_number(item)
            key = _bucket_label(number, edges) if number is not None else _NOT_STATED
        else:
            key = item.strip() if isinstance(item, str) else item
        if key not in keys:
            keys.append(key)
    return keys


def aggregate_population(
    population: List[Dict[str, Any]], spec: ChartSpec
) -> List[Dict[str, Any]]:
    """Reduce the per-document rows to the result table ``[{group_label: …,
    value_label: …}]``. Deterministic — this is where the numbers come from.
    Returns [] when the spec names a field no document has."""
    group_col, value_col = spec.group_label or "group", spec.value_label or "value"
    edges = sorted(set(spec.bucket_edges)) if spec.bucket_edges else None

    if spec.operation == "per_document":
        if not spec.value_field:
            return []
        rows = []
        for doc in population:
            number = _as_number(doc["fields"].get(spec.value_field))
            if number is None:
                continue
            label = doc["fields"].get(spec.group_by_field) if spec.group_by_field else None
            rows.append({group_col: str(label) if _is_scalar(label) else doc["document"], value_col: number})
        return sorted(rows, key=lambda r: -r[value_col])

    if not spec.group_by_field or not any(spec.group_by_field in d["fields"] for d in population):
        return []
    if spec.operation in ("sum", "average") and not spec.value_field:
        return []

    groups: Dict[Any, List[float]] = {}
    for doc in population:
        keys = _group_keys(doc["fields"].get(spec.group_by_field), edges)
        if spec.operation == "count":
            for key in keys:
                groups.setdefault(key, []).append(1.0)
            continue
        number = _as_number(doc["fields"].get(spec.value_field))
        if number is not None:
            for key in keys:
                groups.setdefault(key, []).append(number)

    def reduce(values: List[float]) -> float:
        return round(sum(values) / len(values), 2) if spec.operation == "average" else sum(values)

    rows = [{group_col: key, value_col: reduce(values)} for key, values in groups.items()]
    if spec.only_values:
        # Groups the question named. A named value no document has is a real
        # zero ("how many list rag" -> 0), so it is kept, not dropped.
        wanted = {str(v).strip().lower(): str(v).strip() for v in spec.only_values if str(v).strip()}
        present = {str(r[group_col]).strip().lower(): r for r in rows}
        rows = [present.get(key) or {group_col: label, value_col: 0.0} for key, label in wanted.items()]
    stated = [r for r in rows if r[group_col] != _NOT_STATED]
    unstated = [r for r in rows if r[group_col] == _NOT_STATED]
    if edges:
        order = {_bucket_label(e, edges): i for i, e in enumerate(edges)}
        stated.sort(key=lambda r: order.get(r[group_col], -1))
    elif stated and all(_as_number(r[group_col]) is not None for r in stated):
        stated.sort(key=lambda r: _as_number(r[group_col]))
    else:
        stated.sort(key=lambda r: (-r[value_col], str(r[group_col])))
    return stated + unstated


def _rows_to_csv(rows: List[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for row in rows:
        writer.writerow({
            k: (int(v) if isinstance(v, float) and v.is_integer() else v) for k, v in row.items()
        })
    return buf.getvalue()


def _chunks_by_document(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One extraction item per document (its chunks' text joined), so a
    document with three retrieved chunks yields one row, not three."""
    grouped: Dict[str, Dict[str, Any]] = {}
    for chunk in chunks or []:
        if not isinstance(chunk, dict):
            continue
        oid = str(chunk.get("document_id") or chunk.get("object_id") or id(chunk))
        item = grouped.setdefault(oid, {**chunk, "chunk_text": ""})
        item["chunk_text"] = (item["chunk_text"] + "\n" + _chunk_text(chunk)).strip()
    return list(grouped.values())


async def compute_aggregation(
    query: str, population: List[Dict[str, Any]], chunks: Optional[List[Dict[str, Any]]],
    usage_totals: Optional[Dict[str, int]] = None,
    owner_id: Optional[str] = None, knowledge_id: Optional[str] = None,
    complete: bool = True,
) -> Dict[str, Any]:
    """Population -> ChartSpec -> the aggregated result table, computed ONCE per
    turn and shared: the answer text states these numbers and the chart plots
    them (agent_execution computes it before the answer; chart_node reuses it).
    Never raises.

    Returns ``{"status", "rows", "spec", "documents", "complete"}``. ``status``
    is "ok" when ``rows`` hold numbers the turn may present; otherwise it names
    why there are none ("no_spec", "not_chartable", "needs_document_text",
    "incomplete", "no_rows") and ``rows`` is empty.

    When the spec says the value is in none of the extracted fields, the
    population's retrieved chunks are read once per document and merged into
    the rows before the same deterministic aggregation."""
    result: Dict[str, Any] = {"status": "no_spec", "rows": [], "spec": None,
                              "documents": len(population), "complete": bool(complete)}
    spec = await determine_chart_spec(
        query, population, usage_totals=usage_totals, owner_id=owner_id, knowledge_id=knowledge_id,
    )
    if spec is None:
        return result
    # needs_document_text is checked before chartable: a model that sees the
    # value is missing from the fields tends to answer chartable=false as well,
    # meaning "not from these fields", not "this question has no chart".
    if spec.needs_document_text and chunks:
        extracted = await extract_structured_data_with_llm(
            _chunks_by_document(chunks)[:MAX_CSV_ROWS], query=query, usage_totals=usage_totals,
            owner_id=owner_id, knowledge_id=knowledge_id,
        )
        by_doc = {str(r.get("document_id")): r for r in extracted if r.get("document_id")}
        for doc in population:
            row = by_doc.get(doc["object_id"]) or {}
            doc["fields"].update({
                k: v for k, v in row.items()
                if k not in ("document_id", "file_name") and (_is_scalar(v) or isinstance(v, list)) and v != ""
            })
        spec = await determine_chart_spec(
            query, population, usage_totals=usage_totals, owner_id=owner_id, knowledge_id=knowledge_id,
        )
        if spec is None:
            return result
    result["spec"] = spec.model_dump()
    if not spec.chartable:
        result["status"] = "not_chartable"
        return result
    if spec.needs_document_text:
        result["status"] = "needs_document_text"
        return result
    if spec.operation != "per_document" and not complete:
        # A count/sum/average over a retrieved sample would be presented as the
        # distribution of the whole collection — in the chart AND in the text.
        # per_document values stay valid: each is one document's own value.
        logger.info(
            f"[chart_generator] Not aggregating op={spec.operation}: the population is "
            f"{len(population)} retrieved document(s), not the complete set the question covers"
        )
        result["status"] = "incomplete"
        return result
    rows = aggregate_population(population, spec)
    if not rows:
        result["status"] = "no_rows"
        return result
    result.update(status="ok", rows=rows)
    logger.info(f"[chart_generator] Aggregation: {len(rows)} row(s) from {len(population)} document(s)")
    return result


def aggregation_to_csv(aggregation: Optional[Dict[str, Any]]) -> str:
    """The chart's result table from the turn's shared aggregation ('' = no chart)."""
    rows = (aggregation or {}).get("rows") or []
    if (aggregation or {}).get("status") != "ok" or len(rows) < 2:
        # The Chart API's own router declines a 0/1-row table; asking it anyway
        # is what produced its 500s ("'NoneType' object has no attribute 'get'").
        if rows:
            logger.info(f"[chart_generator] Result table has {len(rows)} row(s); nothing to compare, no chart")
        return ""
    logger.info(f"[chart_generator] Result table: {len(rows)} row(s) from {aggregation.get('documents')} document(s)")
    return _rows_to_csv(rows[:MAX_CSV_ROWS])


async def population_to_result_csv(
    query: str, population: List[Dict[str, Any]], chunks: Optional[List[Dict[str, Any]]],
    usage_totals: Optional[Dict[str, int]] = None,
    owner_id: Optional[str] = None, knowledge_id: Optional[str] = None,
    complete: bool = True,
) -> str:
    """Population -> ChartSpec -> aggregated result CSV ('' = no chart)."""
    aggregation = await compute_aggregation(
        query, population, chunks, usage_totals=usage_totals,
        owner_id=owner_id, knowledge_id=knowledge_id, complete=complete,
    )
    return aggregation_to_csv(aggregation)


async def build_structured_aggregation(
    query: str,
    knowledge_id: Optional[str],
    owner_id: Optional[str],
    summaries: Optional[Dict[str, Any]],
    rag_context: Optional[Dict[str, Any]],
    chunks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """The turn's ONE authoritative aggregation: the chart's own population
    (build_population) reduced by the chart's own spec + aggregate_population.
    Computed BEFORE the answer is written, so the text states these numbers and
    chart_node plots the very same rows instead of recomputing them. Never
    raises; carries its LLM usage under ``usage`` (None when no call returned
    usage)."""
    usage_totals = {"input_tokens": 0, "output_tokens": 0}
    try:
        population, source, complete = await build_population(knowledge_id, summaries, rag_context, chunks)
        documents = [doc for doc in population if doc["fields"]]
        if not documents:
            aggregation: Dict[str, Any] = {"status": "no_population", "rows": [], "spec": None,
                                           "documents": 0, "complete": False}
        else:
            aggregation = await compute_aggregation(
                query, documents, chunks, usage_totals=usage_totals,
                owner_id=owner_id, knowledge_id=knowledge_id, complete=complete,
            )
        aggregation["source"] = source
    except Exception as e:
        logger.warning(f"[chart_generator] build_structured_aggregation failed: {e}")
        aggregation = {"status": "error", "rows": [], "spec": None, "documents": 0, "complete": False}
    aggregation["usage"] = _finalize_usage(usage_totals)
    return aggregation


def render_aggregation_facts(aggregation: Optional[Dict[str, Any]]) -> str:
    """Markdown for the answer node: the exact table the chart plots."""
    if not aggregation or aggregation.get("status") != "ok" or not aggregation.get("rows"):
        return ""
    spec = aggregation.get("spec") or {}
    rows = aggregation["rows"]
    cols = list(rows[0].keys())
    scope = "all" if aggregation.get("complete") else "the"
    lines = [
        f"## Computed Figures (authoritative — computed by code over {scope} "
        f"{aggregation.get('documents')} matching document(s); any chart shown with this answer plots "
        "exactly these numbers)",
        f"operation={spec.get('operation')} · grouped by `{spec.get('group_by_field')}`"
        + (f" · value `{spec.get('value_field')}`" if spec.get("value_field") else "")
        + (f" · buckets {spec.get('bucket_edges')}" if spec.get("bucket_edges") else ""),
        "| " + " | ".join(str(c) for c in cols) + " |",
        "|" + "---|" * len(cols),
    ]
    for row in rows:
        lines.append("| " + " | ".join(
            str(int(v)) if isinstance(v, float) and v.is_integer() else str(v) for v in row.values()
        ) + " |")
    lines.append(
        "RULES: every count, total, sum or average you state for these groups MUST be the value in "
        "this table, copied exactly. Do not recount or recompute from the document cards. Do not "
        "state a number for a group that is not in this table."
    )
    return "\n".join(lines)


def _save_temp_csv(csv_data: str) -> Optional[str]:
    try:
        temp_dir = os.path.join(os.getcwd(), "temp")
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, f"chart_csv_{datetime.now().strftime('%H%M%S_%f')}.csv")
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(csv_data)
        return temp_path
    except Exception as e:
        logger.warning(f"[chart_generator] Could not save temp CSV: {e}")
        return None


def _cleanup_temp_file(path: Optional[str]) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception as e:
            logger.warning(f"[chart_generator] Failed to delete temp CSV: {e}")


# ── LangSmith span for the external Chart API call ───────────────────────
# The Chart API is a plain aiohttp POST, so it left no trace at all — the
# `chart` node showed only its LLM steps, and an empty or rejected chart gave
# no clue whether the CSV we sent or the API's reply was at fault. Payloads
# are bounded: a CSV can run to MAX_CSV_SIZE (100 KB) and a chart reply can
# carry an inline image, and neither belongs in a trace at full size.

_SPAN_CSV_PREVIEW_ROWS = 20
_SPAN_MAX_STR = 2_000


def _truncate(value: Any, limit: int = _SPAN_MAX_STR) -> Any:
    """Shorten long strings (a base64 image, a huge CSV) for the trace,
    leaving everything else untouched."""
    if isinstance(value, str) and len(value) > limit:
        return f"{value[:limit]}… [{len(value)} chars total]"
    return value


def _tag_span(**metadata: Any) -> None:
    """Attach metadata to the enclosing LangSmith run, if there is one.
    Never raises — tracing must not be able to break a chat turn."""
    try:
        run = get_current_run_tree()
        if run is not None:
            run.metadata.update(metadata)
    except Exception:  # pragma: no cover - defensive
        pass


def _chart_api_span_inputs(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """What we SENT: the query, and the CSV as row/byte counts plus a
    header-and-first-rows preview — enough to see whether the data was empty
    or wrongly shaped, which is the usual chart failure."""
    csv_data = inputs.get("csv_data") or ""
    rows = csv_data.splitlines()
    return {
        "query": inputs.get("query"),
        "owner_id": inputs.get("owner_id"),
        "csv_rows": max(len(rows) - 1, 0),  # minus the header
        "csv_bytes": len(csv_data),
        "csv_preview": "\n".join(rows[:_SPAN_CSV_PREVIEW_ROWS]),
    }


def _chart_api_span_outputs(result: Any) -> Dict[str, Any]:
    """What came BACK: the API's JSON with long string values truncated. None
    means the call failed or was declined — the run's http_status metadata and
    the error log say which."""
    if result is None:
        return {"result": None}
    if not isinstance(result, dict):
        return {"result": _truncate(str(result))}
    return {k: _truncate(v) for k, v in result.items()}


@traceable(
    name="chart_api",
    run_type="tool",
    process_inputs=_chart_api_span_inputs,
    process_outputs=_chart_api_span_outputs,
)
async def call_chart_api(query: str, owner_id: str, csv_data: str) -> Optional[Dict[str, Any]]:
    """POST the CSV to the external Chart API. Returns the API's JSON
    response, or None on any failure (never raises — chart generation is
    best-effort and must not break a chat turn)."""
    if not csv_data:
        return None

    temp_path = _save_temp_csv(csv_data)
    try:
        enhanced_query = (
            f"Using the provided data, generate a chart or visualization that answers: "
            f"{query}. If a chart is not suitable, provide a structured table."
        )
        form_data = aiohttp.FormData()
        form_data.add_field("query", enhanced_query)
        form_data.add_field("owner_id", owner_id)
        form_data.add_field(
            "file", csv_data.encode("utf-8"), filename="vector_data.csv", content_type="text/csv"
        )

        logger.info(f"[chart_generator] Calling Chart API at {CHART_API_URL}")
        # See utils/setup_env.py::CHART_SERVICE_KEY: identifies this service so the
        # Chart API does not apply the Database Agent's per-owner key policy to our
        # owner ids (every DI-only owner's chart failed with "No OpenAI API key").
        headers = {"X-Chart-Service-Key": CHART_SERVICE_KEY} if CHART_SERVICE_KEY else None
        async with aiohttp.ClientSession() as session:
            async with session.post(CHART_API_URL, data=form_data, headers=headers) as response:
                _tag_span(http_status=response.status)
                if response.status == 200:
                    result = await response.json()
                    logger.info(
                        f"[chart_generator] Chart API succeeded: "
                        f"chart_generated={result.get('chart_generated') if isinstance(result, dict) else None}"
                    )
                    return result
                error_text = await response.text()
                logger.error(f"[chart_generator] Chart API error {response.status}: {error_text}")
                return None
    except Exception as e:
        logger.error(f"[chart_generator] call_chart_api failed: {e}\n{traceback.format_exc()}")
        return None
    finally:
        _cleanup_temp_file(temp_path)


async def render_chart(
    query: str, owner_id: str, csv_data: str, knowledge_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """The result CSV -> chart dict, on whichever renderer this deployment has.

    CHART_API_URL set (hosted): the external Chart API, behaviour unchanged.
    Unset (standalone): the in-process renderer, whose one chart-type call is
    metered here as a ``chart_render`` usage event. Both return the same
    ``chart_response`` contract; the local one adds ``"renderer": "local"``."""
    from utils import setup_env  # read at call time: the deployment's setting, not an import snapshot

    if setup_env.CHART_API_URL:
        return await call_chart_api(query, owner_id, csv_data)
    from chat.local_chart_renderer import render_chart_locally

    chart, usage = await render_chart_locally(query, csv_data, owner_id=owner_id, knowledge_id=knowledge_id)
    if usage and (usage.get("input_tokens") or usage.get("output_tokens")):
        try:
            from utils.qdrant.usage_tracking import send_llm_usage_event

            asyncio.ensure_future(send_llm_usage_event(
                label="chart_render", provider=usage.get("provider"), model=usage.get("model"),
                input_tokens=usage.get("input_tokens", 0) or 0,
                output_tokens=usage.get("output_tokens", 0) or 0,
                owner_id=owner_id, knowledge_id=knowledge_id,
                key_type=usage.get("key_type") or "platform-key",
            ))
        except Exception as e:  # metering must never fail a chart
            logger.warning(f"[chart_generator] Failed to emit chart_render usage event: {e}")
    return chart


async def process_chart_visualization(
    query: str, owner_id: Optional[str], chunks: List[Dict[str, Any]],
    knowledge_id: Optional[str] = None,
    documents: Optional[List[Dict[str, Any]]] = None,
    population_complete: bool = True,
    aggregation: Optional[Dict[str, Any]] = None,
) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Main entry point: population -> result table -> Chart API -> result dict
    (or None when there's nothing to chart, or the API call fails).

    ``documents`` is the answer's document population, one entry per document
    with its extracted fields (see build_population); ``population_complete``
    is build_population's verdict on whether it is the whole set an aggregate
    needs. When given, the chart is
    computed from those fields — every document counted exactly once — and the
    chunk text is only read when the question needs something the fields do
    not hold. ``chunks`` alone is the pre-2026-10-25 behaviour and still works
    for callers that have no population.

    Returns ``(chart_output, usage)``. ``usage`` is ``{"provider", "model",
    "input_tokens", "output_tokens"}`` for the Agent Health "chart" node
    breakdown when this pass made at least one LLM call with usage metadata,
    else ``None`` — chart_node.py only emits a service_call/usage_by_model
    entry when this is non-None."""
    if not owner_id:
        logger.info("[chart_generator] No owner_id, skipping chart generation")
        return None, None
    if aggregation is not None:
        # The turn's shared aggregation (build_structured_aggregation, computed
        # before the answer): plot exactly the rows the answer was given. No
        # second spec call — a second LLM decision could pick another field or
        # bucketing and draw numbers the text never saw.
        csv_data = aggregation_to_csv(aggregation)
        if not csv_data:
            return None, None
        try:
            return await render_chart(query, owner_id, csv_data, knowledge_id=knowledge_id), None
        except Exception as e:
            logger.error(f"[chart_generator] process_chart_visualization failed: {e}")
            return None, None
    if not chunks and not documents:
        logger.info("[chart_generator] No chunks or documents, skipping chart generation")
        return None, None

    usage_totals = {"input_tokens": 0, "output_tokens": 0}
    try:
        if documents:
            csv_data = await population_to_result_csv(
                query, documents, chunks, usage_totals=usage_totals,
                owner_id=owner_id, knowledge_id=knowledge_id, complete=population_complete,
            )
        else:
            csv_data = await convert_chunks_to_csv(
                chunks, query=query, max_rows=MAX_CSV_ROWS, usage_totals=usage_totals,
                owner_id=owner_id, knowledge_id=knowledge_id,
            )
        if not csv_data:
            logger.info("[chart_generator] No CSV data extracted, skipping chart generation")
            return None, _finalize_usage(usage_totals)
        return (
            await render_chart(query, owner_id, csv_data, knowledge_id=knowledge_id),
            _finalize_usage(usage_totals),
        )
    except Exception as e:
        logger.error(f"[chart_generator] process_chart_visualization failed: {e}\n{traceback.format_exc()}")
        return None, _finalize_usage(usage_totals)


def _finalize_usage(usage_totals: Dict[str, int]) -> Optional[Dict[str, Any]]:
    """usage_totals dict -> the Agent Health usage record, or None if this
    pass never got a token count back (no successful LLM call)."""
    if not (usage_totals["input_tokens"] or usage_totals["output_tokens"]):
        return None
    return {"provider": "openai", "model": _EXTRACTION_MODEL, **usage_totals}


async def determine_chart_spec(*args, **kwargs):
    """``_determine_chart_spec_impl`` with every model call it makes attributed to the
    ``chart_spec`` operation of the turn's usage block (chat/run_usage.py)."""
    from chat.run_usage import in_operation
    return await in_operation("chart_spec", _determine_chart_spec_impl(*args, **kwargs))
