"""
Enrichment LLM Factory Module

Provides dynamic LLM instantiation for document enrichment
supporting multiple providers (OpenAI, Groq, Anthropic, Google, DeepSeek, MiniMax).
"""

from typing import Optional, Tuple, Any, NamedTuple
from services.celery_logger import logger
from services import celery_setup_env
from celery_utils.shared.credentials_utils import get_api_key

from langchain_openai import ChatOpenAI


class _ChatMiniMax(ChatOpenAI):
    """
    ChatOpenAI, but defaulting with_structured_output to function-calling mode.

    MiniMax's OpenAI-compatible endpoint accepts response_format=json_schema (the
    ChatOpenAI default, 'json_schema' strict mode) without erroring, but ignores it —
    the model replies conversationally instead of emitting the requested JSON, which
    fails Pydantic parsing 100% of the time. Verified 2026-08-07 by direct test.
    Tool-calling mode ('function_calling') works reliably, so pin it as the default
    since production calls with_structured_output(schema) with no method override.
    """

    def with_structured_output(self, schema=None, **kwargs):
        kwargs.setdefault("method", "function_calling")
        return super().with_structured_output(schema, **kwargs)


# Lazy imports for optional providers to avoid import errors if not installed
_ChatGroq = None
_ChatAnthropic = None
_ChatGoogle = None
_ChatDeepSeek = None


def _get_chat_groq():
    """Lazy load ChatGroq to avoid import errors if langchain-groq not installed."""
    global _ChatGroq
    if _ChatGroq is None:
        from langchain_groq import ChatGroq
        _ChatGroq = ChatGroq
    return _ChatGroq


def _get_chat_anthropic():
    """Lazy load ChatAnthropic to avoid import errors if langchain-anthropic not installed."""
    global _ChatAnthropic
    if _ChatAnthropic is None:
        from langchain_anthropic import ChatAnthropic
        _ChatAnthropic = ChatAnthropic
    return _ChatAnthropic


def _get_chat_google():
    """Lazy load ChatGoogleGenerativeAI to avoid import errors if langchain-google-genai not installed."""
    global _ChatGoogle
    if _ChatGoogle is None:
        from langchain_google_genai import ChatGoogleGenerativeAI
        _ChatGoogle = ChatGoogleGenerativeAI
    return _ChatGoogle


def _get_chat_deepseek():
    """Lazy load ChatDeepSeek to avoid import errors if langchain-deepseek not installed."""
    global _ChatDeepSeek
    if _ChatDeepSeek is None:
        from langchain_deepseek import ChatDeepSeek
        _ChatDeepSeek = ChatDeepSeek
    return _ChatDeepSeek


# Environment variable defaults
OPENAI_API_KEY = celery_setup_env.OPENAI_API_KEY
GROQ_API_KEY = celery_setup_env.GROQ_API_KEY
ANTHROPIC_API_KEY = getattr(celery_setup_env, 'ANTHROPIC_API_KEY', None)
GOOGLE_API_KEY = getattr(celery_setup_env, 'GOOGLE_API_KEY', None)
DEEPSEEK_API_KEY = getattr(celery_setup_env, 'DEEPSEEK_API_KEY', None)
MINIMAX_API_KEY = getattr(celery_setup_env, 'MINIMAX_API_KEY', None)
MINIMAX_BASE_URL = getattr(celery_setup_env, 'MINIMAX_BASE_URL', None) or "https://api.minimax.io/v1"
DEFAULT_ENRICHMENT_MODEL = celery_setup_env.ENRICHMENT_MODEL
DEFAULT_FALLBACK_MODEL = celery_setup_env.ENRICHMENT_FALLBACK_MODEL


