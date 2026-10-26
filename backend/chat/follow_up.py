"""Post-answer follow-up question generation (Phase 5+ — see
claude-logs/plan/chat-migration/knowledge-owned-chat.md's "Follow-up
questions — IN scope" section).

Ported and trimmed from fs-agent-chat/chat_utils/gpt_processing/
followup_questions.py + its invocation in agents/run.py's `_gen_follow_up`
closure. Differences from the donor, all confirmed by the plan:
  - A graph node, but the LAST one — it needs the FINAL answer, so it runs
    after rag and chart, wrapped in asyncio.wait_for so a slow call never
    delays the SSE final frame. It used to run in routers/chat.py, fired
    concurrently with the knowledge_chat_logs write; that put it OUTSIDE the
    LangGraph run, so LangSmith recorded it as a separate root trace
    ("RunnableSequence") instead of a step of the turn. Moved into the graph
    (2026-08-25) so the whole turn is one trace. Cost of the move: it no
    longer overlaps the log write (a single INSERT).
  - Model: reuses the resolved rag_answer_node model (llm_provider/llm_model
    already resolved by chat/config.py::resolve_chat_config for this turn)
    instead of the donor's hardcoded openai/gpt-5.4-nano — the plan
    explicitly recommends this ("no separate node" needed). An optional
    env-level override (FOLLOW_UP_LLM_PROVIDER/_MODEL, utils/setup_env.py)
    was added later to decouple follow-up from the answer model without a
    DB-level knob — checked first, falls back to the passed-in
    llm_provider/llm_model (today's behavior) when unset.
  - Gating: knowledge_chat_config.follow_up (this service's own config),
    not the donor's chatbot_parameters.get("follow_up") Agent-Registry field.
  - No nl2sql short-circuit (donor checks nl2sql_resp.get("follow_up") first)
    — this service has exactly one capability, there is no other service's
    follow-ups to prefer.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from pydantic import BaseModel, Field

from utils.logger import logger

_FALLBACK_FOLLOW_UPS = ["Can you tell me more?", "Please explain further."]


class FollowUpResponse(BaseModel):
    follow_up: List[str] = Field(..., description="List of exactly two short follow-up questions.")


_FOLLOW_UP_SYSTEM_PROMPT = """Generate exactly 2 short, relevant follow-up questions the user might ask next, \
based on the conversation and the answer just given. Each question must be under 15 words. \
Avoid generic phrases like "Tell me more" — make them specific to the answer's actual content. \
{context_clause}"""

_FOLLOW_UP_CONTEXT_CLAUSE = (
    "You must ONLY generate questions that can be strictly answered using this context:\n[Context]\n{context}"
)


def _build_prompt(has_context: bool) -> ChatPromptTemplate:
    context_clause = _FOLLOW_UP_CONTEXT_CLAUSE if has_context else ""
    system = _FOLLOW_UP_SYSTEM_PROMPT.format(context_clause=context_clause)
    return ChatPromptTemplate.from_messages([
        ("system", system),
        MessagesPlaceholder(variable_name="messages"),
        ("human", "Answer just given: {response}"),
    ])


