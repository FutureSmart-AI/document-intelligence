"""The chat provider/model catalog the dashboard renders its model pickers from.

Single source of truth for *which* chat models this service offers. Closes G8
in docs/screen-api-flow-document-intelligence.md: before this, there was no
endpoint listing chat models at all, so the dashboard had to hardcode a
curated list — in four places on the Settings screen alone (the extraction
LLM, plus each of the three node cards).

Scope: chat models only. Embedding models are a separate, unrelated list
(``EmbeddingModel`` enum, served by ``GET /v1/embedding-models``) — they are
write-once per collection and not interchangeable with these.

Being a static constant rather than a DB table is deliberate: a model is only
usable here if ``chat/llm_factory.py::_create_llm`` has a branch that can
build it and the deployment holds a key for its provider. Both are code/config
facts, not per-tenant data, so a table would let an operator "add" a model the
factory cannot construct — exactly the accepted-but-ignored failure mode this
service has already been bitten by (G10/G11). ``llm_factory`` derives its
``_KNOWN_PROVIDERS`` from this catalog so the two cannot drift.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import utils.setup_env as setup_env
from utils.logger import logger

def resolve_answer_node_default() -> Tuple[str, str]:
    """The (provider, model) an unconfigured answer node would actually run.

    Mirrors levels 4-6 of chat/config.py's resolution chain for
    ``generate_answer``: the per-node env pair
    (``RAG_CHAT_ANSWER_LLM_PROVIDER`` / ``_MODEL``), then the flat
    ``RAG_CHAT_LLM_*`` pair, then that node's floor. Levels 1-3 (request, node
    row, knowledge row) are per-collection and per-turn, so they cannot apply to
    a catalog that is identical for every collection.

    **Env is the source of truth** — the answer node's pair is set in
    `.env.dev`/`.env.local`/`.env.prod`, so changing the model there changes both
    what the node runs and what this picker preselects, with no code edit. The
    per-node pair is read first precisely because the flat pair applies to every
    node at once and could not express "the answer model" alone.

    No model literal is duplicated in this module: the floor for a fully-unset
    env is read from the one place that already owns it,
    ``chat/config.py::NODE_HARDCODED_DEFAULTS``, so the two cannot drift. That
    import is function-local on purpose — chat/config.py pulls in the DB session,
    and this module is imported by llm_factory.
    """
    from chat.config import NODE_HARDCODED_DEFAULTS

    floor_provider, floor_model = NODE_HARDCODED_DEFAULTS["generate_answer"]

    # Resolved as a pair, matching resolve_chat_config: a provider from the
    # per-node env with a model from a different level is a runtime error.
    node_provider, node_model = setup_env.RAG_CHAT_NODE_LLM_ENV.get(
        "generate_answer", (None, None)
    )
    if not (node_provider and node_model):
        node_provider = node_model = None

    return (
        node_provider or setup_env.RAG_CHAT_LLM_PROVIDER or floor_provider,
        node_model or setup_env.RAG_CHAT_LLM_MODEL or floor_model,
    )


# Ordered most-capable-first within each provider; the dashboard renders them
# in this order. ``default_model`` is what the picker preselects when the user
# picks a provider without naming a model — NOT what an unconfigured node
# runs (that is the 5-level chain in chat/config.py).
#
# The Settings screen now configures **only the answer node**: the three
# per-node cards were dropped, so the one model this catalog's default is read
# for is ``generate_answer``. The answer node's provider therefore carries that
# node's default (see resolve_answer_node_default) rather than the flat
# ``HARDCODED_DEFAULT_MODEL`` floor (gpt-5.4-mini) a no-node-name resolution
# falls back to — preselecting the floor would show the user a cheaper model
# than the answer node actually runs when nothing is stored.
#
# The per-provider ``default_model`` below is only the pick for a provider the
# user switches to by hand. The answer node's provider has its value replaced by
# get_chat_model_catalog from env (see resolve_answer_node_default), so this
# constant stays static for llm_factory's CATALOG_PROVIDERS and
# is_known_chat_model while the served response follows the deployment.
#
# Other providers have no answer-node default to mirror, so each keeps its own
# general-purpose pick, used only once the user switches provider by hand.
#
# Each model is an object, not a bare string, so per-model tags (G8's
# "recommended" / "faster" / "better reasoning") can be added later without
# breaking the response shape for callers already reading model_name.
CHAT_MODEL_CATALOG: List[Dict[str, Any]] = [
    {
        "provider_name": "openai",
        "models": [
            {"model_name": "gpt-5.6-terra"},
            {"model_name": "gpt-5.6-luna"},
            {"model_name": "gpt-5.4-mini"},
            {"model_name": "gpt-5.4-nano"},
            # gpt-4o removed 2026-08-25: the platform OpenAI project does not
            # have access to it (403 model_not_found on every call) — same
            # class of failure as the gpt-4o-mini incident on 2026-08-20.
        ],
        # Replaced from env for the answer node's provider — see above
        "default_model": "gpt-5.6-luna",
    },
    {
        "provider_name": "anthropic",
        "models": [
            {"model_name": "claude-opus-4-6"},
            {"model_name": "claude-sonnet-4-6"},
            {"model_name": "claude-haiku-4-5"},
        ],
        "default_model": "claude-sonnet-4-6",
    },
    {
        "provider_name": "google",
        "models": [
            {"model_name": "gemini-3.5-flash"},
            {"model_name": "gemini-3.5-flash-lite"},
            {"model_name": "gemini-3.1-pro-preview"},
            {"model_name": "gemini-3.1-flash-lite-preview"},
        ],
        "default_model": "gemini-3.5-flash-lite",
    },
    {
        "provider_name": "groq",
        "models": [
            {"model_name": "openai/gpt-oss-120b"},
            {"model_name": "openai/gpt-oss-20b"},
            # meta-llama/llama-4-scout-17b-16e-instruct, qwen/qwen3-32b,
            # llama-3.3-70b-versatile, and llama-3.1-8b-instant removed
            # 2026-08-25: all four 404 ("model does not exist or you do not
            # have access to it") on Groq's live API — deprecated/removed
            # model IDs, verified against the real endpoint.
        ],
        "default_model": "openai/gpt-oss-120b",
    },
    {
        "provider_name": "deepseek",
        "models": [
            {"model_name": "deepseek-v4-flash"},
            {"model_name": "deepseek-v4-pro"},
        ],
        "default_model": "deepseek-v4-flash",
    },
    {
        "provider_name": "minimax",
        "models": [{"model_name": "MiniMax-M3"}],
        "default_model": "MiniMax-M3",
    },
]

# Derived, not written twice — adding a provider above makes it known to the
# factory automatically, so the catalog can never advertise a provider
# _create_llm would reject.
CATALOG_PROVIDERS = frozenset(p["provider_name"] for p in CHAT_MODEL_CATALOG)


def get_chat_model_catalog() -> List[Dict[str, Any]]:
    """Return the catalog, with the answer node's provider carrying that node's
    resolved default model.

    Copied per call so a caller mutating the result cannot corrupt the
    module-level constant for every later request — and so the env override
    below is applied to the copy, never written back into the constant.
    """
    catalog = [
        {
            "provider_name": p["provider_name"],
            "models": [dict(m) for m in p["models"]],
            "default_model": p["default_model"],
        }
        for p in CHAT_MODEL_CATALOG
    ]

    answer_provider, answer_model = resolve_answer_node_default()
    entry = next(
        (e for e in catalog if e["provider_name"] == answer_provider), None
    )
    if entry is None:
        # RAG_CHAT_LLM_PROVIDER names a provider this catalog does not carry.
        # llm_factory derives its buildable providers from CATALOG_PROVIDERS, so
        # such a value is unbuildable anyway — surface it rather than inventing
        # a catalog entry the picker would offer and the factory would reject.
        logger.warning(
            "RAG_CHAT_LLM_PROVIDER=%r is not in the chat model catalog; serving "
            "each provider's own default_model unchanged",
            answer_provider,
        )
        return catalog

    entry["default_model"] = answer_model
    if all(m["model_name"] != answer_model for m in entry["models"]):
        # A default the picker cannot select is a broken default, so an
        # env-pinned model that predates/postdates this catalog is added to the
        # list rather than left danging. Prepended, since the catalog is ordered
        # most-capable-first and this is the deployment's chosen model.
        logger.info(
            "RAG_CHAT_LLM_MODEL=%r is not in %s's catalog list; adding it so the "
            "picker can select the model the answer node actually runs",
            answer_model,
            answer_provider,
        )
        entry["models"].insert(0, {"model_name": answer_model})

    return catalog


def is_known_chat_model(provider: str, model: str) -> bool:
    """Whether (provider, model) appears in the catalog.

    Deliberately NOT enforced on write by PUT /chat/config: the factory can
    build any model id its provider's SDK accepts, and pinning writes to this
    list would break the moment a provider ships a model faster than we
    update it. Exposed for callers that want to warn rather than reject.
    """
    for entry in CHAT_MODEL_CATALOG:
        if entry["provider_name"] == provider:
            return any(m["model_name"] == model for m in entry["models"])
    return False
