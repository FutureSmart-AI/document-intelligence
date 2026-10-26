"""In-process execution of a Batch Query Report run.

This is the core of the Worker-wiring decision recorded in
docs/plan/batch-query-reports/execution.md: instead of a separate Worker
process calling this service's chat endpoint once per query over HTTP,
`execute_run` runs the WHOLE pre-materialized run in this process, calling
`chat/graph.py::run_knowledge_chat_graph` directly for every query — the same
in-process entry point `routers/chat.py`'s own non-streaming path uses, never
an HTTP request to this service's own route over loopback.

Group semantics (docs/batch-query-reports-spec.md §3):
  - `independent` groups run their queries in PARALLEL, each its own
    conversation (fresh session_id, no shared history).
  - `sequential` groups run their queries IN ORDER, sharing one session_id
    and accumulating `past_messages` so each step sees prior turns.
  - Different groups always run concurrently with each other.

Reports are NOT persisted to `knowledge_chat_logs`/chat sessions — a report
query is a batch evaluation run, not an interactive chat turn, and mixing the
two would pollute session history and per-knowledge-base chat analytics
(agent-health) with synthetic traffic. This is a deliberate scope decision
(see decisions.md), matching how the donor's Worker never wrote to the Chat
Service's own session store either — it went straight to
`report_run_results`.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from chat.graph import run_knowledge_chat_graph
from chat.byok_fallback import run_with_key_fallback
from utils.logger import logger
from utils.reports import db_utils


def _sum_tokens(usage_by_model: Optional[List[Dict[str, Any]]]) -> Optional[int]:
    if not usage_by_model:
        return None
    total = 0
    for u in usage_by_model:
        total += (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
    return total or None


async def _run_one_query(
    query_text: str,
    knowledge_id: str,
    owner_id: Optional[str],
    session_id: str,
    past_messages: List[Dict[str, Any]],
    report_instructions: Optional[str],
) -> Dict[str, Any]:
    """Run a single query through the chat graph in-process. Never raises —
    an execution failure becomes a result row with `error` set, matching the
    spec's "a populated error is how a failed query is reported" contract."""
    # report_instructions is a report-level steering prompt (e.g. "answer
    # concisely, cite sources") — folded into the query text itself rather
    # than threaded as a new graph parameter, since the graph has no
    # dedicated slot for it and this keeps the executor decoupled from the
    # graph's internal prompt wiring.
    effective_query = (
        f"{report_instructions}\n\n{query_text}" if report_instructions else query_text
    )
    started = time.monotonic()
    try:
        final_state = await run_knowledge_chat_graph(
            query=effective_query,
            knowledge_id=knowledge_id,
            owner_id=owner_id,
            session_id=session_id,
            past_messages=past_messages,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        answer = final_state.get("response") or ""
        return {
            "answer": answer,
            "sources_used": final_state.get("relevant_sources") or [],
            "retrieved_documents": final_state.get("detailed_chunks") or [],
            "response_time_ms": elapsed_ms,
            "token_usage": _sum_tokens(final_state.get("usage_by_model")),
            "error": final_state.get("error"),
        }
    except Exception as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.error(f"[reports.execute] query failed run session={session_id}: {e}", exc_info=True)
        return {
            "answer": None,
            "sources_used": [],
            "retrieved_documents": [],
            "response_time_ms": elapsed_ms,
            "token_usage": None,
            "error": str(e),
        }


async def _run_independent_group(
    group: Dict[str, Any],
    run_id: str,
    knowledge_id: str,
    owner_id: Optional[str],
    report_instructions: Optional[str],
) -> None:
    """Each query is its own conversation; queries run in parallel."""
    async def _one(q: Dict[str, Any]) -> None:
        session_id = str(uuid.uuid4())
        result = await _run_one_query(
            q["text"], knowledge_id, owner_id, session_id, [], report_instructions
        )
        result.update({"query_id": q["query_id"], "group_id": group["group_id"], "session_id": session_id})
        await db_utils.upsert_results(run_id, [result])

    await asyncio.gather(*[_one(q) for q in group.get("queries", [])])


async def _run_sequential_group(
    group: Dict[str, Any],
    run_id: str,
    knowledge_id: str,
    owner_id: Optional[str],
    report_instructions: Optional[str],
) -> None:
    """Queries run in order, sharing one session_id/conversation history."""
    session_id = str(uuid.uuid4())
    past_messages: List[Dict[str, Any]] = []
    for q in group.get("queries", []):
        result = await _run_one_query(
            q["text"], knowledge_id, owner_id, session_id, list(past_messages), report_instructions
        )
        result.update({"query_id": q["query_id"], "group_id": group["group_id"], "session_id": session_id})
        await db_utils.upsert_results(run_id, [result])
        past_messages.append({"role": "user", "content": q["text"]})
        if result.get("answer"):
            past_messages.append({"role": "assistant", "content": result["answer"]})


async def _generate_overall_summary(
    knowledge_id: str,
    owner_id: Optional[str],
    analysis_prompt: str,
    run_full: Dict[str, Any],
) -> Optional[str]:
    """One-shot LLM call over the collected Q&A pairs — mirrors
    chat/follow_up.py's pattern (a lightweight post-hoc LLM call), not a
    LangGraph node, since this is a single summarization step, not a
    multi-step flow."""
    qa_lines = []
    for group in run_full.get("query_groups", []):
        for r in group.get("results", []):
            qa_lines.append(f"Q: {r.get('question')}\nA: {r.get('answer') or '(no answer / error)'}")
    if not qa_lines:
        return None
    context = "\n\n".join(qa_lines)
    try:
        messages = [
            ("system", "You are summarizing the results of a batch of chat queries run against "
                       "a knowledge base. Follow the user's instructions for what to produce."),
            ("human", f"{analysis_prompt}\n\n---\nReport results:\n{context}"),
        ]
        # A report can run long after the owner's key was stored, so it is a likely
        # place to meet an expired one. Retries on the platform key rather than
        # losing the whole report's summary — see chat/byok_fallback.py.
        _used: dict = {}

        async def _invoke(llm):
            _used["llm"] = llm
            return await llm.ainvoke(messages)

        _outcome = await run_with_key_fallback(
            _invoke, owner_id=owner_id, knowledge_id=knowledge_id,
            source="report_analysis", max_tokens=800,
        )
        response = _outcome.value
        llm = _used["llm"]
        _provider, _key_type = _outcome.provider, _outcome.key_type

        # Emit a usage_events row for the report-analysis LLM call. Previously
        # untracked, so a report's overall-summary tokens were invisible to
        # get_current_usage's message aggregate. Fire-and-forget: metering must not
        # be able to fail the summary itself.
        try:
            um = getattr(response, "usage_metadata", None)
            in_tok = int((um or {}).get("input_tokens") or (um or {}).get("prompt_tokens") or 0)
            out_tok = int((um or {}).get("output_tokens") or (um or {}).get("completion_tokens") or 0)
            if in_tok or out_tok:
                import asyncio as _asyncio_evt
                from utils.qdrant.usage_tracking import send_llm_usage_event
                # Resolve the concrete model name from the llm instance. Reports do
                # not go through get_llm's usual chat wrapper's extract helper, so
                # peek at the standard langchain-openai / -anthropic attributes.
                _model = getattr(llm, "model_name", None) or getattr(llm, "model", None) or "unknown"

                _asyncio_evt.ensure_future(send_llm_usage_event(
                    label="report_analysis",
                    provider=_provider,
                    model=_model,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    owner_id=owner_id,
                    knowledge_id=knowledge_id,
                    session_id=None,  # a report analysis is not attached to a chat session
                    key_type=_key_type or "platform-key",
                ))
        except Exception as _e_evt:
            logger.warning(f"[reports.execute] Failed to emit usage event: {_e_evt}")

        return getattr(response, "content", None) or str(response)
    except Exception as e:
        logger.error(f"[reports.execute] analysis generation failed: {e}", exc_info=True)
        return None


async def execute_run(run_id: str) -> None:
    """Execute every group/query of a pre-materialized run, in-process, and
    persist results/status/analysis directly to this service's own DB.

    Never raises to the caller (an internal router handler or a background
    task) — any failure to even start is caught and recorded as a `failed`
    run with `failure_reason`, matching the spec's whole-job-failure
    contract (individual query failures are reported via each result's
    `error` instead, never here).
    """
    try:
        definition = await db_utils.get_run_definition(run_id)
        if not definition:
            logger.error(f"[reports.execute] run {run_id} not found — nothing to execute")
            return

        knowledge_id = definition["knowledge_id"]
        owner_id = definition.get("owner_id")
        report_instructions = definition.get("report_instructions")
        analysis_prompt = definition.get("analysis_prompt")

        await db_utils.update_run_status(run_id, "running", started_at=datetime.now(timezone.utc))

        groups = definition.get("query_groups") or []
        # Different groups run concurrently with each other (spec §3);
        # within a group, independent/sequential semantics apply.
        tasks = []
        for group in groups:
            if group.get("type") == "sequential":
                tasks.append(_run_sequential_group(group, run_id, knowledge_id, owner_id, report_instructions))
            else:
                tasks.append(_run_independent_group(group, run_id, knowledge_id, owner_id, report_instructions))
        if tasks:
            await asyncio.gather(*tasks)

        if analysis_prompt:
            run_full = await db_utils.get_run_full(run_id)
            if run_full:
                summary = await _generate_overall_summary(knowledge_id, owner_id, analysis_prompt, run_full)
                if summary:
                    await db_utils.set_run_analysis(run_id, summary)

        await db_utils.update_run_status(run_id, "completed", ended_at=datetime.now(timezone.utc))
        logger.info(f"[reports.execute] run {run_id} completed")
    except Exception as e:
        logger.error(f"[reports.execute] run {run_id} failed: {e}", exc_info=True)
        try:
            await db_utils.update_run_status(
                run_id, "failed", ended_at=datetime.now(timezone.utc), failure_reason=str(e),
            )
        except Exception:
            logger.error(f"[reports.execute] could not even mark run {run_id} failed", exc_info=True)