async def generate_follow_up_questions(
    query: str,
    response: str,
    owner_id: Optional[str],
    llm_provider: str,
    llm_model: str,
    past_messages: Optional[List[Dict[str, Any]]] = None,
    context: Optional[str] = None,
    knowledge_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[str]:
    """Generate exactly 2 short follow-up questions for the turn just
    answered. Never raises — degrades to a generic fallback pair on any LLM
    failure, matching the donor's own fallback behavior.

    Caller is responsible for the asyncio.wait_for timeout guard and the
    knowledge_chat_config.follow_up gate — this function always attempts
    generation when called.
    """
    try:
        from chat.byok_fallback import run_with_key_fallback
        from chat.utils.messages import build_history_messages
        from utils.setup_env import FOLLOW_UP_LLM_MODEL, FOLLOW_UP_LLM_PROVIDER

        # Env override, checked first. Both must be set to take effect — a
        # half-override (e.g. provider set, model not) falls through to the
        # passed-in generate_answer model for both, rather than silently
        # pairing an overridden provider with a model id from a different
        # catalogue.
        if FOLLOW_UP_LLM_PROVIDER and FOLLOW_UP_LLM_MODEL:
            llm_provider, llm_model = FOLLOW_UP_LLM_PROVIDER, FOLLOW_UP_LLM_MODEL

        history_messages = build_history_messages(past_messages or [])
        prompt = _build_prompt(has_context=bool(context))

        invoke_payload: Dict[str, Any] = {"messages": history_messages, "response": response}
        if context:
            invoke_payload["context"] = context

        # The whole chain is rebuilt per attempt so a retry after a rejected BYOK
        # key binds the platform-key model, not the rejected one.
        _used: dict = {}

        async def _invoke(llm):
            _used["llm"] = llm
            # llm-first, structured_output second: we want the raw response back too so
            # its usage_metadata is available for the usage event. `chain.ainvoke` on a
            # `prompt | structured_output_llm` composition normally returns the parsed
            # object with no way to reach the underlying AIMessage. Wrap the LLM
            # with `include_raw=True` (langchain-standard shape: `{"raw", "parsed",
            # "parsing_error"}`) so we get both, then unpack.
            try:
                structured_llm = llm.with_structured_output(FollowUpResponse, include_raw=True)
            except TypeError:
                # Older/other providers may not accept include_raw — fall through to the
                # pre-existing behaviour (no usage_metadata available, no event emitted).
                structured_llm = llm.with_structured_output(FollowUpResponse)
            return await (prompt | structured_llm).ainvoke(invoke_payload)

        _outcome = await run_with_key_fallback(
            _invoke, owner_id=owner_id, knowledge_id=knowledge_id,
            source="follow_up", provider=llm_provider, model=llm_model,
            temperature=0.5, for_function_calling=True,
        )
        result = _outcome.value
        llm = _used["llm"]
        _provider, _key_type = _outcome.provider, _outcome.key_type

        # Unpack the (raw, parsed) shape when include_raw was accepted.
        raw_msg = None
        if isinstance(result, dict) and "parsed" in result:
            raw_msg = result.get("raw")
            parsed = result.get("parsed")
        else:
            parsed = result

        # Emit a usage_events row for the follow-up LLM call. Previously untracked,
        # so a chat turn's follow-up tokens were invisible to get_current_usage's
        # message aggregate. Fire-and-forget: metering must not be able to fail
        # follow-up generation.
        try:
            um = getattr(raw_msg, "usage_metadata", None) if raw_msg is not None else None
            in_tok = int((um or {}).get("input_tokens") or (um or {}).get("prompt_tokens") or 0)
            out_tok = int((um or {}).get("output_tokens") or (um or {}).get("completion_tokens") or 0)
            if in_tok or out_tok:
                import asyncio as _asyncio_evt
                from utils.qdrant.usage_tracking import send_llm_usage_event

                _asyncio_evt.ensure_future(send_llm_usage_event(
                    label="follow_up",
                    provider=llm_provider,
                    model=llm_model,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    owner_id=owner_id,
                    knowledge_id=knowledge_id,
                    session_id=session_id,
                    key_type=_key_type or "platform-key",
                ))
        except Exception as _e_evt:
            logger.warning(f"[follow_up] Failed to emit usage event: {_e_evt}")

        follow_ups = parsed.follow_up if isinstance(parsed, FollowUpResponse) else (parsed or {}).get("follow_up")
        if isinstance(follow_ups, list) and follow_ups:
            return follow_ups
        return list(_FALLBACK_FOLLOW_UPS)
    except Exception as e:
        logger.warning(f"[follow_up] Generation failed for query={query[:60]!r}: {e}")
        return list(_FALLBACK_FOLLOW_UPS)


async def follow_up_node(
    query: str,
    response: str,
    is_answered: Optional[str],
    owner_id: Optional[str] = None,
    past_messages: Optional[List[Dict[str, Any]]] = None,
    chat_config: Any = None,
    knowledge_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Graph node wrapper: gate, timeout, and never raise.

    Skipped entirely (returns no follow-ups, rather than an empty list) when
    the knowledge_chat_config.follow_up flag is off, when no chat_config was
    supplied at all (chat/report_execution.py runs the same graph for batch
    reports and has no user to suggest questions to), or when the turn
    produced no answer — there is nothing to generate follow-ups FROM.

    Wrapped in asyncio.wait_for with RAG_FOLLOW_UP_TIMEOUT_SEC so a slow call
    never delays the turn. Returns None on any failure/timeout/disable.
    """
    if chat_config is None or not getattr(chat_config, "follow_up", False):
        return {"follow_up": None}
    if not (response or "").strip() or is_answered == "no":
        return {"follow_up": None}

    try:
        from utils.setup_env import RAG_FOLLOW_UP_TIMEOUT_SEC

        questions = await asyncio.wait_for(
            generate_follow_up_questions(
                query=query,
                response=response,
                owner_id=owner_id,
                llm_provider=chat_config.llm_provider,
                llm_model=chat_config.llm_model,
                past_messages=past_messages,
                knowledge_id=knowledge_id,
                session_id=session_id,
            ),
            timeout=RAG_FOLLOW_UP_TIMEOUT_SEC,
        )
        return {"follow_up": questions}
    except asyncio.TimeoutError:
        logger.warning("[follow_up] Generation timed out, omitting from response")
        return {"follow_up": None}
    except Exception as e:
        logger.warning(f"[follow_up] Generation failed, omitting from response: {e}")
        return {"follow_up": None}
