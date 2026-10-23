"""Query refiner node for the Knowledge Service's own chat graph.

Ported from fs-agent-chat/agents/nodes/refiner.py. Rewrites the raw user
query into a self-contained question using recent chat history, so a
follow-up like "tell me more about that" becomes retrievable on its own.

Two deletions from the donor, per claude-logs/plan/knowledge-owned-chat.md
Phase 3's confirmed design:
  - The single-enabled-service skip-refinement shortcut is gone. The donor's
    reasoning ("no routing decision to make for single-tool agents") is
    ALWAYS true here — this service has exactly one capability (RAG), no
    router. Keeping the shortcut would mean refinement never runs at all.
    The no-history / on-failure skips below are kept — those are legitimate
    guards, not the Agent-Registry-specific shortcut.
  - The Agent-Registry prompt-resolution chain (get_node_config,
    get_base_prompt, combine_prompts, get_user_instructions_with_default) is
    gone. Replaced with a single hardcoded module constant, copied from the
    donor's OWN fallback-of-a-fallback (chat_utils/prompts.py::
    get_prompt_query_refiner's hardcoded string) rather than invented fresh.
"""
from __future__ import annotations

import time as _time
from typing import Any, Dict, List, Optional

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from pydantic import BaseModel, Field

from chat.byok_fallback import ByokKeyRejected, run_with_key_fallback
from chat.utils.messages import build_history_messages
from chat.utils.service_calls import build_service_call
from chat.utils.usage import extract_cache_tokens, extract_model_name
from utils.logger import logger
from utils.tracing import track_time

# Hardcoded default — copied verbatim from the donor's own fallback-of-a-
# fallback (chat_utils/prompts.py::get_prompt_query_refiner's hardcoded
# string, used when its own Agent-Registry/admin-DB lookups come up empty).
# No Agent-Registry equivalent exists in this repo, so this is now the ONLY
# prompt source — not a fallback.
QUERY_REFINER_PROMPT = (
    "You are an AI Assistant for Enhanced Questions. Your role is to refine user questions by:\n"
    "1. Analyzing the chat history to identify relevant context  \n"
    "2. Understanding the user's current question  \n"
    "3. Creating a standalone question that:  \n"
    "   - Incorporates relevant context from chat history only if necessary  \n"
    "   - Maintains the original intent of the question exactly  \n"
    "   - Is clear and complete without requiring chat history to understand  \n"
    "\n"
    "Guidelines:\n"
    "- Only include context that is directly relevant to the current question  \n"
    "- Maintain the same language as the original question  \n"
    "- Keep the refined question concise but complete  \n"
    "- Do NOT answer the question, only refine it\n"
    "- Do NOT add extra information, requirements, or details that the user did not ask for\n"
    "- Do NOT include phrases like \"Please include...\", \"Please provide...\", or similar requests\n"
)


class RefinedQuery(BaseModel):
    refined_query: str = Field(
        description="The refined, standalone question that incorporates relevant context"
    )


def _usage_from_message(msg: Any) -> tuple[int, int, int, int]:
    """(input, output, cached_input, cache_write) tokens from a LangChain
    AIMessage's usage_metadata; (0,0,0,0) if absent. cached_input/cache_write are
    the cache-read/creation subsets of input, for cost pricing."""
    um = getattr(msg, "usage_metadata", None)
    if isinstance(um, dict):
        i = um.get("input_tokens", um.get("prompt_tokens", 0)) or 0
        o = um.get("output_tokens", um.get("completion_tokens", 0)) or 0
        cached, write = extract_cache_tokens(um)
        return int(i), int(o), cached, write
    return 0, 0, 0, 0


