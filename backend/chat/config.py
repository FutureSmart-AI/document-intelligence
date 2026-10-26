"""Per-knowledge_id chat config resolution (Phase 3d — see
claude-logs/plan/knowledge-owned-chat.md's Config section and
claude-logs/plan/knowledge-owned-chat-progress.md).

Resolves provider/model/temperature/system_prompt for the main RAG
retrieval + answer-generation + query-refinement path through a 5-level
chain:
  1. Request body fields, when the Orchestrator (or another caller) supplies
     them explicitly — highest priority, since a caller-supplied value is an
     explicit override. Populated by routers/chat.py's ChatRequest, which
     merges its flat provider/model/temperature/system_prompt fields with its
     per-node ``node_configs`` map before calling in.
  2. The ``knowledge_chat_node_config`` row for (knowledge_id, node_name) —
     per-node stored config, only consulted when a ``node_name`` is supplied.
     Lets one knowledge base pair an expensive retrieval model with a cheap
     query rewriter without the caller having to send config every turn.
  3. The ``knowledge_chat_config`` DB row for this knowledge_id, if one
     exists. For ``llm_provider``/``llm_model`` this level applies ONLY to
     the ``generate_answer`` node (and to no-node calls): the row's LLM
     selection is what the Settings UI's model picker writes via
     ``PUT /chat/config``, and that picker is the *answer* model — using it
     to also drag the query rewriter and the retrieval ReAct loop onto the
     picked model silently multiplied the cost of every turn (e.g. picking
     an expensive answer model moved the rewriter off its nano default).
     ``refined_query_node``/``rag_agent`` skip this level for provider/model
     and fall through to the env levels below; every other field
     (temperature, max_tokens, system_prompt, the policy flags) still reads
     the row for all nodes. Absence of a row is the expected default case,
     not an error.
  4. Per-node env vars (``RAG_CHAT_ANSWER_LLM_PROVIDER``/``_MODEL``,
     ``RAG_CHAT_AGENT_LLM_*``, ``RAG_CHAT_REFINED_QUERY_LLM_*``, collected in
     ``setup_env.RAG_CHAT_NODE_LLM_ENV``) — ops-level, per node, no DB row
     required. Above the flat pair below because it is more specific: this is
     how you pin the answer model without also moving the query rewriter off
     its cheap one. Provider and model resolve as a pair.
  5. Flat env vars (``RAG_CHAT_LLM_PROVIDER`` etc.) — same ops-level override
     applied to **every** node at once.
  6. Hardcoded default — per-node for provider/model (``NODE_HARDCODED_
     DEFAULTS``: refined_query_node -> gpt-5.4-nano, rag_agent/
     generate_answer -> gpt-5.6-luna; openai/gpt-5.4-mini when no node_name
     is given), temperature 0, no system prompt suffix.

Each field resolves independently through that chain — a node row setting
only ``prompt`` still inherits provider/model from level 3.

This does NOT cover the CRAG retrieval verifier's model (env-only,
``RAG_RETRIEVAL_VERIFIER_PROVIDER``/``_MODEL``) or the RAG agent's
``ModelFallbackMiddleware`` model (env-only, ``RAG_FALLBACK_PROVIDER``/
``_MODEL``) — both are separate, already-configurable knobs per the plan,
not part of this chain.
"""
from __future__ import annotations

from typing import Any, Dict, List, NamedTuple, Optional

from sqlmodel import select

from utils.db.session import async_session
from utils.db.retry_utils import retry_db_connection
from utils.logger import logger
from utils.models.db_tables import KnowledgeChatConfig, KnowledgeChatNodeConfig
import utils.setup_env as setup_env

HARDCODED_DEFAULT_PROVIDER = "openai"
HARDCODED_DEFAULT_MODEL = "gpt-5.4-mini"
HARDCODED_DEFAULT_TEMPERATURE = 0.0
HARDCODED_DEFAULT_MAX_TOKENS = 1024

# Level-5 floor, per node — the query rewriter is a cheap classification-style
# call, while retrieval and answer generation carry the actual reasoning load,
# so each gets its own hardcoded (provider, model) floor instead of sharing
# HARDCODED_DEFAULT_MODEL above. Only consulted when node_name is given;
# resolve_chat_config's no-node call (knowledge-level fields like
# chat_enabled/show_sources) keeps falling back to the flat constants.
NODE_HARDCODED_DEFAULTS: Dict[str, "tuple[str, str]"] = {
    "refined_query_node": ("openai", "gpt-5.4-nano"),
    "rag_agent": ("openai", "gpt-5.6-luna"),
    "generate_answer": ("openai", "gpt-5.6-luna"),
}