# Enrichment LLM Models Configuration
# Maps model names to provider - 2 models per provider with similar performance to gpt-5-mini
ENRICHMENT_MODELS = {
    # OpenAI models
    "gpt-5-mini": {
        "provider": "openai",
        "supports_temperature": False,
        "supports_reasoning_effort": True,
        "reasoning_effort": "minimal",
    },
    "gpt-5-nano": {
        "provider": "openai",
        "supports_temperature": False,
        "supports_reasoning_effort": True,
        "reasoning_effort": "minimal",
    },
    "gpt-5.4-mini": {
        "provider": "openai",
        "supports_temperature": False,
        "supports_reasoning_effort": True,
        "reasoning_effort": "none",  # disables reasoning for 5.4 models
    },
    # The platform fallback model. "medium", not "none": with no reasoning it does date
    # arithmetic in one shot and miscounts. On the 24 sample resumes (3 runs each, with the
    # Reference date section) years_of_experience was right 68/72 at "none", 70/72 at "low"
    # and 72/72 at "medium", for ~370 extra reasoning tokens per document
    # (backend/samples/resumes/EXTRACTION-YEARS-2026-10-25.md).
    "gpt-5.4-nano": {
        "provider": "openai",
        "supports_temperature": False,
        "supports_reasoning_effort": True,
        "reasoning_effort": "medium",
    },
    # GPT-5.6 "Terra" — OpenAI's balanced tier, the 5.6-family successor to the
    # mini tier (per developers.openai.com/api/docs/models/gpt-5.6-terra).
    # Reasoning-token model, so treated like the 5.4 pair: no custom temperature,
    # reasoning disabled via effort="none" to keep enrichment cheap and fast.
    # The docs do not state parameter support for this model, so both settings were
    # inherited from the 5.4 family and confirmed by a live call on 2026-08-05:
    # 200 OK with output_token_details.reasoning == 0, i.e. effort="none" is honoured.
    "gpt-5.6-terra": {
        "provider": "openai",
        "supports_temperature": False,
        "supports_reasoning_effort": True,
        "reasoning_effort": "none",
    },
    # GPT-5.6 "Sol" — OpenAI's flagship/Pro tier ("frontier model for complex
    # professional work" per developers.openai.com/api/docs/models). $5.00/$30.00
    # per 1M, the most expensive model in this eval by a wide margin.
    "gpt-5.6-sol": {
        "provider": "openai",
        "supports_temperature": False,
        "supports_reasoning_effort": True,
        "reasoning_effort": "none",
    },
    # Reasoning-capacity variants (2026-08-10): same wire model as the entry above,
    # requested at "high" effort instead of the "none"/default baseline, via
    # `api_model` so both can be swept as distinct, comparable rows without
    # touching the production baseline entries. See llm_factory "reasoning
    # variant" handling in get_enrichment_llm / get_provider_from_model.
    "gpt-5.4-nano-high": {
        "provider": "openai",
        "api_model": "gpt-5.4-nano",
        "supports_temperature": False,
        "supports_reasoning_effort": True,
        "reasoning_effort": "high",
    },
    "gpt-5.6-terra-high": {
        "provider": "openai",
        "api_model": "gpt-5.6-terra",
        "supports_temperature": False,
        "supports_reasoning_effort": True,
        "reasoning_effort": "high",
    },
    "gpt-5.6-luna-high": {
        "provider": "openai",
        "api_model": "gpt-5.6-luna",
        "supports_temperature": False,
        "supports_reasoning_effort": True,
        "reasoning_effort": "high",
    },
    # Production extraction candidate (2026-10-26): luna at "low". The model evaluation
    # measured it the most accurate extraction model, 980/984 fields against 967/984 for
    # gpt-5.4-nano at "medium", at about half the cost
    # (evals/model_eval/RESULTS-2026-10-26.md). It is a separate name, not a change to
    # "gpt-5.6-luna", because plain luna at "none" is also RICH_MODE_ENRICHMENT_MODEL
    # (processing_modes.py) and that path was not measured at "low". Selecting it is a
    # config change: enrichment_default_config (PATCH /v1/admin/enrichment-config) or a
    # collection's enrichment_model. The API keeps a mirror of this entry
    # (backend/utils/enrichment/model_registry.py); tests/unit/test_enrichment_model_registry.py
    # fails when the two disagree.
    "gpt-5.6-luna-low": {
        "provider": "openai",
        "api_model": "gpt-5.6-luna",
        "supports_temperature": False,
        "supports_reasoning_effort": True,
        "reasoning_effort": "low",
    },
    # GPT-5.6 "Luna" — the budget tier of the 5.6 family, "roughly corresponds to
    # the nano model tier" per developers.openai.com/api/docs/models/gpt-5.6-luna.
    # $0.20/$1.20, cheaper than gpt-5.6-terra ($2.00/$12.00) by 10x.
    "gpt-5.6-luna": {
        "provider": "openai",
        "supports_temperature": False,
        "supports_reasoning_effort": True,
        "reasoning_effort": "none",
    },
    # Groq models (fast, similar capability tier)
    "llama-3.3-70b-versatile": {
        "provider": "groq",
    },
    # gpt-oss on Groq: model ceiling is context_window 131072 /
    # max_completion_tokens 65536 (GET /openai/v1/models, verified 2026-08-27).
    # We cap far below that on purpose. The cap is NOT a truncation fix -- run
    # 01a043f8 died at 2,137 output tokens, 3% of the ceiling, because the model
    # emitted unbalanced tool-call JSON, not because it ran out of room. This is a
    # runaway/cost guard: a repetition loop would otherwise burn all 65536 tokens
    # (~$0.039 at $0.60/M completion) and stall the task for minutes. 16384 is 7.7x
    # the largest output observed in the mode-model matrix, so it cannot clip a
    # legitimate extraction, and bounds a runaway to ~$0.01 and a few seconds.
    "openai/gpt-oss-20b": {
        "provider": "groq",
        "supports_reasoning_effort": True,
        "reasoning_effort": "low",
        "max_output_tokens": 16384,
    },
    "openai/gpt-oss-120b": {
        "provider": "groq",
        "supports_reasoning_effort": True,
        "reasoning_effort": "low",
        "max_output_tokens": 16384,
    },
    # Judge / semantic-comparator model for enrichment eval. Deliberately a
    # different provider from the OpenAI/Groq extractors so judge and extractor
    # don't share failure modes.
    "claude-haiku-4-5-20251001": {
        "provider": "anthropic",
    },
    # Reference generator for enrichment eval (testing/eval_enrichment_langsmith.py).
    # Opus 5 rejects `temperature` outright rather than only non-default values.
    "claude-opus-5": {
        "provider": "anthropic",
        "supports_temperature": False,
    },
    # Claude Sonnet 5 — "the best combination of speed and intelligence" per
    # platform.claude.com/docs/en/about-claude/models/overview, confirmed current
    # 2026-08-10. $3/$15 standard ($2/$10 introductory through 2026-08-31). Sits
    # between claude-haiku-4-5 ($1/$5) and claude-opus-5 ($5/$25) in price — the
    # first mid-tier Anthropic option measured in this eval. Like claude-opus-5,
    # it rejects `temperature` outright ("temperature is deprecated for this
    # model", confirmed live 2026-08-10) — adaptive thinking replaced it.
    "claude-sonnet-5": {
        "provider": "anthropic",
        "supports_temperature": False,
    },
    # Google models. The Flash-Lite tier is the cheapest credible option across
    # all four providers, which is why it is worth measuring: enrichment quality
    # saturates across strong models, so cost is the real differentiator.
    "gemini-2.5-flash-lite": {
        "provider": "google",
    },
    "gemini-3.1-flash-lite": {
        "provider": "google",
    },
    # Newer Gemini tiers (2026-08-10), confirmed current against
    # ai.google.dev/gemini-api/docs/models. 3.5-flash-lite is 3.1-flash-lite's
    # direct successor at a higher price ($0.30/$2.50 vs $0.25/$1.50); 3.6-flash is
    # a non-lite tier well above anything else measured ($1.50/$7.50) — both need a
    # quality case, not a cost one, to matter on this workload.
    "gemini-3.5-flash-lite": {
        "provider": "google",
    },
    "gemini-3.6-flash": {
        "provider": "google",
    },
    # DeepSeek. OpenAI-compatible pricing tier, integration pattern (langchain-deepseek,
    # thinking mode disabled) already proven in production in fs-agent-chat/agents/utils/llm.py.
    "deepseek-v4-flash": {
        "provider": "deepseek",
    },
    # MiniMax. No first-class LangChain chat model — served via ChatOpenAI + base_url,
    # same as fs-agent-chat/chat_utils/gpt_processing/llm_initialization.py.
    "minimax-m3": {
        "provider": "minimax",
    },
}