async def _run_refinement_chain(
    prompt, query: str, history_messages: list,
    owner_id: Optional[str], knowledge_id: Optional[str], session_id: Optional[str],
    **get_llm_kwargs,
) -> tuple[str, dict]:
    """Execute the refinement chain, log usage, and return (refined_query, usage_entry).

    ``usage_entry`` is ``{"provider","model","input_tokens","output_tokens"}`` for
    the per-model cost breakdown (in/out are 0 when usage metadata is unavailable;
    a 0-token entry is harmless — it's dropped during cost aggregation).

    Builds the LLM itself (from ``get_llm_kwargs``) rather than taking one, because
    a rejected BYOK key has to be retried on a freshly built model — see
    chat/byok_fallback.py. The provider/key_type used for the usage event come back
    from the outcome, so a call served by the platform-key retry is metered as
    ``platform-key`` and not as the owner's.
    """
    # The LLM that actually produced the result — needed for extract_model_name
    # below, which reads the instance, and which may be the retry's model rather
    # than the one built first.
    used: dict = {}

    async def _invoke(llm):
        used["llm"] = llm
        # include_raw=True keeps the underlying AIMessage so we can read usage_metadata
        # (plain structured output alone would log all-zero tokens).
        chain = prompt | llm.with_structured_output(RefinedQuery, include_raw=True)
        return await chain.ainvoke({"query": query, "messages": history_messages})

    outcome = await run_with_key_fallback(
        _invoke, owner_id=owner_id, knowledge_id=knowledge_id,
        source="query_refinement", **get_llm_kwargs,
    )
    result = outcome.value
    llm = used["llm"]
    provider, key_type = outcome.provider, outcome.key_type

    parsed = result.get("parsed") if isinstance(result, dict) else None
    raw = result.get("raw") if isinstance(result, dict) else None
    refined = parsed.refined_query if parsed is not None else query
    in_tok, out_tok, cached_tok, write_tok = _usage_from_message(raw)

    try:
        model_name = extract_model_name(llm, provider=provider)
    except Exception:
        model_name = getattr(llm, "model_name", None) or getattr(llm, "model", None)

    try:
        from utils.qdrant.usage_tracking import send_llm_usage_event
        await send_llm_usage_event(
            label="query_refinement",
            provider=provider,
            model=model_name,
            input_tokens=in_tok,
            output_tokens=out_tok,
            owner_id=owner_id,
            knowledge_id=knowledge_id,
            session_id=session_id,
            key_type=key_type,
        )
    except Exception as e:
        logger.warning(f"[refiner] Failed to log usage: {e}")

    usage_entry = {
        "provider": provider, "model": model_name,
        "input_tokens": in_tok, "output_tokens": out_tok,
    }
    if cached_tok:
        usage_entry["cached_input_tokens"] = cached_tok
    if write_tok:
        usage_entry["cache_write_tokens"] = write_tok
    return refined, usage_entry