class ResolvedChatConfig(NamedTuple):
    llm_provider: str
    llm_model: str
    temperature: float
    system_prompt: Optional[str]
    show_sources: bool
    chat_enabled: bool
    follow_up: bool = True
    # G10. Same posture as show_sources/chat_enabled/follow_up above: stored
    # knowledge-level policy, no request-level override, no env level.
    # suggested_questions is editorial content, not generation output —
    # resolving it is a plain read, not a fallthrough chain. The voice flags
    # gate no server behavior (see db_tables.py::KnowledgeChatConfig) — they
    # exist here only so GET /chat/config can round-trip what was stored.
    # No mutable default here — NamedTuple defaults are shared across every
    # instance that doesn't override them, so a caller mutating the list
    # in place would leak into every other resolution. resolve_chat_config
    # always passes an explicit value below.
    suggested_questions: Optional[List[str]] = None
    voice_input_enabled: bool = False
    voice_output_enabled: bool = False
    # max_tokens resolves through the same per-field chain as temperature
    # (request > node row > knowledge row > env > hardcoded) and, unlike
    # temperature, IS threaded downstream — see chat/rag_node.py. include_charts/
    # show_raw_response have no request-level or per-node override slot, same
    # posture as show_sources/chat_enabled/follow_up: knowledge-level policy only.
    max_tokens: int = 1024
    include_charts: bool = True
    show_raw_response: bool = True


@retry_db_connection(max_retries=3)
async def _get_node_config_row(
    knowledge_id: str, node_name: str
) -> Optional[KnowledgeChatNodeConfig]:
    """Fetch the knowledge_chat_node_config row for (knowledge_id, node_name),
    or None if absent or on any DB failure.

    Same degrade-never-raise contract as _get_config_row below: a missing or
    unreachable per-node override falls through to the knowledge-level row,
    it must never break a chat turn.
    """
    if not knowledge_id or not node_name:
        return None
    try:
        async with async_session() as session:
            statement = select(KnowledgeChatNodeConfig).where(
                KnowledgeChatNodeConfig.knowledge_id == knowledge_id,
                KnowledgeChatNodeConfig.node_name == node_name,
            )
            result = await session.execute(statement)
            return result.scalar_one_or_none()
    except Exception as e:
        logger.warning(
            f"[chat_config] Failed to load knowledge_chat_node_config for "
            f"{knowledge_id}/{node_name}: {e}"
        )
        return None


@retry_db_connection(max_retries=3)
async def _get_config_row(knowledge_id: str) -> Optional[KnowledgeChatConfig]:
    """Fetch the knowledge_chat_config row for knowledge_id, or None if absent
    or on any DB failure — a missing/unreachable row degrades to the next
    level of the chain, it must never raise."""
    if not knowledge_id:
        return None
    try:
        async with async_session() as session:
            statement = select(KnowledgeChatConfig).where(
                KnowledgeChatConfig.knowledge_id == knowledge_id
            )
            result = await session.execute(statement)
            return result.scalar_one_or_none()
    except Exception as e:
        logger.warning(f"[chat_config] Failed to load knowledge_chat_config for {knowledge_id}: {e}")
        return None