def _api_model(model_name: str) -> str:
    """
    Resolve the wire-level model id to send to the provider.

    Normally identical to the dict key. Reasoning-capacity variants (e.g.
    "gpt-5.4-nano-high") key on a distinct eval-facing name so they can be swept
    as separate rows, but must call the API with the real model id via `api_model`.
    """
    model_config = ENRICHMENT_MODELS.get(model_name)
    if model_config and "api_model" in model_config:
        return model_config["api_model"]
    return model_name


def served_model_identity(model_name: Optional[str]) -> dict:
    """What a call configured as ``model_name`` actually sent to the provider.

    ``{"model": <wire model id>, "configured_model": <registry name, only when it
    differs>, "reasoning_effort": <effort sent, only for reasoning models>}``.

    Every record of an enrichment call (document telemetry, the enrichment-health
    event, the usage event) names the WIRE model, because that is what the provider
    bills and what every price table is keyed on: "gpt-5.6-luna-low" has no price,
    "gpt-5.6-luna" does. The registry name and the effort travel beside it, so a
    record still says which configuration ran.
    """
    if not model_name:
        return {"model": model_name}
    identity = {"model": _api_model(model_name)}
    if identity["model"] != model_name:
        identity["configured_model"] = model_name
    if _supports_reasoning_effort(model_name):
        identity["reasoning_effort"] = _get_reasoning_effort(model_name)
    return identity


