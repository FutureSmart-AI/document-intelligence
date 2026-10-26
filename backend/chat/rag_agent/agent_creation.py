"""
RAG agent graph creation.
"""
import os
from typing import Any, List, Optional

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelFallbackMiddleware
from langchain.agents.structured_output import ToolStrategy

from chat.llm_factory import get_llm
from chat.rag_agent.agent_output import RagAgentOutput
from chat.rag_agent.prompts import RAG_AGENT_CORE, RAG_AGENT_RETRIEVAL_ONLY_TAIL
from chat.rag_tools import RAG_TOOLS
from utils.logger import logger


def _env_flag(name: str, default: str = "true") -> bool:
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "no", "off"}


async def _build_rag_middleware(
    llm_provider: str,
    llm_model: str,
    temperature: float,
    owner_id: Optional[str],
    knowledge_id: Optional[str] = None,
    force_platform_key: bool = False,
) -> List[Any]:
    """Assemble the middleware stack for the RAG ReAct agent.

    - ``ModelCallLimitMiddleware`` — explicit per-run cap on model calls. Before
      this, the only bound was LangGraph's default ``recursion_limit`` of 25
      super-steps (≈12 model calls, hit as an opaque GraphRecursionError).
      ``exit_behavior="end"`` ends the run gracefully instead, so a
      budget-exhausted run still yields messages the extraction/verifier
      pipeline can consume. Limit applies PER ainvoke — each CRAG verifier
      retry is its own run with a fresh budget.
    - ``ModelFallbackMiddleware`` — on a model-call exception (provider outage,
      quota), retries the call on the fallback model instead of failing the
      whole retrieval turn. Keys resolve through ``get_llm`` with the same
      owner_id chain as the primary, so owner/platform key policy is
      unchanged. Disabled by setting RAG_FALLBACK_PROVIDER/MODEL to "".
    - ``ToolRetryMiddleware`` is deliberately ABSENT: it only retries tools that
      RAISE, and every RAG tool catches Exception internally and returns an
      error artifact instead — it could never fire. Transient-error retries
      belong in the tools' HTTP layer.
    """
    middleware: List[Any] = []

    try:
        run_limit = int(os.environ.get("RAG_MODEL_CALL_RUN_LIMIT", "12"))
    except ValueError:
        run_limit = 12
    if run_limit > 0:
        middleware.append(
            ModelCallLimitMiddleware(run_limit=run_limit, exit_behavior="end")
        )

    fb_provider = os.environ.get("RAG_FALLBACK_PROVIDER", "openai").strip()
    fb_model = os.environ.get("RAG_FALLBACK_MODEL", "gpt-5.4-mini").strip()
    if fb_provider and fb_model and (fb_provider, fb_model) != (llm_provider, llm_model):
        try:
            # Inherits force_platform_key: rebuilding the graph on the platform key
            # while leaving the fallback model on the rejected owner key would just
            # move the same auth failure one middleware deeper.
            fallback_llm, _, _ = await get_llm(
                owner_id=owner_id,
                provider=fb_provider,
                model=fb_model,
                temperature=temperature,
                for_function_calling=True,
                knowledge_id=knowledge_id,
                force_platform_key=force_platform_key,
            )
            middleware.append(ModelFallbackMiddleware(fallback_llm))
        except Exception as e:
            # A broken fallback must never block agent creation — run without it.
            logger.warning(
                f"[rag_agent] Could not build fallback model {fb_provider}/{fb_model}: {e}"
            )

    return middleware


async def create_rag_agent_graph(
    knowledge_id: str,
    llm_provider: str = "openai",
    llm_model: str = "gpt-5.4-mini",
    temperature: float = 0,
    user_instructions: Optional[str] = None,
    owner_id: Optional[str] = None,
    generate_answer_instructions: Optional[str] = None,
    force_platform_key: bool = False,
):
    """
    Create a LangGraph ReAct agent for RAG retrieval.

    The ReAct agent is ALWAYS retrieval-only. The user-facing answer is
    produced by a separate node (``answer_node.generate_rag_answer``) that
    runs after this agent finishes. ``generate_answer_instructions`` is
    accepted here for signature compatibility with the upstream caller but
    is not used to compose the retrieval system prompt.

    The prefetched metadata schema is injected at invocation time as a
    pre-synthesized ``retrieve_metadata_catalog`` tool call in the message
    history (see ``run_rag_agent``), not into the system prompt.

    Structured handoff: the agent is bound to ``RagAgentOutput`` via
    ``ToolStrategy`` — the schema rides as a tool the model calls to END its
    final turn, so unlike the old ``response_format=RagAgentOutput`` binding
    there is NO extra end-of-turn coercion LLM call. ``_parse_agent_output``'s
    JSON fallback still covers models that emit the blob as text instead.
    Kill switch: RAG_STRUCTURED_OUTPUT_ENABLED=false.

    Returns:
        Tuple of (CompiledStateGraph instance ready to be invoked, key_type string)
    """
    del generate_answer_instructions  # consumed downstream

    logger.info(f"Creating LangGraph RAG agent for knowledge_id: {knowledge_id}")

    # RAG always binds tools (RAG_TOOLS below) — for_function_calling=True so
    # GPT-5 never gets reasoning_effort on a Chat Completions call that also
    # carries tool definitions (that combination 400s).
    #
    # ``force_platform_key`` is set when agent_execution rebuilds this graph after
    # the owner's BYOK key was rejected mid-run: the model is bound at construction,
    # so recovering means rebuilding the graph, not re-keying an existing one.
    llm, _, key_type = await get_llm(
        owner_id=owner_id,
        provider=llm_provider,
        model=llm_model,
        temperature=temperature,
        for_function_calling=True,
        knowledge_id=knowledge_id,
        force_platform_key=force_platform_key,
    )

    logger.info(f"Using {len(RAG_TOOLS)} RAG tools")

    core_prompt = RAG_AGENT_CORE

    # NOTE: user_instructions are intentionally NOT folded into the system
    # prompt anymore. They are injected as a late HumanMessage (operator
    # instructions) right before the query in ``run_rag_agent`` — recency in
    # the thread improves instruction-following over burial in the long system
    # prompt. The param is kept in the signature for signature compatibility.
    del user_instructions  # consumed by run_rag_agent as a HumanMessage

    parts: list[str] = [core_prompt, RAG_AGENT_RETRIEVAL_ONLY_TAIL]

    parts.append(
        "\n\n---\n\n"
        f"**Knowledge ID** (use this exact value as `knowledge_id` on every tool call): `{knowledge_id}`"
    )

    rag_prompt = "".join(parts)

    middleware = await _build_rag_middleware(
        llm_provider=llm_provider,
        llm_model=llm_model,
        temperature=temperature,
        owner_id=owner_id,
        knowledge_id=knowledge_id,
        force_platform_key=force_platform_key,
    )

    response_format = (
        ToolStrategy(RagAgentOutput)
        if _env_flag("RAG_STRUCTURED_OUTPUT_ENABLED")
        else None
    )

    rag_agent_graph = create_agent(
        model=llm,
        tools=RAG_TOOLS,
        system_prompt=rag_prompt,
        middleware=middleware,
        response_format=response_format,
        name="rag_sub_agent",
    )

    logger.info(
        f"Created LangGraph RAG agent successfully "
        f"(middleware={[type(m).__name__ for m in middleware]}, "
        f"structured_output={response_format is not None})"
    )
    return rag_agent_graph, key_type