async def resolve_chat_config(
    knowledge_id: str,
    request_provider: Optional[str] = None,
    request_model: Optional[str] = None,
    request_temperature: Optional[float] = None,
    request_max_tokens: Optional[int] = None,
    request_system_prompt: Optional[str] = None,
    node_name: Optional[str] = None,
) -> ResolvedChatConfig:
    """Resolve provider/model/temperature/system_prompt/show_sources/
    chat_enabled/follow_up/suggested_questions/voice_input_enabled/
    voice_output_enabled for a chat turn on this knowledge_id.

    Each field is resolved independently through the 4-level chain — e.g. a
    caller can supply ``request_provider`` alone and still pick up the DB
    row's ``temperature``, rather than an all-or-nothing override.
    ``show_sources``/``chat_enabled``/``follow_up``/``suggested_questions``/
    ``voice_input_enabled``/``voice_output_enabled`` have no request-level
    override slot (they are knowledge_id-level policy, not per-request
    choices) and resolve directly from the knowledge-level DB row or the
    hardcoded default (``True``/``[]``/``False``); there is no env level, and
    no per-node level, for any of them.

    ``node_name`` inserts the per-node DB tier (knowledge_chat_node_config)
    between the request body and the knowledge-level row, making the full
    chain:

        request body
          -> knowledge_chat_node_config row for (knowledge_id, node_name)
          -> knowledge_chat_config row  (provider/model: generate_answer and
             no-node calls only — see the module docstring, level 3)
          -> env
          -> hardcoded

    Omitting it (the default) skips that tier entirely and preserves the
    original 4-level behaviour, so existing callers are unaffected.
    """
    row = await _get_config_row(knowledge_id)
    node_row = await _get_node_config_row(knowledge_id, node_name) if node_name else None

    # provider and model resolve as a PAIR at the node tier, matching
    # routers/chat.py::_request_chat_config's own both-or-neither rule: a node
    # row naming a provider without a model (or vice versa) would otherwise
    # pair one level's provider with another's model — e.g. provider
    # 'anthropic' with model 'gpt-5.4-mini' — which is a runtime error, not a
    # config. Either the node row supplies both or it supplies neither.
    node_provider = node_row.provider if node_row else None
    node_model = node_row.model if node_row else None
    if not (node_provider and node_model):
        node_provider = node_model = None

    # The knowledge-level row's LLM selection is answer-only: PUT /chat/config's
    # llm_provider/llm_model come from the Settings model picker, which selects
    # the ANSWER model. refined_query_node and rag_agent must not inherit it —
    # they skip this level and resolve from per-node env / flat env / their own
    # hardcoded floors instead (see the module docstring, level 3). A no-node
    # call keeps reading the row so knowledge-level reads round-trip unchanged.
    row_provider = row.llm_provider if row else None
    row_model = row.llm_model if row else None
    if node_name is not None and node_name != "generate_answer":
        row_provider = row_model = None

    default_provider, default_model = NODE_HARDCODED_DEFAULTS.get(
        node_name, (HARDCODED_DEFAULT_PROVIDER, HARDCODED_DEFAULT_MODEL)
    )

    # Per-node env, above the flat RAG_CHAT_LLM_* pair because it is more
    # specific: the flat pair applies to every node at once, so using it to pin
    # the answer model would also drag refined_query_node off its cheap model.
    # Resolved as a pair for the same reason node rows are (see above) — a
    # provider from one level with a model from another is a runtime error.
    env_node_provider, env_node_model = setup_env.RAG_CHAT_NODE_LLM_ENV.get(
        node_name, (None, None)
    )
    if not (env_node_provider and env_node_model):
        env_node_provider = env_node_model = None

    llm_provider = (
        request_provider
        or node_provider
        or row_provider
        or env_node_provider
        or setup_env.RAG_CHAT_LLM_PROVIDER
        or default_provider
    )
    llm_model = (
        request_model
        or node_model
        or row_model
        or env_node_model
        or setup_env.RAG_CHAT_LLM_MODEL
        or default_model
    )

    # `is not None` at every level, not truthiness — 0.0 is a legitimate
    # explicit temperature, and a bare `or` chain would silently treat "use
    # temperature 0" as unset and fall through to the next level.
    if request_temperature is not None:
        temperature = request_temperature
    elif node_row is not None and node_row.temperature is not None:
        temperature = node_row.temperature
    elif row is not None and row.temperature is not None:
        temperature = row.temperature
    elif setup_env.RAG_CHAT_LLM_TEMPERATURE is not None:
        try:
            temperature = float(setup_env.RAG_CHAT_LLM_TEMPERATURE)
        except ValueError:
            temperature = HARDCODED_DEFAULT_TEMPERATURE
    else:
        temperature = HARDCODED_DEFAULT_TEMPERATURE

    system_prompt = (
        request_system_prompt
        or (node_row.prompt if node_row else None)
        or (row.system_prompt if row else None)
        or setup_env.RAG_CHAT_SYSTEM_PROMPT
        or None
    )

    # Same `is not None` posture as temperature above — 0 is not a realistic
    # max_tokens value in practice, but treating it as unset would still be
    # wrong in principle, so this stays consistent with the temperature chain
    # rather than relying on truthiness.
    if request_max_tokens is not None:
        max_tokens = request_max_tokens
    elif node_row is not None and node_row.max_tokens is not None:
        max_tokens = node_row.max_tokens
    elif row is not None and row.max_tokens is not None:
        max_tokens = row.max_tokens
    elif setup_env.RAG_CHAT_MAX_TOKENS is not None:
        try:
            max_tokens = int(setup_env.RAG_CHAT_MAX_TOKENS)
        except ValueError:
            max_tokens = HARDCODED_DEFAULT_MAX_TOKENS
    else:
        max_tokens = HARDCODED_DEFAULT_MAX_TOKENS

    show_sources = row.show_sources if row is not None else True
    chat_enabled = row.chat_enabled if row is not None else True
    follow_up = row.follow_up if row is not None else True
    # G10 — same posture as the three flags above: knowledge-level policy
    # only, no request override, no env level.
    suggested_questions = list(row.suggested_questions) if row is not None and row.suggested_questions else []
    voice_input_enabled = row.voice_input_enabled if row is not None else False
    voice_output_enabled = row.voice_output_enabled if row is not None else False
    include_charts = row.include_charts if row is not None else True
    show_raw_response = row.show_raw_response if row is not None else True

    return ResolvedChatConfig(
        llm_provider=llm_provider,
        llm_model=llm_model,
        temperature=temperature,
        system_prompt=system_prompt,
        show_sources=show_sources,
        chat_enabled=chat_enabled,
        follow_up=follow_up,
        suggested_questions=suggested_questions,
        voice_input_enabled=voice_input_enabled,
        voice_output_enabled=voice_output_enabled,
        max_tokens=max_tokens,
        include_charts=include_charts,
        show_raw_response=show_raw_response,
    )
