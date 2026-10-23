"""Chart generation for the Knowledge Service's chat graph.

Ported from fs-agent-chat/chat_utils/chart_utils_module/ (the refactored,
multi-file version — not the older chart_utils.py monolith). Condensed into
one module here, one function per pipeline step, matching this repo's own
one-file-per-node convention (see chat/rag_node.py). Behavior kept verbatim:
determine which fields to extract (LLM) -> extract structured rows from
chunks (LLM, batched) -> convert to CSV -> POST the CSV to the external
Chart API.

Reuses this repo's own infra instead of the donor's:
  - chat/llm_factory.py::get_llm (via chat/byok_fallback.py), which already
    carries the process-wide rate limiter (chat/llm_limiter.py) and resolves the
    owner's BYOK key — not a separate ad hoc ChatOpenAI built from OPENAI_API_KEY.
  - utils/logger.py::logger, not chat_services.logger.
  - utils/setup_env.py::CHART_API_URL, not a local os.getenv default.
"""
from __future__ import annotations

import ast
import csv
import io
import json
import os
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree
from langchain_core.messages import HumanMessage, SystemMessage

from chat.byok_fallback import run_with_key_fallback
from utils.logger import logger
from utils.setup_env import CHART_API_URL, CHART_EXTRACTION_MODEL

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
        async with aiohttp.ClientSession() as session:
            async with session.post(CHART_API_URL, data=form_data) as response:
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


async def process_chart_visualization(
    query: str, owner_id: Optional[str], chunks: List[Dict[str, Any]],
    knowledge_id: Optional[str] = None,
) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Main entry point: chunks -> CSV -> Chart API -> result dict (or None
    when there's nothing to chart, or the API call fails).

    Returns ``(chart_output, usage)``. ``usage`` is ``{"provider", "model",
    "input_tokens", "output_tokens"}`` for the Agent Health "chart" node
    breakdown when this pass made at least one LLM call with usage metadata,
    else ``None`` — chart_node.py only emits a service_call/usage_by_model
    entry when this is non-None."""
    if not owner_id:
        logger.info("[chart_generator] No owner_id, skipping chart generation")
        return None, None
    if not chunks:
        logger.info("[chart_generator] No chunks, skipping chart generation")
        return None, None

    usage_totals = {"input_tokens": 0, "output_tokens": 0}
    try:
        csv_data = await convert_chunks_to_csv(
            chunks, query=query, max_rows=MAX_CSV_ROWS, usage_totals=usage_totals,
            owner_id=owner_id, knowledge_id=knowledge_id,
        )
        if not csv_data:
            logger.info("[chart_generator] No CSV data extracted, skipping chart generation")
            return None, _finalize_usage(usage_totals)
        return await call_chart_api(query, owner_id, csv_data), _finalize_usage(usage_totals)
    except Exception as e:
        logger.error(f"[chart_generator] process_chart_visualization failed: {e}\n{traceback.format_exc()}")
        return None, _finalize_usage(usage_totals)


def _finalize_usage(usage_totals: Dict[str, int]) -> Optional[Dict[str, Any]]:
    """usage_totals dict -> the Agent Health usage record, or None if this
    pass never got a token count back (no successful LLM call)."""
    if not (usage_totals["input_tokens"] or usage_totals["output_tokens"]):
        return None
    return {"provider": "openai", "model": _EXTRACTION_MODEL, **usage_totals}
