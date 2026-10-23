"""
LLM factory for the Knowledge Service's own chat path.

Ported from fs-agent-chat/agents/utils/llm.py::build_llm / _create_llm — the
Orchestrator's chat-side builder, not fs-agent-workers' enrichment factory.
Enrichment optimizes for a different axis (temperature suppression per
reasoning model, no streaming, an enrichment-specific model registry);
this is chat-shaped: streaming, and reasoning_effort gated on whether the
call also carries tool definitions (see chat/openai_reasoning.py).

Dropped from the Orchestrator version: the Agent Registry node-config
priority chain (per-node provider/model/temperature resolution). That
depends on `agent_registry_config`, which has no equivalent in this repo yet
(knowledge_chat_config lands in a later phase per
claude-logs/plan/knowledge-owned-chat.md). Callers here pass provider/model/
temperature explicitly; unset falls back to the hardcoded default.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

from langchain_openai import ChatOpenAI

from chat.anthropic_sampling import anthropic_accepts_temperature
from chat.credentials import get_api_key_async
from chat.llm_limiter import apply_llm_limits, LLM_REQUEST_TIMEOUT
from chat.model_catalog import CATALOG_PROVIDERS
from chat.openai_reasoning import OPENAI_REASONING_EFFORT_LOW, openai_reasoning_effort_for_model
import utils.setup_env as setup_env

logger = logging.getLogger(__name__)

ChatOpenAI.model_rebuild()

# Lazy-imported: only providers actually used at runtime pay the import cost,
# and a provider missing its optional package doesn't break the others.
_ChatAnthropic = None
_ChatGoogleGenerativeAI = None
_ChatGroq = None
_ChatDeepSeek = None

# Derived from the catalog served by GET /v1/chat-models, so a provider the
# dashboard is told about always has a _create_llm branch below. Was a
# duplicated literal set; the two drifting would have let the picker offer a
# provider this factory silently falls back to OpenAI for.
_KNOWN_PROVIDERS = CATALOG_PROVIDERS

HARDCODED_DEFAULT_PROVIDER = "openai"
HARDCODED_DEFAULT_MODEL = "gpt-5.4-mini"


def _get_chat_anthropic():
    global _ChatAnthropic
    if _ChatAnthropic is None:
        from langchain_anthropic import ChatAnthropic
        _ChatAnthropic = ChatAnthropic
    return _ChatAnthropic


def _get_chat_google():
    global _ChatGoogleGenerativeAI
    if _ChatGoogleGenerativeAI is None:
        from langchain_google_genai import ChatGoogleGenerativeAI
        _ChatGoogleGenerativeAI = ChatGoogleGenerativeAI
    return _ChatGoogleGenerativeAI


def _get_chat_groq():
    global _ChatGroq
    if _ChatGroq is None:
        from langchain_groq import ChatGroq
        _ChatGroq = ChatGroq
    return _ChatGroq


def _get_chat_deepseek():
    global _ChatDeepSeek
    if _ChatDeepSeek is None:
        from langchain_deepseek import ChatDeepSeek
        _ChatDeepSeek = ChatDeepSeek
    return _ChatDeepSeek


def _infer_provider_from_model_id(model: str) -> Optional[str]:
    """When provider is omitted or mislabeled, infer from the model id.

    Groq hosts many OSS models as openai/* or meta-llama/* — route to groq.
    """
    if not model:
        return None
    m = model.lower()
    if "gpt-oss" in m or m.startswith("openai/gpt-oss"):
        return "groq"
    if "llama3" in m or "llama-3" in m or "meta-llama" in m or "mixtral" in m:
        return "groq"
    if "gemini" in m or m.startswith("google/"):
        return "google"
    if m.startswith("deepseek"):
        return "deepseek"
    if m.startswith("minimax") or m.startswith("abab"):
        return "minimax"
    return None


def normalize_provider_and_model(provider: Optional[str], model: str) -> Tuple[str, str]:
    """Fix common misconfigurations (e.g. provider=openai for a Groq-only OSS model id).

    A missing provider is inferred from the model id first, falling back to
    the hardcoded default only if inference also comes up empty — an absent
    provider must not shortcut past inference the way a wrong-but-known
    provider does below.
    """
    m = model or ""
    p = (provider or "").strip().lower()

    aliases = {"gemini": "google", "google_ai": "google", "google-ai": "google"}
    if p in aliases:
        p = aliases[p]

    if p not in _KNOWN_PROVIDERS:
        inferred = _infer_provider_from_model_id(m)
        if inferred:
            logger.info(f"[llm_factory] Unknown provider {provider!r} — inferring {inferred!r} from model {m!r}")
            p = inferred
        elif not p:
            p = HARDCODED_DEFAULT_PROVIDER

    # OpenAI API does not serve Groq OSS models; a Groq model id may arrive tagged openai.
    if p == "openai" and _infer_provider_from_model_id(m) == "groq":
        logger.info(f"[llm_factory] Model {m!r} is a Groq-hosted OSS model — using groq client instead of openai")
        p = "groq"

    return p, m


async def get_llm(
    owner_id: Optional[str],
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 1000,
    streaming: bool = False,
    for_function_calling: bool = False,
    knowledge_id: Optional[str] = None,
    force_platform_key: bool = False,
    effort: Optional[str] = None,
) -> Tuple[Any, str, str]:
    """Build a rate-limited chat model for the knowledge chat path.

    Falls back to the hardcoded default (openai / gpt-5.4-mini) when
    provider/model are omitted. BYOK credentials are looked up async
    (chat/credentials.py) so this never blocks the event loop — pass
    `knowledge_id` so that lookup resolves in-process instead of falling back
    to an external HTTP call (see chat/credentials.py's module docstring).

    ``force_platform_key=True`` bypasses the BYOK lookup and builds on the
    platform key. Callers should not set it directly — it is how
    chat/byok_fallback.py retries after the owner's key was rejected.

    Returns:
        (llm_instance, provider, key_type) — key_type is "owner-key" or "platform-key".
    """
    model = model or HARDCODED_DEFAULT_MODEL
    provider, model = normalize_provider_and_model(provider, model)

    default_key = _default_key_for_provider(provider)
    api_key, key_type = await get_api_key_async(
        owner_id, provider, default_key, knowledge_id=knowledge_id,
        force_platform_key=force_platform_key,
    )

    logger.info(f"[llm_factory] provider={provider} model={model} key_type={key_type}")

    llm = apply_llm_limits(
        _create_llm(provider, model, temperature, max_tokens, api_key, streaming,
                    for_function_calling, effort=effort)
    )
    return llm, provider, key_type


def _default_key_for_provider(provider: str) -> str:
    return {
        "openai": setup_env.OPENAI_API_KEY,
        "anthropic": setup_env.ANTHROPIC_API_KEY,
        "groq": setup_env.GROQ_API_KEY,
        "google": setup_env.GOOGLE_API_KEY,
        "deepseek": setup_env.DEEPSEEK_API_KEY,
        "minimax": setup_env.MINIMAX_API_KEY,
    }.get(provider) or ""


def _create_llm(provider, model, temperature, max_tokens, api_key, streaming,
                for_function_calling=False, effort=None):
    # max_tokens was already unconditionally passed to anthropic/groq/
    # deepseek/google below (i.e. every OTHER provider already capped at
    # get_llm's default of 1000) — openai/minimax silently dropped it, which
    # was an inconsistency, not an intentional "openai has no cap" choice.
    # Passing it here just makes openai/minimax match the other four.
    if provider == "openai":
        kwargs = dict(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key or setup_env.OPENAI_API_KEY,
            streaming=streaming,
            stream_usage=streaming,
            max_retries=2,
            timeout=LLM_REQUEST_TIMEOUT,
        )
        reasoning_effort = openai_reasoning_effort_for_model(model, for_function_calling=for_function_calling)
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort
        return ChatOpenAI(**kwargs)

    elif provider == "anthropic":
        ChatAnthropic = _get_chat_anthropic()
        kwargs = dict(
            model_name=model,
            max_tokens=max_tokens,
            api_key=api_key or setup_env.ANTHROPIC_API_KEY,
            streaming=streaming,
        )
        # Anthropic REJECTS temperature with a 400 on its newer models (Opus 4.7
        # and later), so this is an omission, not a default: sending it fails the
        # whole request. It is omitted for unknown models too — see
        # anthropic_sampling for the empirical boundary and why unknown fails
        # safe. Callers asking for temperature=0 determinism get it on the older
        # models and rely on the newer models' own low-variance defaults.
        if anthropic_accepts_temperature(model):
            kwargs["temperature"] = temperature
        else:
            logger.debug(
                f"Omitting temperature for Anthropic model {model!r} — the "
                f"parameter is rejected by this model generation"
            )
        # `effort` is how the models that dropped temperature are steered
        # instead. Probed 2026-08-28: it is accepted by BOTH generations —
        # claude-sonnet-4-6 takes effort AND temperature — so this is not an
        # either/or with the block above. Sent only when a caller asks, because
        # the current models already default to effort=high and setting it
        # unconditionally would be a silent behaviour change, not because the
        # parameter is unsupported anywhere.
        if effort:
            kwargs["effort"] = effort
            logger.info(f"Anthropic model {model!r} running at effort={effort!r}")
        return ChatAnthropic(**kwargs)

    elif provider == "groq":
        ChatGroq = _get_chat_groq()
        return ChatGroq(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key or setup_env.GROQ_API_KEY,
            streaming=streaming,
        )

    elif provider == "google":
        ChatGoogleGenerativeAI = _get_chat_google()
        return ChatGoogleGenerativeAI(
            model=model,
            temperature=temperature,
            max_output_tokens=max_tokens,
            api_key=api_key or setup_env.GOOGLE_API_KEY,
            disable_streaming=not streaming,
        )

    elif provider == "deepseek":
        ChatDeepSeek = _get_chat_deepseek()
        return ChatDeepSeek(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key or setup_env.DEEPSEEK_API_KEY,
            streaming=streaming,
            max_retries=2,
            reasoning_effort=OPENAI_REASONING_EFFORT_LOW,
            # DeepSeek V4 defaults to thinking mode, which rejects a forced
            # tool_choice with a 400 and requires echoing reasoning_content
            # back on multi-turn tool calls. Disable it so DeepSeek behaves
            # as a plain tool-capable chat model.
            extra_body={"thinking": {"type": "disabled"}},
        )

    elif provider == "minimax":
        # No first-class LangChain chat model for MiniMax; use its
        # OpenAI-compatible endpoint through ChatOpenAI.
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key or setup_env.MINIMAX_API_KEY,
            base_url=setup_env.MINIMAX_BASE_URL,
            streaming=streaming,
            stream_usage=streaming,
            max_retries=2,
        )

    else:
        logger.warning(f"Unsupported provider {provider!r} — known: {sorted(_KNOWN_PROVIDERS)}; falling back to OpenAI {HARDCODED_DEFAULT_MODEL}")
        return ChatOpenAI(
            model=HARDCODED_DEFAULT_MODEL,
            temperature=0,
            api_key=setup_env.OPENAI_API_KEY,
            max_retries=2,
        )
