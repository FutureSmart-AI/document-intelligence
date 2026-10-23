"""RAG node for the Knowledge Service's own chat graph.

Ported from fs-agent-chat/agents/nodes/rag.py — the densest Agent-Registry
cluster in the whole port. Deleted entirely, per
claude-logs/plan/knowledge-owned-chat.md's confirmed Phase-3 design:
  - _extract_knowledge_id — knowledge_id is now a REQUIRED plain parameter,
    never resolved from an enabled_services list. Tenancy-critical: see the
    plan's "Tenancy — non-negotiable" section.
  - _resolve_rag_llm_config / _resolve_generate_answer_llm_config /
    _resolve_generate_answer_instructions — replaced with
    chat/config.py::resolve_chat_config's 4-level chain (Phase 3d): request
    field (no caller populates this yet — Phase 5's endpoint will) >
    knowledge_chat_config DB row > env default > hardcoded default.
  - The streaming gate's three planner-field checks (dispatch_size /
    needs_followup / dispatch_rounds) — this service has no planner, so
    streaming is unconditional whenever a token queue is passed in.
  - RAG_DIRECT_ANSWER_MODE — removed (2026-08-11). The answer node
    (generate_rag_answer) always runs; there is no separate rephrase_answer
    node in this graph to fall back to, so making it optional was a latent
    footgun, not a real toggle. See claude-logs/plan/chat-migration/plan.md
    "Answer-node gates — remove both".
"""
from __future__ import annotations

import asyncio as _asyncio
import os
import time as _time
from typing import Any, Dict, List, Optional

from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree

from chat.byok_fallback import ByokKeyRejected
from chat.utils.service_calls import build_service_call
from utils.logger import logger
from utils.tracing import track_time


