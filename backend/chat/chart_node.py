"""Chart graph node — runs after rag_node, gated by the include_charts toggle.

Closes G16 (docs/product/screen-api-flow-document-intelligence.md's gap
register): there is now a structured chart_output field on the chat
response, produced by chat/chart_generator.py (ported from
fs-agent-chat/chat_utils/chart_utils_module/).

Kept as its own graph node (not folded into rag_node) so chart generation —
an extra LLM extraction pass plus the Chart API call (or the in-process
renderer, chat/local_chart_renderer.py, when CHART_API_URL is unset) — is a clearly
separate, independently-failing step: a chart failure never touches
rag_node's result, and this node's own failures are swallowed here, not
propagated to the graph.
"""
from __future__ import annotations

import time as _time
from typing import Any, Dict, List, Optional

from chat.utils.service_calls import build_service_call
from utils.logger import logger
from utils.tracing import track_time


@track_time("Node: Chart")
async def chart_node(
    query: str,
    knowledge_id: str,
    owner_id: Optional[str],
    detailed_chunks: Optional[Dict[str, List[dict]]],
    session_id: Optional[str] = None,
    summaries: Optional[Dict[str, Any]] = None,
    rag_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve include_charts for this knowledge_id and, when enabled and
    there is evidence to work with, generate a chart. Always returns a dict
    with a chart_output key (None when charts are off, there's nothing to
    chart, or generation failed) — never raises, so a chart problem can
    never fail an otherwise-successful chat turn.

    The chart is computed over the answer's DOCUMENT population (summaries /
    rag_context — see chart_generator.build_population), not over its top-k
    chunks: an exhaustive aggregate retrieval returns every document's
    extracted fields and no chunks at all, which used to mean no chart."""
    try:
        from chat.config import resolve_chat_config

        chat_config = await resolve_chat_config(knowledge_id, node_name="generate_answer")
        # No CHART_API_URL is no longer "no charts": chart_generator draws the chart
        # in-process (chat/local_chart_renderer.py) when the external API is unset.
        if not chat_config.include_charts:
            return {"chart_output": None}

        chunks_list: List[dict] = [
            chunk for group in (detailed_chunks or {}).values() for chunk in group
        ]

        from chat.chart_generator import build_population, process_chart_visualization

        _t0 = _time.perf_counter()
        shared = (rag_context or {}).get("aggregation")
        if isinstance(shared, dict):
            # The numbers the answer text was given (agent_execution computed
            # them before the answer). Plot those rows; never recompute — a
            # second computation is how the text said "1 candidate with 5
            # years" under a chart showing 2.
            logger.info(
                f"[chart_node] Charting the turn's shared aggregation "
                f"(status={shared.get('status')}, {len(shared.get('rows') or [])} row(s), "
                f"source={shared.get('source')})"
            )
            chart_output, _ = await process_chart_visualization(
                query, owner_id, [], knowledge_id=knowledge_id, aggregation=shared,
            )
            return {"chart_output": chart_output}
        population, population_source, complete = await build_population(
            knowledge_id, summaries, rag_context, chunks_list,
        )
        documents = [doc for doc in population if doc["fields"]]
        if not documents and not chunks_list:
            return {"chart_output": None}
        logger.info(
            f"[chart_node] Charting over {len(documents)} document(s) with extracted fields "
            f"(population={population_source}, {len(population)} doc(s), complete={complete}); "
            f"{len(chunks_list)} chunk(s)"
        )
        chart_output, usage = await process_chart_visualization(
            query, owner_id, chunks_list, knowledge_id=knowledge_id, documents=documents or None,
            population_complete=complete,
        )
        latency_ms = int((_time.perf_counter() - _t0) * 1000)
        if chart_output:
            logger.info("[chart_node] Chart generated successfully")

        result: Dict[str, Any] = {"chart_output": chart_output}
        # Agent Health node breakdown — chart extraction runs real LLM calls
        # (field-determination + batched row extraction, chat/chart_generator.py)
        # that were previously untracked; report them as the "chart" node
        # whenever at least one call returned usage metadata.
        # Also emit a usage_events row for the chart's real LLM calls. Previously
        # these tokens only appeared in the per-turn health-event breakdown, which
        # meant they were invisible to `usage_events` — and therefore to
        # get_current_usage's message aggregate, so a chart-heavy turn would under-
        # count against `max_messages_per_month`. Fire-and-forget: metering must not
        # be able to fail an otherwise-successful chart turn.
        if usage and (usage.get("input_tokens") or usage.get("output_tokens")):
            try:
                import asyncio as _asyncio_evt
                from utils.qdrant.usage_tracking import send_llm_usage_event

                _asyncio_evt.ensure_future(send_llm_usage_event(
                    label="chart",
                    provider=usage.get("provider"),
                    model=usage.get("model"),
                    input_tokens=usage.get("input_tokens", 0) or 0,
                    output_tokens=usage.get("output_tokens", 0) or 0,
                    owner_id=owner_id,
                    knowledge_id=knowledge_id,
                    session_id=session_id,
                    key_type=usage.get("key_type") or "platform-key",
                ))
            except Exception as _e_evt:
                logger.warning(f"[chart_node] Failed to emit usage event: {_e_evt}")
        if usage:
            result["usage_by_model"] = [{
                "provider": usage["provider"], "model": usage["model"],
                "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
            }]
            result["service_calls"] = [build_service_call(
                service="chart", latency_ms=latency_ms,
                provider=usage["provider"], model=usage["model"],
                input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
            )]
        return result
    except Exception as e:
        logger.warning(f"[chart_node] Failed, omitting chart from response: {e}")
        return {"chart_output": None}