def _supports_reasoning_effort(model_name: str) -> bool:
    """Check if a model supports reasoning_effort configuration."""
    model_config = ENRICHMENT_MODELS.get(model_name)
    return bool(model_config and model_config.get("supports_reasoning_effort"))


REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")


def _reasoning_effort_override() -> Optional[str]:
    """ENRICHMENT_REASONING_EFFORT, when set, replaces the registry's effort for every
    reasoning-capable enrichment model in this worker. It exists so a model evaluation
    (evals/model_eval) can compare e.g. gpt-5.4-nano at "low" and "medium" through the
    real worker; production leaves it unset and keeps the per-model registry values.
    An unknown value is a configuration error, not something to guess around."""
    import os

    value = os.getenv("ENRICHMENT_REASONING_EFFORT", "").strip().lower()
    if not value:
        return None
    if value not in REASONING_EFFORTS:
        raise ValueError(f"ENRICHMENT_REASONING_EFFORT={value!r}; expected one of {REASONING_EFFORTS}")
    return value


def _get_reasoning_effort(model_name: str) -> str:
    """Return the reasoning effort value for a model ('minimal', None, etc.)."""
    override = _reasoning_effort_override()
    if override:
        return override
    model_config = ENRICHMENT_MODELS.get(model_name)
    if model_config and "reasoning_effort" in model_config:
        return model_config["reasoning_effort"]
    return "minimal"  # safe default for unknown reasoning models


def _max_output_tokens(model_name: str) -> Optional[int]:
    """Return the output-token cap for a model, or None to use the provider default.

    Only set where we know the provider-reported ceiling and have a reason to sit
    below it (see the gpt-oss note in ENRICHMENT_MODELS). Absent means "unset" --
    never guess a cap, since one that is too low turns a good extraction into a
    truncated one, which is the failure this guard is meant to bound, not cause.
    """
    model_config = ENRICHMENT_MODELS.get(model_name)
    if model_config and model_config.get("max_output_tokens"):
        return int(model_config["max_output_tokens"])
    return None


def _supports_temperature(model_name: str) -> bool:
    """Check if a model supports custom temperature settings.

    Some models (e.g., OpenAI reasoning models like o1, o3, gpt-5-mini)
    only support the default temperature and will reject temperature=0.0.
    """
    model_config = ENRICHMENT_MODELS.get(model_name)
    if model_config and "supports_temperature" in model_config:
        return model_config["supports_temperature"]

    # Reasoning model prefixes that don't support temperature
    no_temp_prefixes = ("o1", "o3", "o4")
    for prefix in no_temp_prefixes:
        if model_name.startswith(prefix):
            return False

    return True