@track_time("Node: RAG")
async def rag_node(
    query: str,
    knowledge_id: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    past_messages: Optional[List[Dict[str, Any]]] = None,
    token_queue: Optional[Any] = None,
    request_chat_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Invoke the RAG sub-agent and map its output to the response shape the
    endpoint (and, transitively, the Orchestrator's thin HTTP client) expects.

    knowledge_id is REQUIRED and never resolved from anywhere else — every
    retrieval call below carries it, per the plan's tenancy section: a
    master-key caller bypasses RBAC entirely, so the knowledge_id payload
    filter in the retrieval adapter is the ONLY thing separating one tenant's
    chunks from another's.

    request_chat_config carries level-1 (request-body) overrides for
    chat/config.py::resolve_chat_config, keyed BY NODE:
    {"rag_agent": {...}, "generate_answer": {...}}, each inner dict holding
    any subset of {"provider", "model", "temperature", "system_prompt"}.
    This node drives both of those roles and resolves them independently —
    the retrieval ReAct loop and the answer writer may legitimately run
    different models with different prompts. Keys for other nodes (e.g.
    refined_query_node) are ignored here; chat/graph.py routes those to their
    own consumer. None (the default) means no caller-supplied override
    anywhere — resolution falls through to the DB row / env / hardcoded
    levels exactly as before this param existed.
    """
    logger.info(f"[rag_node] Invoking RAG subagent for query: {query[:80]}...")

    # 4-level provider/model/temperature/system_prompt resolution (Phase 3d).
    # request_chat_config is level 1 — populated by routers/chat.py's
    # ChatRequest fields when the caller supplies them.
    from chat.config import resolve_chat_config

    # Two INDEPENDENT resolutions, one per role this node drives. Both fall
    # through the same DB-row/env/hardcoded levels, so an agent that
    # configures neither still gets identical values for both (the previous
    # behaviour) — but one that configures them differently now keeps the
    # difference instead of having generate_answer overwritten by rag_agent.
    _rcc = request_chat_config or {}
    _rag_rcc = _rcc.get("rag_agent") or {}
    _ans_rcc = _rcc.get("generate_answer") or {}

    chat_config = await resolve_chat_config(
        knowledge_id,
        request_provider=_rag_rcc.get("provider"),
        request_model=_rag_rcc.get("model"),
        request_temperature=_rag_rcc.get("temperature"),
        request_system_prompt=_rag_rcc.get("system_prompt"),
        node_name="rag_agent",
    )
    llm_provider = chat_config.llm_provider
    llm_model = chat_config.llm_model
    user_instructions = chat_config.system_prompt
    # chat_config.temperature is resolved but NOT threaded downstream — every
    # get_llm call site in the RAG chain (create_rag_agent_graph,
    # generate_rag_answer, retrieval_verifier) still hardcodes temperature=0,
    # and run_rag_agent has no temperature parameter to carry it through.
    # Wiring it would touch several function signatures beyond this node and
    # remains a follow-up; the request schema accepts temperature per node so
    # that doing so later is not a breaking change. show_sources/chat_enabled
    # are consumed by routers/chat.py (404 on disabled chat, evidence
    # stripping on the response), not here.

    # The RAG sub-agent's answer-generation node always writes the final
    # user-facing answer; there is no separate generate_answer /
    # rephrase_answer node in this graph to hand off to (deleted — see the
    # plan's "Two simplifications" section).

    # The answer writer's own config. Previously this was hardcoded to
    # `= llm_provider, llm_model` with instructions pinned to None, which
    # made the generate_answer node unconfigurable: an agent whose answer
    # writer differed from its retrieval loop silently ran both on the
    # retrieval model, and any answer-formatting prompt was discarded. That
    # is not a rare edge — 27 of the 52 production agents holding
    # node_configs give the two roles different prompts, and 14 give them
    # different models.
    #
    # Resolved through the SAME 4-level chain rather than defaulted to the
    # rag_agent values directly, so an unset generate_answer still picks up
    # the knowledge_chat_config row / env defaults on its own terms. In the
    # common case where nothing is configured for either, both resolutions
    # land on the same values and behaviour is unchanged.
    answer_chat_config = await resolve_chat_config(
        knowledge_id,
        request_provider=_ans_rcc.get("provider"),
        request_model=_ans_rcc.get("model"),
        request_temperature=_ans_rcc.get("temperature"),
        request_max_tokens=_ans_rcc.get("max_tokens"),
        request_system_prompt=_ans_rcc.get("system_prompt"),
        node_name="generate_answer",
    )
    answer_llm_provider = answer_chat_config.llm_provider
    answer_llm_model = answer_chat_config.llm_model
    # Unlike temperature, max_tokens IS threaded all the way to the answer
    # node's get_llm call (chat/rag_agent/answer_node.py::generate_rag_answer).
    answer_max_tokens = answer_chat_config.max_tokens
    # Answer instructions come from generate_answer's OWN config only —
    # request-level first, then this node's knowledge_chat_node_config row.
    #
    # Deliberately NOT answer_chat_config.system_prompt: that resolution falls
    # through to the knowledge-level row's system_prompt, which is the RAG
    # answer suffix already carried by user_instructions above. Using the
    # resolved value here would append the same text twice to one generated
    # answer. The node row's prompt is read directly because it is specific to
    # this node and has no such overlap.
    from chat.config import _get_node_config_row
    _ans_node_row = await _get_node_config_row(knowledge_id, "generate_answer")
    generate_answer_instructions = (
        _ans_rcc.get("system_prompt")
        or (_ans_node_row.prompt if _ans_node_row else None)
    )

    # include_charts (Settings' "include charts when useful" toggle) has no
    # request-level or per-node override — knowledge-level policy only, same
    # posture as show_sources/follow_up. Default (True) leaves the answer
    # node's existing organic behaviour untouched — it already reaches for a
    # markdown table when that's the clearest way to present something.
    # False appends an explicit suppression directive; this shapes the prose
    # answer only. The structured chart_output field on ChatResponse (G16,
    # closed) is produced separately by chat/chart_node.py, which runs after
    # this node and resolves include_charts again on its own — see that
    # module's docstring.
    if not answer_chat_config.include_charts:
        no_charts_directive = (
            "Do not format the answer as a markdown table, chart, or other "
            "tabular/chart-like structure — respond in plain prose only."
        )
        generate_answer_instructions = (
            f"{generate_answer_instructions}\n\n{no_charts_directive}"
            if generate_answer_instructions
            else no_charts_directive
        )

    # CRAG-style retrieval verifier toggle. Defaults ON; ops can disable via
    # env var when debugging or when latency budget is tight. max_retries
    # caps how many extra retrieval passes the verifier can trigger (1 = up
    # to 2 total passes). Kept verbatim from the donor.
    enable_retrieval_verifier = os.environ.get(
        "RAG_RETRIEVAL_VERIFIER_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    try:
        max_retrieval_retries = int(
            os.environ.get("RAG_RETRIEVAL_VERIFIER_MAX_RETRIES", "1")
        )
    except ValueError:
        max_retrieval_retries = 1
    if max_retrieval_retries < 0:
        max_retrieval_retries = 0

    # Verifier LLM — decoupled from the answer-node model. The verifier's job
    # is a quick sufficiency judgement, not full answer generation, so a
    # small fast model is a good fit. Defaults to openai/gpt-5.4-nano; ops
    # can override via env.
    verifier_llm_provider = os.environ.get(
        "RAG_RETRIEVAL_VERIFIER_PROVIDER", "openai"
    ).strip() or None
    verifier_llm_model = os.environ.get(
        "RAG_RETRIEVAL_VERIFIER_MODEL", "gpt-5.4-nano"
    ).strip() or None

    # Prefetch lightweight metadata schema (in-memory TTL cache).
    metadata_schema = await _fetch_schema(knowledge_id)

    # Small KB direct mode: bypass the agentic ReAct loop (no
    # retrieve_documents/get_document_details tool calls) for tiny knowledge
    # bases, but still run the answer node on the retrieved chunks — same
    # citation filtering, style directives, and streaming as the normal path.
    direct_result = await _try_small_kb_direct(
        query,
        knowledge_id,
        metadata_schema,
        answer_llm_provider=answer_llm_provider,
        answer_llm_model=answer_llm_model,
        answer_max_tokens=answer_max_tokens,
        generate_answer_instructions=generate_answer_instructions,
        owner_id=owner_id,
        session_id=session_id,
        past_messages=past_messages,
        token_queue=token_queue,
    )
    if direct_result is not None:
        return direct_result

    # Load prior-turn evidence (last N valid turns that retrieved docs) so
    # the agent can reuse documents already in play this session and scope
    # follow-up retrieval to them. Guidance only — the agent can always
    # retrieve fresh.
    #
    # get_recent_evidence loads by recency alone (no notion of the current
    # query), so an UNRELATED follow-up would otherwise be handed the
    # previous query's docs and reuse them. Relevance-gate the loaded groups
    # against the current query before injection: legitimate follow-ups
    # share the prior topic and pass; a genuinely new question drops the
    # stale block to empty so the agent retrieves fresh. Fail-open — see
    # chat.evidence_relevance.
    prior_evidence: list = []
    # Evidence-gate counts, kept for the RAG-eval Tier 0 signal: how much
    # recency-loaded evidence survived the relevance gate. Both stay None when
    # the session loaded no prior evidence at all — which is a different fact
    # from "loaded three groups and dropped all three".
    evidence_groups_loaded: Optional[int] = None
    evidence_groups_kept: Optional[int] = None
    if session_id:
        try:
            from chat.db.chat_logs import get_recent_evidence
            from utils.setup_env import RAG_RECENT_EVIDENCE_TURNS

            prior_evidence = await get_recent_evidence(session_id, RAG_RECENT_EVIDENCE_TURNS)
            if prior_evidence:
                logger.info(f"[rag_node] Loaded {len(prior_evidence)} prior-evidence group(s)")
                from chat.evidence_relevance import filter_evidence_by_relevance

                evidence_groups_loaded = len(prior_evidence)
                prior_evidence = await filter_evidence_by_relevance(query, prior_evidence)
                evidence_groups_kept = len(prior_evidence)
                logger.info(
                    f"[rag_node] {len(prior_evidence)} prior-evidence group(s) "
                    f"relevant to current query after gating"
                )
        except Exception as e:
            logger.warning(f"[rag_node] Failed to load prior evidence: {e}")

    # ── Live answer streaming gate ────────────────────────────────────────
    # This service has no planner and no multi-service fan-out (unlike the
    # donor's Orchestrator, where the gate had to prove RAG was the SOLE
    # producer for the whole turn before it was safe to stream). RAG is
    # always the sole producer here, so the three planner-field checks
    # (dispatch_size/needs_followup/dispatch_rounds) are dropped entirely.
    # Streaming is now unconditional: it fires whenever a token queue was
    # passed in — the answer node always runs, so there's always something
    # to stream.
    answer_token_queue = None
    if token_queue is not None:
        answer_token_queue = token_queue
        logger.info("[rag_node] Streaming RAG answer tokens live")

    try:
        from chat.rag_agent.agent_execution import run_rag_agent

        context = await run_rag_agent(
            query=query,
            knowledge_id=knowledge_id,
            user_id=user_id,
            llm_provider=llm_provider,
            llm_model=llm_model,
            user_instructions=user_instructions,
            owner_id=owner_id,
            metadata_schema=metadata_schema,
            past_messages=past_messages,
            prior_evidence=prior_evidence,
            generate_answer_instructions=generate_answer_instructions,
            answer_llm_provider=answer_llm_provider,
            answer_llm_model=answer_llm_model,
            answer_max_tokens=answer_max_tokens,
            enable_retrieval_verifier=enable_retrieval_verifier,
            max_retrieval_retries=max_retrieval_retries,
            verifier_llm_provider=verifier_llm_provider,
            verifier_llm_model=verifier_llm_model,
            answer_token_queue=answer_token_queue,
        )

        # Log usage. key_type is threaded through as a parameter rather than
        # re-derived from an owner_credentials dict (that dict no longer
        # exists in this repo's call chain) — see _log_usage's docstring.
        # run_rag_agent surfaces the key_type that create_rag_agent_graph
        # resolved internally via get_llm (chat/rag_agent/agent_execution.py:
        # retrieved_context["key_type"] = key_type); falls back to
        # "platform-key" only if that key is ever absent (e.g. the
        # exception-path return shape, which has no LLM call to report).
        _log_usage(
            context, llm_provider, llm_model, owner_id, knowledge_id, session_id,
            key_type=context.get("key_type"),
        )

        # Surface ONLY the evidence the retrieval agent actually used to
        # ground its answer.
        summaries = context.get("summaries", {})
        chunks_list = context.get("chunks", [])
        summaries, chunks_list = _filter_to_cited(
            summaries, chunks_list, context.get("agent_output") or {}
        )

        normalized_summaries = _normalize_summaries(summaries)
        detailed_chunks = _group_chunks(chunks_list)
        relevant_context = _build_relevant_context(normalized_summaries, chunks_list, context)

        relevant_sources = {
            "data": [],
            "tool_name": "rag",
        }
        for doc_id, summary_data in normalized_summaries.items():
            src = summary_data if isinstance(summary_data, dict) else {"summary": str(summary_data)}
            relevant_sources["data"].append({
                "id": doc_id,
                "title": src.get("file_name") or src.get("url") or doc_id,
                "context": src.get("summary", ""),
            })

        # Surface the RAG agent's GENUINE self-eval (the answer node's
        # RagAnswerOutput.is_answered, in rag_context) as this service's
        # verdict — no extra LLM call, RAG already judged it.
        rag_self_eval = context.get("is_answered")
        if rag_self_eval is None:
            is_answered = "yes" if (normalized_summaries or chunks_list) else "no"
        else:
            is_answered = "yes" if rag_self_eval else "no"

        logger.info(
            f"[rag_node] Retrieved {len(normalized_summaries)} summaries, "
            f"{len(chunks_list)} chunks across {len(detailed_chunks)} documents, "
            f"is_answered={is_answered}"
        )

        # The agent's own confidence / stop_reason, forwarded to graph state so
        # the health event can record them (see chat/graph.py's
        # KnowledgeChatState). agent_output is absent when the sub-agent emitted
        # no parseable structured output for this turn — leave both None rather
        # than substituting a default, so "the agent did not report" stays
        # distinguishable from "the agent reported low".
        agent_output = context.get("agent_output") or {}

        # RAG eval Tier 0: the free structural signals, computed only for owners
        # in RAG_EVAL_OWNER_IDS (empty by default, so this is inert until an
        # owner is named). Wrapped because a metric that only observes must never
        # fail a chat turn — the same fail-open posture as the verifier and the
        # evidence gate.
        rag_eval_tier0: Dict[str, Any] = {}
        try:
            from utils.knowledge_health.rag_eval import rag_eval_enabled_for

            if rag_eval_enabled_for(owner_id):
                context["evidence_groups_loaded"] = evidence_groups_loaded
                context["evidence_groups_kept"] = evidence_groups_kept
                rag_eval_tier0 = _traced_compute_tier0(context)
                logger.info(
                    f"[rag_node] RAG eval tier0: "
                    f"retrieved={rag_eval_tier0.get('retrieved_chunk_count')} chunks "
                    f"cited={rag_eval_tier0.get('cited_chunk_count')} "
                    f"attempts={rag_eval_tier0.get('retrieval_attempts')} "
                    f"verifier={rag_eval_tier0.get('verifier_verdict')}"
                )
        except Exception as e:
            logger.warning(f"[rag_node] RAG eval tier0 skipped: {type(e).__name__}: {e}")

        result: Dict[str, Any] = {
            "relevant_context": relevant_context,
            "relevant_sources": relevant_sources,
            "summaries": normalized_summaries,
            "detailed_chunks": detailed_chunks,
            "rag_context": context,
            "is_answered": is_answered,
            "confidence": agent_output.get("confidence"),
            "stop_reason": agent_output.get("stop_reason"),
            "rag_eval_tier0": rag_eval_tier0,
            "services_used": ["knowledge"],
            "error": None,
        }

        # The answer node's output IS the final response.
        final_answer = (context.get("raw_output") or "").strip()
        if final_answer:
            result["response"] = final_answer
            result["is_direct_response"] = True
            logger.info(f"[rag_node] Emitting final answer ({len(final_answer)} chars)")
        else:
            logger.warning("[rag_node] Answer node produced no final message")

        # Agent Health node breakdown — agent_execution.py's run_rag_agent
        # already tags the retrieval loop ("rag_agent") and the answer writer
        # ("generate_answer") as separate service_calls/usage_by_model entries
        # (they may run different models), timed independently at their own
        # end. Forward both verbatim rather than re-aggregating into one
        # combined "rag" entry here, which used to collapse the two roles
        # into a single service_call AND — because this function never read
        # context["usage_by_model"] back out — silently dropped RAG/answer
        # tokens from graph state's usage_by_model entirely (only the query
        # refiner's tokens ever reached knowledge_health_events.cost_usd on a
        # real chat turn; see docs/plan/analytics-and-health/state.md).
        result["usage_by_model"] = context.get("usage_by_model") or []
        result["service_calls"] = context.get("service_calls") or []
        return result

    except ByokKeyRejected:
        # Propagate — see the matching guard in
        # chat/rag_agent/agent_execution.py::run_rag_agent for why this must reach
        # routers/chat.py as a real exception, not this function's usual
        # error-dict shape.
        raise
    except Exception as e:
        logger.error(f"[rag_node] Failed: {e}", exc_info=True)
        return {
            "relevant_context": "",
            "relevant_sources": {},
            "error": f"rag_node failed: {str(e)}",
        }


# ── Internal helpers ──────────────────────────────────────────────────────

def _filter_to_cited(summaries: dict, chunks: list, agent_output: dict) -> tuple[dict, list]:
    """Restrict retrieved summaries/chunks to what the retrieval agent CITED as
    grounding its answer (``agent_output.relevant_docs``), so the sources shown
    to the user are the ones actually used — not everything retrieved.

    Doc-level and reliable: any document the agent didn't cite is dropped.
    Chunk-level is lenient — a chunk of a cited doc is kept even if its
    specific id wasn't listed (chunk-id rendering varies by backend); this
    still removes every chunk of a non-cited doc. Fallbacks (never blank the
    sources panel): return the inputs unchanged when the agent cited nothing
    or when the citation ids matched no retrieved evidence.
    """
    try:
        from chat.rag_agent.answer_node import (
            _cited_ids_from_agent_output,
            _chunk_matches_citation,
        )

        cited_doc_ids, cited_chunk_ids = _cited_ids_from_agent_output(agent_output)
        if not cited_doc_ids:
            return summaries, chunks  # no citations → keep everything (fallback)

        filtered_summaries = {
            oid: data for oid, data in (summaries or {}).items()
            if str(oid) in cited_doc_ids
        }
        filtered_chunks = [
            c for c in (chunks or [])
            if _chunk_matches_citation(c, cited_doc_ids, cited_chunk_ids)
        ]
        if not filtered_summaries and not filtered_chunks:
            return summaries, chunks

        dropped_docs = len(summaries or {}) - len(filtered_summaries)
        dropped_chunks = len(chunks or []) - len(filtered_chunks)
        if dropped_docs or dropped_chunks:
            logger.info(
                "[rag_node] cited-sources filter: keeping %d doc(s) + %d chunk(s) "
                "(dropped %d uncited doc(s), %d uncited chunk(s))",
                len(filtered_summaries), len(filtered_chunks), dropped_docs, dropped_chunks,
            )
        return filtered_summaries, filtered_chunks
    except Exception as e:  # never let source-trimming break the RAG response
        logger.warning("[rag_node] cited-sources filter failed, showing all: %s", e)
        return summaries, chunks


# ── LangSmith spans for the small-KB direct path ─────────────────────────
# The direct path does its retrieval with plain adapter calls (no LangChain
# runnable, no ReAct agent), and LangSmith only records LangChain runs or
# @traceable functions. Without these wrappers a direct-mode turn showed a
# `rag` node whose only child was `rag_answer_node`, with the retrieval
# seconds unattributed — the whole reason this instrumentation exists.
#
# Span payloads are deliberately summaries (counts, ids, sizes), never chunk
# or document bodies: a small-KB turn pulls up to 100 chunks, and shipping
# their text to LangSmith on every request bloats the trace and copies
# customer document content into a third-party store. Latency and fan-out
# are what these spans are here to show.


def _chunk_span_outputs(chunks: Any) -> Dict[str, Any]:
    """Summarize a chunk-search result for the trace: how many chunks came
    back, from how many distinct documents."""
    items = chunks if isinstance(chunks, list) else []
    doc_ids = {
        c.get("document_id") or c.get("object_id")
        for c in items
        if isinstance(c, dict)
    }
    doc_ids.discard(None)
    return {
        "chunk_count": len(items),
        "document_count": len(doc_ids),
        "document_ids": sorted(str(d) for d in doc_ids)[:20],
    }


def _doc_details_span_outputs(details: Any) -> Dict[str, Any]:
    """Summarize the doc-detail batch fetch: how many documents were
    enriched, and how many of those carried a summary."""
    items = details if isinstance(details, list) else []
    return {
        "document_count": len(items),
        "with_summary": sum(
            1 for d in items if isinstance(d, dict) and (d.get("summary") or "").strip()
        ),
    }


def _direct_span_inputs(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only the fields worth seeing on the direct-mode span. Drops the
    streaming queue (an asyncio.Queue, not serializable) and conversation
    history (large, and already visible on the answer node's own span)."""
    return {
        "query": inputs.get("query"),
        "knowledge_id": inputs.get("knowledge_id"),
        "doc_count": inputs.get("doc_count"),
    }


def _direct_span_outputs(result: Any) -> Dict[str, Any]:
    """Summarize the direct path's return dict — the full dict carries every
    retrieved chunk plus the answer text, which the child spans already
    account for."""
    if not isinstance(result, dict):
        return {"result": None}
    chunks = (result.get("rag_context") or {}).get("chunks") or []
    return {
        "chunk_count": len(chunks) if isinstance(chunks, list) else 0,
        "document_count": len(result.get("summaries") or {}),
        "is_answered": result.get("is_answered"),
        "answer_chars": len(result.get("response") or ""),
    }


def _tier0_span_inputs(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Trim the span's inputs to counts.

    `rag_context` carries every retrieved chunk's text plus the summaries, and
    the sibling retriever spans already uploaded all of it. Recording it again
    here would copy the whole retrieved corpus into the trace store a second
    time for a scorer that only counts things — the `process_inputs` rule from
    the langchain skill's multi-tier-evaluator section, which measured a 35,897
    to 325 char drop on the extraction judge.
    """
    ctx = inputs.get("rag_context") or {}
    return {
        "chunks_in_context": len(ctx.get("chunks") or []),
        "summaries_in_context": len(ctx.get("summaries") or {}),
        "evidence_groups_loaded": ctx.get("evidence_groups_loaded"),
        "evidence_groups_kept": ctx.get("evidence_groups_kept"),
    }


@traceable(name="rag_eval_tier0", run_type="tool",
           tags=["rag-eval", "tier0"], process_inputs=_tier0_span_inputs)
def _traced_compute_tier0(rag_context: Dict[str, Any]) -> Dict[str, Any]:
    """`compute_tier0` with a span, so the free tier is visible in the turn's trace.

    Tier 0 makes no LLM call, and LangSmith auto-traces Runnables only — so
    without this wrapper the deterministic tier is invisible in `KnowledgeChat`
    beside the paid one, including on the turns where it is the tier that
    explains the answer. The scorer itself stays pure and untraced in
    `utils/knowledge_health/rag_eval.py` so it remains unit-testable.

    `context_utilization` is stamped on the span but deliberately NOT stored as a
    column: it is cited/retrieved, derivable at read time, and a stored copy is
    one more thing to keep consistent. Here it earns its place because a reader
    scanning the trace should not have to divide two numbers in their head.
    """
    from utils.knowledge_health.rag_eval import compute_tier0, context_utilization

    result = compute_tier0(rag_context)
    rt = get_current_run_tree()
    if rt is not None:
        util = context_utilization(
            result.get("cited_chunk_count"), result.get("retrieved_chunk_count")
        )
        rt.metadata.update({
            "tier": "tier0",
            **result,
            # None, not 0.0, when nothing was retrieved — no retrieval is not
            # zero utilization, and the trace must not imply otherwise.
            "context_utilization": util,
        })
    return result


@traceable(name="chunk_search", run_type="retriever", process_outputs=_chunk_span_outputs)
async def _traced_search_chunks(
    knowledge_id: str, query: str, num_chunks: int, mode: str
) -> List[dict]:
    """``search_chunks`` with a LangSmith span around it."""
    from chat.knowledge_adapter import search_chunks

    return await search_chunks(
        knowledge_id=knowledge_id, query=query, num_chunks=num_chunks, mode=mode
    )


@traceable(name="doc_details", run_type="retriever", process_outputs=_doc_details_span_outputs)
async def _traced_get_document_details(
    knowledge_id: str, object_ids: List[str]
) -> List[dict]:
    """``get_document_details`` with a LangSmith span around it."""
    from chat.knowledge_adapter import get_document_details

    return await get_document_details(knowledge_id=knowledge_id, object_ids=object_ids)


@track_time("Node: RAG Direct (Small KB)")
async def _try_small_kb_direct(
    query: str,
    knowledge_id: Optional[str],
    metadata_schema: dict,
    *,
    answer_llm_provider: Optional[str] = None,
    answer_llm_model: Optional[str] = None,
    answer_max_tokens: Optional[int] = None,
    generate_answer_instructions: Optional[str] = None,
    owner_id: Optional[str] = None,
    session_id: Optional[str] = None,
    past_messages: Optional[List[Dict[str, Any]]] = None,
    token_queue: Optional[Any] = None,
) -> Dict[str, Any] | None:
    """Bypass the agentic ReAct loop for small knowledge bases, but still run
    the answer node — same citation filtering, style directives, and
    streaming as the normal path gets.

    Returns a full rag_node result dict when direct mode fires, or None to
    fall through to the normal ReAct agent path.

    This function is only the gate — the work lives in ``_small_kb_direct``,
    which carries the LangSmith span. Keeping the gate untraced means an
    agentic turn (KB above the threshold) emits no empty direct-mode span.
    """
    from utils.setup_env import SMALL_KB_DIRECT_MODE, SMALL_KB_DOC_THRESHOLD

    if not SMALL_KB_DIRECT_MODE or not knowledge_id:
        return None

    doc_count = metadata_schema.get("document_count", -1)
    if doc_count < 0 or doc_count > SMALL_KB_DOC_THRESHOLD:
        return None

    logger.info(
        f"[rag_node] Small KB direct mode: {doc_count} docs "
        f"(<= threshold {SMALL_KB_DOC_THRESHOLD}), skipping agentic loop"
    )

    try:
        return await _small_kb_direct(
            query=query,
            knowledge_id=knowledge_id,
            doc_count=doc_count,
            answer_llm_provider=answer_llm_provider,
            answer_llm_model=answer_llm_model,
            answer_max_tokens=answer_max_tokens,
            generate_answer_instructions=generate_answer_instructions,
            owner_id=owner_id,
            session_id=session_id,
            past_messages=past_messages,
            token_queue=token_queue,
        )
    except Exception as e:
        logger.warning(f"[rag_node] Small KB direct mode failed, falling back to agentic: {e}")
        return None


@traceable(
    name="small_kb_direct",
    run_type="chain",
    process_inputs=_direct_span_inputs,
    process_outputs=_direct_span_outputs,
)
async def _small_kb_direct(
    query: str,
    knowledge_id: str,
    doc_count: int,
    *,
    answer_llm_provider: Optional[str] = None,
    answer_llm_model: Optional[str] = None,
    answer_max_tokens: Optional[int] = None,
    generate_answer_instructions: Optional[str] = None,
    owner_id: Optional[str] = None,
    session_id: Optional[str] = None,
    past_messages: Optional[List[Dict[str, Any]]] = None,
    token_queue: Optional[Any] = None,
) -> Dict[str, Any] | None:
    """The direct path itself: one hybrid chunk search, one batched doc-detail
    fetch, then the answer node — each its own child span under this one.

    Raises on failure; the caller (``_try_small_kb_direct``) catches and falls
    back to the agentic path. Letting it raise is deliberate: the span then
    shows the error in LangSmith instead of an opaque success.
    """
    chunks_list = await _traced_search_chunks(
        knowledge_id=knowledge_id,
        query=query,
        num_chunks=min(doc_count * 5, 100) if doc_count > 0 else 20,
        mode="hybrid",
    )

    detailed_chunks = _group_chunks(chunks_list)

    # This path skips retrieve_documents/get_document_details entirely,
    # so unlike the agentic ReAct path it never learns each doc's real
    # title and structured metadata. Fetch it here in one batch (bounded
    # by SMALL_KB_DOC_THRESHOLD docs) so this fast path produces the same
    # doc-level shape the full agentic path does.
    doc_ids = sorted({
        chunk.get("document_id") or chunk.get("object_id")
        for chunk in chunks_list
        if isinstance(chunk, dict) and (chunk.get("document_id") or chunk.get("object_id"))
    })
    summaries: Dict[str, Any] = {}
    if doc_ids:
        try:
            details = await _traced_get_document_details(
                knowledge_id=knowledge_id, object_ids=doc_ids
            )
            for detail in details or []:
                if not isinstance(detail, dict) or not detail.get("object_id"):
                    continue
                oid = detail["object_id"]
                metadata = detail.get("metadata") if isinstance(detail.get("metadata"), dict) else {}
                entry: Dict[str, Any] = {
                    "summary": detail.get("summary", ""),
                    "metadata": metadata,
                    "object_id": oid,
                }
                file_name = detail.get("file_name") or detail.get("filename") or metadata.get("file_name")
                if file_name:
                    entry["file_name"] = file_name
                summaries[oid] = entry
        except Exception as e:
            logger.warning(f"[rag_node] Small KB direct mode: doc-detail enrichment failed: {e}")

    context: Dict[str, Any] = {
        "raw_output": "",
        "summaries": summaries,
        "chunks": chunks_list,
        "tool_responses": [],
        "usage_metrics": {},
    }

    from chat.rag_agent.answer_node import generate_rag_answer

    _answer_t0 = _time.perf_counter()
    answer_result = await generate_rag_answer(
        query=query,
        retrieved_context=context,
        llm_provider=answer_llm_provider or "openai",
        llm_model=answer_llm_model or "gpt-5.4-mini",
        owner_id=owner_id,
        generate_answer_instructions=generate_answer_instructions,
        past_messages=past_messages,
        token_queue=token_queue,
        max_tokens=answer_max_tokens,
        knowledge_id=knowledge_id,
    )
    _answer_latency_ms = int((_time.perf_counter() - _answer_t0) * 1000)
    final_answer = (answer_result.get("answer") or "").strip()
    context["raw_output"] = final_answer
    context["answer_node_output"] = final_answer
    context["is_answered"] = bool(answer_result.get("is_answered", False))
    context["usage_metrics"] = answer_result.get("usage_metrics") or {}

    relevant_context = _build_relevant_context(summaries, chunks_list, context)

    relevant_sources: Dict[str, Any] = {"data": [], "tool_name": "rag"}
    seen_docs: set[str] = set()
    for chunk in chunks_list:
        if isinstance(chunk, dict):
            doc_id = chunk.get("document_id", chunk.get("object_id"))
            if doc_id and doc_id not in seen_docs:
                seen_docs.add(doc_id)
                doc_summary = summaries.get(doc_id) or {}
                relevant_sources["data"].append({
                    "id": doc_id,
                    "title": doc_summary.get("file_name") or doc_id,
                    "context": doc_summary.get("summary", ""),
                })

    logger.info(
        f"[rag_node] Small KB direct mode retrieved {len(chunks_list)} chunks "
        f"across {len(detailed_chunks)} documents ({len(summaries)} doc(s) enriched), "
        f"is_answered={context['is_answered']}"
    )

    result: Dict[str, Any] = {
        "relevant_context": relevant_context,
        "relevant_sources": relevant_sources,
        "summaries": summaries,
        "detailed_chunks": detailed_chunks,
        "rag_context": context,
        "is_answered": "yes" if context["is_answered"] else "no",
        "services_used": ["knowledge"],
        "error": None,
    }
    if final_answer:
        result["response"] = final_answer
        result["is_direct_response"] = True
    else:
        logger.warning("[rag_node] Small KB direct mode: answer node produced no final message")

    # Agent Health node breakdown — this path skips the ReAct retrieval
    # loop entirely (no "rag_agent" node fires), so "generate_answer" is
    # the only LLM call to report.
    _um = context.get("usage_metrics") or {}
    _in, _out = _um.get("input_tokens", 0) or 0, _um.get("output_tokens", 0) or 0

    # Usage event — MUST happen here too, not only on the agentic path.
    # Direct mode returns early (rag_node's `if direct_result is not None:
    # return direct_result`), so the _log_usage call further down the agentic
    # branch never runs for a small KB. Because SMALL_KB_DOC_THRESHOLD is 15,
    # direct mode is the *common* path — which meant a chat turn on almost any
    # real knowledge base wrote no `llm_generation` row at all. Downstream that
    # made `messages_this_month` and `llm_tokens_this_month` sit at zero while
    # chat was demonstrably working, so max_messages_per_month could never bind
    # and analytics showed no chat usage.
    # Observed live 2026-08-20: a turn on a 1-doc KB streamed 948 tokens and
    # emitted only the embedding event. See
    # docs/plan/usage-and-limits/api-tests.md.
    _log_usage(
        context,
        answer_llm_provider or "openai",
        answer_llm_model or "gpt-5.4-mini",
        owner_id, knowledge_id, session_id,
        key_type=context.get("key_type"),
    )

    if _in or _out:
        _provider = answer_llm_provider or "openai"
        _model = answer_llm_model or "gpt-5.4-mini"
        result["usage_by_model"] = [{
            "provider": _provider, "model": _model,
            "input_tokens": _in, "output_tokens": _out,
        }]
        result["service_calls"] = [build_service_call(
            service="generate_answer", latency_ms=_answer_latency_ms,
            provider=_provider, model=_model,
            input_tokens=_in, output_tokens=_out,
        )]
    return result


_SCHEMA_MEM_CACHE: dict[str, tuple[dict, float]] = {}
_SCHEMA_MEM_TTL = 300  # 5 minutes


async def _fetch_schema(knowledge_id: Optional[str]) -> dict:
    """Fetch or cache the lightweight metadata schema for the knowledge base.

    Uses a simple in-memory dict cache (TTL = 5 min).
    First request per knowledge_id hits the retrieval adapter (~seconds);
    all subsequent requests within the TTL window return instantly.
    A background refresh is triggered when the entry expires.
    """
    if not knowledge_id:
        return {}

    cached_entry = _SCHEMA_MEM_CACHE.get(knowledge_id)
    if cached_entry:
        schema, ts = cached_entry
        age = _time.monotonic() - ts
        if age < _SCHEMA_MEM_TTL:
            logger.info(f"[rag_node] Schema in-memory cache HIT (age={age:.0f}s)")
            return schema
        logger.info("[rag_node] In-memory cache expired, refreshing in background")
        _asyncio.ensure_future(_refresh_schema_in_background(knowledge_id))
        return schema  # serve stale while refreshing

    schema = await _build_schema(knowledge_id)
    _SCHEMA_MEM_CACHE[knowledge_id] = (schema, _time.monotonic())
    return schema


async def _build_schema(knowledge_id: str) -> dict:
    """Build schema from the retrieval adapter (with doc count).

    Runs get_lightweight_schema and get_docs concurrently.
    """
    try:
        from chat.schema import get_lightweight_schema
        from chat.knowledge_adapter import get_docs

        schema_task = get_lightweight_schema(knowledge_id)
        docs_task = get_docs(knowledge_id)
        schema, doc_ids = await _asyncio.gather(schema_task, docs_task, return_exceptions=True)

        if isinstance(schema, BaseException):
            logger.error(f"[rag_node] get_lightweight_schema failed: {schema}")
            schema = {}
        if isinstance(doc_ids, BaseException):
            logger.warning(f"[rag_node] Could not fetch document count: {doc_ids}")
            doc_ids = None

        schema["document_count"] = len(doc_ids) if doc_ids else -1

        logger.info(
            f"[rag_node] Built metadata schema: {len(schema.get('fields', {}))} fields, "
            f"{len(schema.get('enumerable_values', {}))} enumerable, "
            f"document_count={schema.get('document_count')}"
        )
        return schema
    except Exception as e:
        logger.error(f"[rag_node] Failed to build metadata schema: {e}")
        return {}


async def _refresh_schema_in_background(knowledge_id: str):
    """Fire-and-forget: rebuild schema and update in-memory cache."""
    try:
        schema = await _build_schema(knowledge_id)
        _SCHEMA_MEM_CACHE[knowledge_id] = (schema, _time.monotonic())
        logger.info(f"[rag_node] Background schema refresh completed for {knowledge_id}")
    except Exception as e:
        logger.warning(f"[rag_node] Background schema refresh failed for {knowledge_id}: {e}")


def _normalize_summaries(summaries: dict) -> dict:
    normalized = {}
    for doc_id, summary_data in summaries.items():
        if isinstance(summary_data, dict):
            if "metadata" not in summary_data:
                summary_data["metadata"] = {}
            normalized[doc_id] = summary_data
        else:
            normalized[doc_id] = {"summary": str(summary_data), "metadata": {}}
    return normalized


def _group_chunks(chunks_list: list) -> dict:
    grouped = {}
    for chunk in chunks_list:
        if isinstance(chunk, dict):
            doc_id = chunk.get("document_id", chunk.get("object_id", "unknown"))
        else:
            doc_id = "unknown"
        grouped.setdefault(doc_id, []).append(chunk)
    return grouped


def _build_relevant_context(summaries: dict, chunks: list, context: dict) -> str:
    parts = []
    if summaries:
        parts.append("DOCUMENT SUMMARIES:")
        for doc_id, data in summaries.items():
            summary_text = data.get("summary", "") if isinstance(data, dict) else str(data)
            parts.append(f"- [{doc_id}] {summary_text}")

    if chunks:
        parts.append("\nRELEVANT CHUNKS:")
        for idx, chunk in enumerate(chunks[:20]):
            if isinstance(chunk, dict):
                text = chunk.get("text", chunk.get("content", chunk.get("chunk_text", "")))
                doc_id = chunk.get("document_id", chunk.get("object_id", "unknown"))
                parts.append(f"[Chunk {idx+1} | Doc: {doc_id}] {text}")
            else:
                parts.append(f"[Chunk {idx+1}] {chunk}")

    raw_output = context.get("raw_output")
    if raw_output:
        parts.append(f"\nRAG AGENT SUMMARY:\n{raw_output}")

    return "\n".join(parts) if parts else ""


def _log_usage(
    context: dict,
    llm_provider: str,
    llm_model: str,
    owner_id: Optional[str],
    knowledge_id: Optional[str],
    session_id: Optional[str],
    key_type: Optional[str] = None,
) -> None:
    """Fire-and-forget usage event for the RAG sub-agent's aggregate token
    spend. ``key_type`` is threaded through as a parameter — it comes back
    from get_llm's return tuple further up the call chain (run_rag_agent ->
    create_rag_agent_graph), not re-derived from an owner_credentials dict
    (that dict doesn't exist in this repo's call chain; the donor's
    dict-lookup key_type resolution is deleted, not ported)."""
    try:
        from utils.qdrant.usage_tracking import send_llm_usage_event
        from chat.utils.usage import normalize_usage_metrics

        usage_metrics = context.get("usage_metrics", {})
        if not usage_metrics:
            return

        normalized = normalize_usage_metrics(usage_metrics)

        _asyncio.ensure_future(send_llm_usage_event(
            label="rag_agent",
            provider=llm_provider,
            model=llm_model,
            input_tokens=normalized.get("input_tokens", 0),
            output_tokens=normalized.get("output_tokens", 0),
            owner_id=owner_id,
            knowledge_id=knowledge_id,
            session_id=session_id,
            key_type=key_type or "platform-key",
        ))
    except Exception as e:
        logger.warning(f"[rag_node] Failed to log usage: {e}")
