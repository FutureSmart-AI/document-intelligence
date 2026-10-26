"""Agent Health / Analytics event emission (see
docs/plan/analytics-and-health/events.md).

In-process, not HTTP: unlike fs-agent-chat's fire-and-forget POST to a
separate dashboard service, this repo owns both the chat graph and the
target table, so a turn's outcome is written directly via a DB insert.
Callers (routers/chat.py) fire this via ``asyncio.create_task`` so a slow or
failing insert never delays or fails the chat response — same degrade-not-
fail posture as chat/db/chat_logs.py::insert_chat_log.

build_health_event() derives every field from what chat/graph.py's
KnowledgeChatState actually produces (see that module's docstring for what
was deliberately dropped from the multi-service donor graph). Fields the
donor's Orchestrator populates that this single-capability RAG graph has no
equivalent for (``failed_stage`` beyond "rag", per-turn ``retry_count``) stay
``None``/``0`` here rather than a fabricated value — the same "null when this
graph doesn't produce it" posture the donor itself uses for non-RAG turns.

RAG eval Tier 0 (migration 048) arrives as one ``rag_eval_tier0`` dict from
rag_node and is spread into the row. It is EMPTY unless the turn's owner is in
RAG_EVAL_OWNER_IDS, so those nine columns stay NULL for everyone by default —
see utils/knowledge_health/rag_eval.py for the gate and why it is owner-scoped.

``confidence`` and ``stop_reason`` were in that list until 2026-08-27 and are
not any more: the RAG sub-agent reports both on every turn (RagAgentOutput,
chat/rag_agent/agent_output.py) and chat/rag_node.py now forwards them to graph
state, so this row carries the agent's own quality self-report at no extra LLM
cost. They stay None only when the turn produced no parseable structured
output.

``route`` is hardcoded to "knowledge": this graph has exactly one
capability, unlike the donor's router/multi-service fan-out.

``key_type`` is a 3-way simplification of the donor's owner-key/platform-key
pair, mapped onto this service's own auth modes (docs/modules/auth-and-rbac.md):
"master_key" | "session_or_api_key" | "public". Session and per-user API-key
callers are NOT distinguished here because utils/auth/dependencies.py::
require_permission collapses both to the same bare user_id string before it
reaches the router — splitting them would require threading auth_type
through that dependency's return value, a change to a shared auth contract
this task doesn't need to make just to label one telemetry column.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from utils.db.session import async_session
from utils.logger import logger
from utils.models.db_tables import KnowledgeHealthEvent
from chat.pricing import price_breakdown


def _build_node_data(service_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """This turn's service_calls (chat/utils/service_calls.py records, tagged
    by node — refiner / rag_agent / generate_answer / chart, see chat/graph.py's
    docstring) -> the Agent Health per-node breakdown stored on
    knowledge_health_events.node_data.

    Grouped by (node_name, model) rather than kept as one row per call: a node
    can fire more than once in a turn (e.g. the RAG retrieval loop's verifier
    retry), and the scorecard's by_node aggregation and this per-turn view
    must use the same grouping so a query's total_calls/errors match what the
    window-level aggregation would compute if it summed just this one turn.
    cost_usd is None whenever any grouped call's cost was unknown (mirrors
    chat/pricing.py::price_breakdown's "never silently zero" rule) — summing
    only the priced calls would understate cost without saying so.
    """
    grouped: Dict[tuple, Dict[str, Any]] = {}
    for call in service_calls or []:
        key = (call.get("service"), call.get("model"))
        node = grouped.setdefault(key, {
            "node_name": call.get("service"),
            "provider": call.get("provider"),
            "model": call.get("model"),
            "calls": 0,
            "errors": 0,
            "latency_ms": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "_cost_known": True,
        })
        node["calls"] += 1
        node["latency_ms"] += int(call.get("latency_ms") or 0)
        node["input_tokens"] += int(call.get("input_tokens") or 0)
        node["output_tokens"] += int(call.get("output_tokens") or 0)
        node["total_tokens"] += int(call.get("total_tokens") or 0)
        cost = call.get("cost_usd")
        if cost is None:
            node["_cost_known"] = False
        else:
            node["cost_usd"] += float(cost)

    result = []
    for node in grouped.values():
        cost_known = node.pop("_cost_known")
        node["cost_usd"] = round(node["cost_usd"], 6) if cost_known else None
        result.append(node)
    return result


def _resolve_status(final_state: Dict[str, Any]) -> tuple:
    """(status, failure_category, failed_stage, failure_detail) from final_state.

    This graph's error surface is a single ``error`` string (see
    chat/graph.py) plus the RAG node's own "yes"/"no"/None ``is_answered`` —
    far coarser than the donor's ten-way failure_category enum, so only the
    distinctions this graph actually makes are reported.
    """
    error = final_state.get("error")
    if error:
        return "error", "llm_error", "rag", str(error)[:2000]
    raw_is_answered = final_state.get("is_answered")
    if raw_is_answered == "no":
        return "no_answer", None, None, None
    return "success", None, None, None


def build_health_event(
    *,
    knowledge_id: str,
    owner_id: Optional[str],
    user_id: Optional[str],
    session_id: Optional[str],
    chat_id: str,
    query: str,
    latency_ms: int,
    key_type: str,
    trace_id: Optional[str] = None,
    from_widget: bool = False,
    final_state: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the field dict for one knowledge_health_events row from a
    completed chat turn. Pure function — no I/O — so it's independently
    testable from the insert."""
    usage_by_model = final_state.get("usage_by_model") or []
    run_usage = final_state.get("usage")
    if isinstance(run_usage, dict) and "operations" in run_usage:
        # The run-level block (chat/run_usage.py) counted every model call of
        # the turn -- verifier and follow-ups included, which usage_by_model
        # never saw -- and it is the block the API returned for this turn. Using
        # it here is what makes Analytics and the API report the same tokens
        # and cost for the same turn.
        from chat.run_usage import cost_breakdown_by_model

        breakdown = cost_breakdown_by_model(run_usage)
        priced = {
            "cost_breakdown": breakdown,
            "cost_usd": run_usage.get("estimated_cost_usd"),
            "cost_complete": bool(run_usage.get("cost_complete", True)),
        }
        total_input = int(run_usage.get("input_tokens") or 0)
        total_output = int(run_usage.get("output_tokens") or 0)
        answer_rows = [r for r in run_usage.get("operations") or [] if r.get("operation") == "answer"]
        last = answer_rows[-1] if answer_rows else (usage_by_model[-1] if usage_by_model else None)
        provider = last.get("provider") if last else None
        model = last.get("model") if last else None
    else:
        priced = price_breakdown(usage_by_model)
        total_input = sum(int(e.get("input_tokens") or 0) for e in usage_by_model)
        total_output = sum(int(e.get("output_tokens") or 0) for e in usage_by_model)
        provider = usage_by_model[-1].get("provider") if usage_by_model else None
        model = usage_by_model[-1].get("model") if usage_by_model else None

    status, failure_category, failed_stage, failure_detail = _resolve_status(final_state)
    raw_is_answered = final_state.get("is_answered")
    is_answered = raw_is_answered == "yes"

    services_used: List[str] = final_state.get("services_used") or ["knowledge"]

    return {
        "knowledge_id": knowledge_id,
        "owner_id": owner_id,
        "user_id": user_id,
        "session_id": session_id,
        "chat_id": chat_id,
        "trace_id": trace_id,
        # The turn's root LangSmith run (chat/graph.py::_traced_graph_invoke).
        # NOT interchangeable with trace_id above, which is an OpenTelemetry
        # span id: this is what the cost finalizer reads the turn's real priced
        # cost back by. None when tracing was inactive.
        "langsmith_run_id": final_state.get("langsmith_run_id"),
        "query": query,
        "refined_query": final_state.get("refined_query") or "",
        "route": "knowledge",
        "services_used": services_used,
        "status": status,
        "is_answered": is_answered,
        "failure_category": failure_category,
        "failed_stage": failed_stage,
        "failure_detail": failure_detail,
        # The retrieval agent's own per-turn self-report, forwarded through
        # graph state by chat/rag_node.py. Stays None when the agent emitted no
        # parseable structured output for the turn, or on the error path.
        "confidence": final_state.get("confidence"),
        "stop_reason": final_state.get("stop_reason"),
        "provider": provider or "openai",
        "model": model or "unknown",
        "from_widget": from_widget,
        "key_type": key_type,
        "latency_ms": int(latency_ms or 0),
        "input_tokens": total_input,
        "output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "retry_count": 0,
        "cost_usd": priced["cost_usd"] if priced["cost_breakdown"] else None,
        "cost_complete": priced["cost_complete"],
        "cost_breakdown": priced["cost_breakdown"],
        "node_data": _build_node_data(final_state.get("service_calls") or []),
        # RAG eval Tier 0 (migration 048), spread in from the dict rag_node
        # built. Empty for any owner outside RAG_EVAL_OWNER_IDS, which leaves
        # every column NULL — meaning "not recorded", never zero.
        **(final_state.get("rag_eval_tier0") or {}),
    }


async def emit_health_event(fields: Dict[str, Any]) -> None:
    """Insert one knowledge_health_events row. Never raises — a failed or
    duplicate (chat_id already recorded, e.g. a retried emit) insert must
    never surface to the caller, which by design has already returned or is
    about to return the chat response to the client."""
    try:
        chat_id = fields.get("chat_id")
        row = KnowledgeHealthEvent(
            **{**fields, "chat_id": uuid.UUID(str(chat_id))},
        )
        async with async_session() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # chat_id UNIQUE — a retried emit for the same turn, not an error.
                await session.rollback()
                logger.info(f"[health_events] Duplicate emit for chat_id={chat_id}, skipped")
    except Exception as e:
        logger.error(f"[health_events] Failed to emit health event: {e}", exc_info=True)