def get_provider_from_model(model_name: str) -> str:
    """
    Infer provider from model name.

    Args:
        model_name: Model identifier

    Returns:
        Provider name ("openai", "groq", "anthropic", "google", "deepseek", "minimax")
    """
    model_config = ENRICHMENT_MODELS.get(model_name)
    if model_config:
        return model_config["provider"]

    # Fallback: infer provider from model name prefix
    if model_name.startswith("gpt-") or model_name.startswith("o1"):
        return "openai"
    elif model_name.startswith("llama") or model_name.startswith("mixtral") or model_name.startswith("qwen") or model_name.startswith("openai/"):
        return "groq"
    elif model_name.startswith("claude"):
        return "anthropic"
    elif model_name.startswith("gemini"):
        return "google"
    elif model_name.startswith("deepseek"):
        return "deepseek"
    elif model_name.startswith("minimax"):
        return "minimax"

    # Default to OpenAI
    return "openai"


def get_enrichment_llm(
    owner_id: Optional[str],
    provider: str,
    model: str,
    temperature: float = 0.0,
    force_platform_key: bool = False,
) -> Tuple[Any, str]:
    """
    Create an LLM instance for enrichment based on provider.

    Args:
        owner_id: Owner ID for owner-specific API keys
        provider: LLM provider ("openai", "groq", "anthropic", "google", "deepseek", "minimax")
        model: Model name
        temperature: Temperature setting (default 0.0 for deterministic output)
        force_platform_key: Skip the BYOK lookup and use the platform key outright.
            Set by llm_retry.py::invoke_with_retry after the owner's key has been
            classified as a terminal failure (auth_error/low_balance) — the retry
            must not resolve that same dead key again. See
            celery_utils/shared/credentials_utils.py for the classification.

    Returns:
        Tuple of (llm_instance, key_type)
    """
    provider = provider.lower()

    # Models that don't support custom temperature only accept 1 (the default)
    if not _supports_temperature(model):
        logger.info(f"Model '{model}' does not support custom temperature, forcing temperature=1")
        temperature = 1

    def _get_api_key(p: str, default_key: str) -> Tuple[str, str]:
        if force_platform_key:
            return default_key, "platform-key"
        return get_api_key(owner_id, p, default_key)

    if provider == "openai":
        api_key, key_type = _get_api_key("openai", OPENAI_API_KEY)
        openai_kwargs = dict()
        if _supports_reasoning_effort(model):
            openai_kwargs["reasoning"] = {"effort": _get_reasoning_effort(model)}
        llm = ChatOpenAI(openai_api_key=api_key, model_name=_api_model(model), temperature=temperature, **openai_kwargs)
        return llm, key_type

    elif provider == "groq":
        api_key, key_type = _get_api_key("groq", GROQ_API_KEY)
        ChatGroq = _get_chat_groq()
        groq_kwargs = dict()
        if _supports_reasoning_effort(model):
            groq_kwargs["reasoning_effort"] = _get_reasoning_effort(model)
        max_out = _max_output_tokens(model)
        if max_out:
            groq_kwargs["max_tokens"] = max_out
            logger.info(f"Capping output for '{model}' at {max_out} tokens")
        llm = ChatGroq(
            groq_api_key=api_key,
            model_name=model,
            temperature=temperature,
            **groq_kwargs,
        )
        return llm, key_type

    elif provider == "anthropic":
        default_key = ANTHROPIC_API_KEY or ""
        api_key, key_type = _get_api_key("anthropic", default_key)
        ChatAnthropic = _get_chat_anthropic()
        anthropic_kwargs = dict()
        # Newer Anthropic models (e.g. claude-opus-5) reject `temperature` outright —
        # passing even the default 1 returns 400. Omit the param entirely for those.
        if _supports_temperature(model):
            anthropic_kwargs["temperature"] = temperature
        llm = ChatAnthropic(
            anthropic_api_key=api_key,
            model=model,
            **anthropic_kwargs,
        )
        return llm, key_type

    elif provider == "google":
        default_key = GOOGLE_API_KEY or ""
        api_key, key_type = _get_api_key("google", default_key)
        ChatGoogleGenerativeAI = _get_chat_google()
        llm = ChatGoogleGenerativeAI(
            google_api_key=api_key,
            model=model,
            temperature=temperature,
        )
        return llm, key_type

    elif provider == "deepseek":
        default_key = DEEPSEEK_API_KEY or ""
        api_key, key_type = _get_api_key("deepseek", default_key)
        ChatDeepSeek = _get_chat_deepseek()
        llm = ChatDeepSeek(
            api_key=api_key,
            model=model,
            temperature=temperature,
            # DeepSeek V4 defaults to thinking mode, which rejects a forced tool_choice
            # (with_structured_output) with a 400. Disable it — same fix already applied
            # in fs-agent-chat/agents/utils/llm.py.
            extra_body={"thinking": {"type": "disabled"}},
        )
        return llm, key_type

    elif provider == "minimax":
        default_key = MINIMAX_API_KEY or ""
        api_key, key_type = _get_api_key("minimax", default_key)
        llm = _ChatMiniMax(
            openai_api_key=api_key,
            model_name=model,
            temperature=temperature,
            base_url=MINIMAX_BASE_URL,
            # MiniMax M3 defaults to thinking mode ON, which leaks <think>...</think>
            # into .content and would corrupt structured-output parsing. Disable it —
            # verified against platform.minimax.io/docs/api-reference/text-openai-api.
            extra_body={"thinking": {"type": "disabled"}},
        )
        return llm, key_type

    else:
        logger.warning(f"Unknown provider '{provider}', falling back to OpenAI")
        api_key, key_type = _get_api_key("openai", OPENAI_API_KEY)
        openai_kwargs = dict()
        if _supports_reasoning_effort(model):
            openai_kwargs["reasoning"] = {"effort": "low"}
        llm = ChatOpenAI(openai_api_key=api_key, model_name=_api_model(model), temperature=temperature, **openai_kwargs)
        return llm, key_type


