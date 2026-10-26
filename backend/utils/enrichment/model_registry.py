"""The enrichment (metadata extraction) models this service accepts, and what each one
sends to its provider.

The worker owns extraction and its registry
(workers/celery_utils/enrichment/llm_factory.py ``ENRICHMENT_MODELS``). The API image
cannot import worker code (deploy/docker/api.Dockerfile copies only backend/), yet three
API paths need the same facts:

- validation: ``PATCH /v1/admin/enrichment-config`` and a collection's
  ``enrichment_model`` must accept a name the worker can run and refuse one it cannot,
  before the name is stored and every document of a collection fails on it;
- collection preview (utils/enrichment/preview_run.py) must call the same wire model at
  the same reasoning effort extraction will;
- pricing (chat/pricing.py, utils/usage/cost_calculator.py) must price a registry
  variant such as ``gpt-5.6-luna-low`` at its wire model's rate, never "no price".

So this is a mirror of that registry, field for field (provider, api_model,
reasoning_effort). tests/unit/test_enrichment_model_registry.py reads the worker file
and fails when the two disagree, so the mirror cannot drift silently.

A *variant* is a registry name whose wire model differs (``api_model``): the same
OpenAI model requested at a different reasoning effort, swept or deployed as its own
name without changing the base entry (plain ``gpt-5.6-luna`` at "none" is also the
rich-mode extraction model, so it must not change when extraction moves to "low").
"""
from __future__ import annotations

from typing import Dict, NamedTuple, Optional, Tuple


class EnrichmentModel(NamedTuple):
    provider: str
    #: Model id sent to the provider. Equal to the registry name except for variants.
    api_model: str
    #: Reasoning effort the worker sends, or None for a model that takes none.
    reasoning_effort: Optional[str]


def _m(provider: str, api_model: str, reasoning_effort: Optional[str] = None) -> EnrichmentModel:
    return EnrichmentModel(provider, api_model, reasoning_effort)


#: Mirror of workers/celery_utils/enrichment/llm_factory.py ENRICHMENT_MODELS.
ENRICHMENT_MODELS: Dict[str, EnrichmentModel] = {
    "gpt-5-mini": _m("openai", "gpt-5-mini", "minimal"),
    "gpt-5-nano": _m("openai", "gpt-5-nano", "minimal"),
    "gpt-5.4-mini": _m("openai", "gpt-5.4-mini", "none"),
    "gpt-5.4-nano": _m("openai", "gpt-5.4-nano", "medium"),
    "gpt-5.6-terra": _m("openai", "gpt-5.6-terra", "none"),
    "gpt-5.6-sol": _m("openai", "gpt-5.6-sol", "none"),
    "gpt-5.4-nano-high": _m("openai", "gpt-5.4-nano", "high"),
    "gpt-5.6-terra-high": _m("openai", "gpt-5.6-terra", "high"),
    "gpt-5.6-luna-high": _m("openai", "gpt-5.6-luna", "high"),
    "gpt-5.6-luna-low": _m("openai", "gpt-5.6-luna", "low"),
    "gpt-5.6-luna": _m("openai", "gpt-5.6-luna", "none"),
    "llama-3.3-70b-versatile": _m("groq", "llama-3.3-70b-versatile"),
    "openai/gpt-oss-20b": _m("groq", "openai/gpt-oss-20b", "low"),
    "openai/gpt-oss-120b": _m("groq", "openai/gpt-oss-120b", "low"),
    "claude-haiku-4-5-20251001": _m("anthropic", "claude-haiku-4-5-20251001"),
    "claude-opus-5": _m("anthropic", "claude-opus-5"),
    "claude-sonnet-5": _m("anthropic", "claude-sonnet-5"),
    "gemini-2.5-flash-lite": _m("google", "gemini-2.5-flash-lite"),
    "gemini-3.1-flash-lite": _m("google", "gemini-3.1-flash-lite"),
    "gemini-3.5-flash-lite": _m("google", "gemini-3.5-flash-lite"),
    "gemini-3.6-flash": _m("google", "gemini-3.6-flash"),
    "deepseek-v4-flash": _m("deepseek", "deepseek-v4-flash"),
    "minimax-m3": _m("minimax", "minimax-m3"),
}

_PROVIDER_ALIASES = {"gemini": "google", "google_ai": "google", "google-ai": "google"}


def _norm_provider(provider: Optional[str]) -> str:
    p = (provider or "").strip().lower()
    return _PROVIDER_ALIASES.get(p, p)


def _lookup(name: Optional[str]) -> Tuple[Optional[str], Optional[EnrichmentModel]]:
    """``(registry key, entry)`` for ``name``, or ``(None, None)``.

    Exact names first (``openai/gpt-oss-20b`` is itself a groq key). Otherwise a
    ``provider/model`` spelling resolves when ``model`` is a registry key on that
    provider: the stored row has carried ``google/gemini-3.1-flash-lite``, which the
    worker runs, so it must stay acceptable.
    """
    if not name:
        return None, None
    n = name.strip()
    if n in ENRICHMENT_MODELS:
        return n, ENRICHMENT_MODELS[n]
    if "/" in n:
        prefix, rest = n.split("/", 1)
        entry = ENRICHMENT_MODELS.get(rest)
        if entry is not None and entry.provider == _norm_provider(prefix):
            return rest, entry
    return None, None


def get(name: Optional[str]) -> Optional[EnrichmentModel]:
    """The registry entry for ``name``, or None when it is not an enrichment model."""
    return _lookup(name)[1]


def wire_model(name: Optional[str]) -> Optional[str]:
    """The model id the provider bills for ``name``: a variant's ``api_model``
    (``gpt-5.6-luna-low`` -> ``gpt-5.6-luna``), otherwise ``name`` unchanged,
    including names this registry does not know."""
    key, entry = _lookup(name)
    if entry is not None and entry.api_model != key:
        return entry.api_model
    return name


def validate(model: str, provider: Optional[str] = None) -> Tuple[str, str]:
    """``(model, provider)`` for a name the worker can run, or ``ValueError`` naming the
    accepted ones. The model is returned as sent (a ``provider/`` spelling is kept).

    A provider, when given, must be the one the model runs on: the worker builds the
    client from the stored provider, so ``gpt-5.6-luna-low`` stored with ``groq``
    would fail every document of the collection instead of failing this request.
    An omitted provider is filled from the registry.
    """
    name = (model or "").strip()
    _, entry = _lookup(name)
    if entry is None:
        raise ValueError(
            f"Unknown enrichment model {model!r}. Accepted: {', '.join(sorted(ENRICHMENT_MODELS))}"
        )
    if provider is not None and provider.strip() and _norm_provider(provider) != entry.provider:
        raise ValueError(
            f"Enrichment model {name!r} runs on provider {entry.provider!r}, not {provider!r}"
        )
    return name, entry.provider