@track_time("Node: Query Refiner")
async def query_refiner_node(
    query: str,
    past_messages: Optional[List[Dict[str, Any]]],
    owner_id: Optional[str],
    knowledge_id: Optional[str] = None,
    session_id: Optional[str] = None,
    request_chat_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Rewrite the raw user query into a self-contained question.

    Plain-parameter port of the donor's query_refiner_node(state, config) —
    knowledge_id/session_id/owner_id come from the caller (chat/graph.py),
    never from an Orchestrator-style cfg resolution chain. Parameter naming
    matches chat/rag_agent/agent_execution.py::run_rag_agent for consistency
    across the two node functions chat/graph.py calls.

    request_chat_config carries level-1 (request-body) overrides for
    chat/config.py::resolve_chat_config — THIS NODE'S SLICE ONLY
    ({"provider", "model", "temperature", "system_prompt"}, any subset),
    already selected by chat/graph.py from the per-node map keyed
    "refined_query_node". It is not the whole map; this node never sees
    another node's config.

    Only "provider"/"model" are consumed — this node's own rewriting prompt
    is QUERY_REFINER_PROMPT, not a caller-supplied suffix (which is the RAG
    *answer* prompt, see chat/rag_node.py), so a "system_prompt" override in
    the dict is intentionally not applied here.

    Returns {"refined_query": ...} always; on a successful LLM call also
    {"usage_by_model": [...], "service_calls": [...]}.
    """
    past_messages = past_messages or []

    if not past_messages:
        logger.info("[refiner] No history — skipping refinement")
        return {"refined_query": query}  # no LLM call → no service_calls record

    _t0 = _time.perf_counter()

    human_message_indices = [
        i for i, msg in enumerate(past_messages) if msg.get("role") == "user"
    ]
    if len(human_message_indices) > 25:
        past_messages = past_messages[human_message_indices[-25]:]

    history_messages = build_history_messages(past_messages)

    prompt = ChatPromptTemplate.from_messages([
        ("system", QUERY_REFINER_PROMPT),
        MessagesPlaceholder(variable_name="messages"),
        ("human", "{query}"),
    ])

    # Provider/model resolved through chat/config.py's 4-level chain (Phase
    # 3d): request field (no caller populates this yet) > knowledge_chat_config
    # DB row > env default > hardcoded default. NOT the same as
    # QUERY_REFINER_PROMPT above — knowledge_chat_config.system_prompt is the
    # RAG *answer* prompt suffix (see chat/rag_node.py's use of it as
    # user_instructions), a different setting from this node's own rewriting
    # instructions, so it is deliberately not substituted into the prompt here.
    from chat.config import resolve_chat_config

    _rcc = request_chat_config or {}
    resolved = await resolve_chat_config(
        knowledge_id,
        request_provider=_rcc.get("provider"),
        request_model=_rcc.get("model"),
        node_name="refined_query_node",
    ) if knowledge_id else None
    refiner_provider = resolved.llm_provider if resolved else None
    refiner_model = resolved.llm_model if resolved else None

    # Try primary LLM.
    try:
        # for_function_calling=True: the chain below uses with_structured_output
        # (function tools), and OpenAI 400s when reasoning_effort is combined with
        # function tools for GPT-5 on /v1/chat/completions.
        refined, usage_entry = await _run_refinement_chain(
            prompt, query, history_messages, owner_id, knowledge_id, session_id,
            provider=refiner_provider, model=refiner_model, temperature=0.0,
            for_function_calling=True,
        )
        logger.info(f"[refiner] '{query}' -> '{refined}'")
        return {"refined_query": refined, "usage_by_model": [usage_entry],
                "service_calls": [build_service_call(
                    service="refiner", latency_ms=int((_time.perf_counter() - _t0) * 1000),
                    provider=usage_entry.get("provider"), model=usage_entry.get("model"),
                    input_tokens=usage_entry.get("input_tokens", 0),
                    output_tokens=usage_entry.get("output_tokens", 0))]}

    except ByokKeyRejected:
        # The owner's key is dead AND they are over their limit, so the platform key
        # may not cover for them. Retrying on a different MODEL would resolve the
        # same rejected key again, so let this propagate — the router turns it into
        # an actionable message instead of silently degrading to the raw query.
        raise
    except Exception as e:
        logger.warning(f"[refiner] Primary LLM failed ({e}), retrying with gpt-5.4-mini fallback")

    # Fallback: retry get_llm with a hardcoded safe model. The donor built a
    # standalone ChatOpenAI here (its own owner-key lookup via
    # chat_utils.gpt_utils::get_owner_api_key); that lookup is superseded by
    # get_llm's own owner-credential resolution (chat/credentials.py), so a
    # second _build_fallback_llm helper would just re-implement what get_llm
    # already does — simplified to a second get_llm call instead.
    try:
        refined, usage_entry = await _run_refinement_chain(
            prompt, query, history_messages, owner_id, knowledge_id, session_id,
            provider="openai", model="gpt-5.4-mini", temperature=0.0,
            for_function_calling=True,
        )
        logger.info(f"[refiner] (fallback) '{query}' -> '{refined}'")
        return {"refined_query": refined, "usage_by_model": [usage_entry],
                "service_calls": [build_service_call(
                    service="refiner", latency_ms=int((_time.perf_counter() - _t0) * 1000),
                    provider=usage_entry.get("provider"), model=usage_entry.get("model"),
                    input_tokens=usage_entry.get("input_tokens", 0),
                    output_tokens=usage_entry.get("output_tokens", 0))]}

    except Exception as e:
        logger.warning(f"[refiner] Fallback LLM also failed ({e}), using original query")
        return {"refined_query": query}