class FallbackLLM(NamedTuple):
    """Pairs an LLM instance with its model name and provider so callers never carry them separately."""
    llm: Any
    model_name: str
    provider: str  # e.g. "openai", "groq", "anthropic"

    def with_structured_output(self, schema: Any) -> "FallbackLLM":
        """Return a new FallbackLLM with structured output bound to schema."""
        return FallbackLLM(
            llm=self.llm.with_structured_output(schema, include_raw=True),
            model_name=self.model_name,
            provider=self.provider,
        )


def get_platform_key_llm(provider: str, model: str, temperature: float = 0.0) -> "FallbackLLM":
    """Rebuild the SAME provider/model on the platform key, wrapped as a FallbackLLM.

    Used by llm_retry.py::invoke_with_retry after the owner's BYOK key is
    classified as a terminal failure (celery_utils/shared/credentials_utils.py).
    This is deliberately not ``get_fallback_enrichment_llm`` above — that swaps to
    a DIFFERENT configured fallback model, which is the wrong move for a dead key:
    the owner picked this model for a reason (cost, quality, a provider they
    trust), and a dead key says nothing about whether that model choice was good.
    Reusing FallbackLLM as the return shape just means invoke_with_retry's existing
    ``_switch_to_fallback`` plumbing (model_name/provider bookkeeping,
    with_structured_output rebinding) works unchanged for this case too.
    """
    llm, _key_type = get_enrichment_llm(None, provider, model, temperature, force_platform_key=True)
    return FallbackLLM(llm=llm, model_name=model, provider=provider)


def get_fallback_enrichment_llm(
    owner_id: Optional[str],
    temperature: float = 0.0,
) -> Optional["FallbackLLM"]:
    """
    Create a fallback LLM using a two-level fallback chain:
    1. Global DB default  — enrichment_default_config.enrichment_fallback_model
    2. Env var            — ENRICHMENT_FALLBACK_MODEL
    Returns None if no fallback model is configured anywhere.
    """
    from celery_utils.db.db_utils import get_enrichment_default_config_sync

    # Level 1: global DB default
    global_config = get_enrichment_default_config_sync()
    if global_config and global_config.get("enrichment_fallback_model"):
        model = global_config["enrichment_fallback_model"]
        provider = global_config.get("enrichment_fallback_provider") or get_provider_from_model(model)
        logger.info(f"Fallback LLM source=global: model={model}, provider={provider}")
    elif DEFAULT_FALLBACK_MODEL:
        # Level 2: env var
        model = DEFAULT_FALLBACK_MODEL
        provider = get_provider_from_model(model)
        logger.info(f"Fallback LLM source=env: model={model}, provider={provider}")
    else:
        return None

    llm, _ = get_enrichment_llm(owner_id, provider, model, temperature)
    return FallbackLLM(llm=llm, model_name=model, provider=provider)


def get_enrichment_config(knowledge_config, processing_mode=None) -> Tuple[str, str, float]:
    """
    Extract enrichment configuration, most specific source first:

    1. Per-knowledge override  — knowledge_config.enrichment_model/provider/temperature
    2. Processing-mode default — a document read in any mode other than auto/text_based gets
                                 the stronger model named in processing_modes.py
    3. Global DB default       — enrichment_default_config table (id=1)
    4. Env var                 — ENRICHMENT_MODEL + inferred provider
    5. Hardcoded default       — openai / gpt-5.4-mini / 0.0

    The mode sits *below* the per-knowledge override on purpose: a knowledge base whose
    owner picked an enrichment model keeps it whatever the document looks like. The mode
    only replaces the value nobody chose — the platform default.

    Args:
        knowledge_config: KnowledgeConfig object or None
        processing_mode: The ProcessingMode (or its wire string) the document was read
            in. None means "not told", which changes nothing — see
            processing_modes.enrichment_override_for_mode.

    Returns:
        Tuple of (provider, model, temperature)
    """
    from celery_utils.db.db_utils import get_enrichment_default_config_sync
    from celery_utils.processing.processing_modes import enrichment_override_for_mode

    hardcoded_model = DEFAULT_ENRICHMENT_MODEL or "gpt-5.4-mini"
    hardcoded_provider = get_provider_from_model(hardcoded_model)
    hardcoded_temperature = 0.0

    # Level 1: per-knowledge override
    if knowledge_config is not None and knowledge_config.enrichment_model:
        model = knowledge_config.enrichment_model
        provider = knowledge_config.enrichment_provider or get_provider_from_model(model)
        temperature = knowledge_config.enrichment_temperature
        if temperature is None:
            temperature = hardcoded_temperature
        logger.info(f"Enrichment config source=knowledge: model={model}, provider={provider}")
        return provider, model, temperature

    # Level 2: processing-mode default. A scanned/layout/visual/mixed document arrives as
    # messier text than plain prose, so it is enriched by a stronger model unless the
    # knowledge base above named one of its own.
    mode_override = enrichment_override_for_mode(processing_mode)
    if mode_override is not None:
        provider, model = mode_override
        logger.info(
            f"Enrichment config source=processing_mode({getattr(processing_mode, 'value', processing_mode)}): "
            f"model={model}, provider={provider}"
        )
        return provider, model, hardcoded_temperature

    # Level 3: global DB default
    global_config = get_enrichment_default_config_sync()
    if global_config and global_config.get("enrichment_model"):
        model = global_config["enrichment_model"]
        provider = global_config.get("enrichment_provider") or get_provider_from_model(model)
        temperature = global_config.get("enrichment_temperature")
        if temperature is None:
            temperature = hardcoded_temperature
        logger.info(f"Enrichment config source=global: model={model}, provider={provider}")
        return provider, model, temperature

    # Level 4: env var + hardcoded default
    logger.info(f"Enrichment config source=env/default: model={hardcoded_model}, provider={hardcoded_provider}")
    return hardcoded_provider, hardcoded_model, hardcoded_temperature


def get_enrichment_prompt_extension(knowledge_config) -> Optional[str]:
    """
    Get custom enrichment prompt from KnowledgeConfig if available.

    Args:
        knowledge_config: KnowledgeConfig object or None

    Returns:
        Custom prompt string or None
    """
    if knowledge_config is None:
        return None

    return getattr(knowledge_config, 'enrichment_prompt', None)
