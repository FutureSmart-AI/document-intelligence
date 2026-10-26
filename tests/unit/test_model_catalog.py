"""Unit tests for the chat model catalog (chat/model_catalog.py) and the
endpoint that serves it (GET /v1/chat-models in routers/knowledge_config_new.py).

Pure unit tests: the catalog is a static constant and the endpoint reads
nothing but that constant, so there is no DB or network to stub — only the
auth dependency is overridden.

The load-bearing test here is test_catalog_providers_match_factory: the
catalog tells the dashboard which providers exist, and llm_factory decides
which ones it can actually build. If those two ever disagree, the model
picker offers a provider that silently falls back to OpenAI at runtime —
the accepted-but-ignored failure mode this service has already shipped twice
(G10/G11). They are wired to the same frozenset today; this test fails loudly
if someone unpicks that.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chat.model_catalog import (
    CHAT_MODEL_CATALOG,
    CATALOG_PROVIDERS,
    get_chat_model_catalog,
    is_known_chat_model,
)


# ---------------------------------------------------------------------------
# Catalog invariants
# ---------------------------------------------------------------------------

def test_every_default_model_is_in_its_own_models_list():
    """A default_model the picker cannot select is a broken default."""
    for entry in CHAT_MODEL_CATALOG:
        names = [m["model_name"] for m in entry["models"]]
        assert entry["default_model"] in names, (
            f"{entry['provider_name']}'s default_model "
            f"{entry['default_model']!r} is not among its models {names}"
        )


def test_served_default_matches_the_answer_node_with_no_env_override(monkeypatch):
    """The Settings screen configures only the answer node, so the served
    default must be what generate_answer actually runs — not the flat
    HARDCODED_DEFAULT_MODEL floor a no-node-name resolution falls back to."""
    import utils.setup_env as setup_env
    from chat.config import NODE_HARDCODED_DEFAULTS

    monkeypatch.setattr(setup_env, "RAG_CHAT_NODE_LLM_ENV", {})
    monkeypatch.setattr(setup_env, "RAG_CHAT_LLM_PROVIDER", None)
    monkeypatch.setattr(setup_env, "RAG_CHAT_LLM_MODEL", None)

    expected_provider, expected_model = NODE_HARDCODED_DEFAULTS["generate_answer"]
    entry = next(
        e for e in get_chat_model_catalog()
        if e["provider_name"] == expected_provider
    )
    assert entry["default_model"] == expected_model, (
        f"{expected_provider}'s served default_model is "
        f"{entry['default_model']!r} but the answer node runs "
        f"{expected_model!r} — the picker would preselect the wrong model"
    )


def test_per_node_answer_env_wins_over_the_flat_pair(monkeypatch):
    """RAG_CHAT_ANSWER_LLM_* is more specific than the flat RAG_CHAT_LLM_* pair,
    which applies to every node at once. The whole point of the per-node vars is
    pinning the answer model without moving the query rewriter, so the per-node
    pair must win here."""
    import utils.setup_env as setup_env

    monkeypatch.setattr(setup_env, "RAG_CHAT_LLM_PROVIDER", "openai")
    monkeypatch.setattr(setup_env, "RAG_CHAT_LLM_MODEL", "gpt-4o")
    monkeypatch.setattr(
        setup_env, "RAG_CHAT_NODE_LLM_ENV",
        {"generate_answer": ("anthropic", "claude-opus-4-6")},
    )

    anthropic = next(
        e for e in get_chat_model_catalog() if e["provider_name"] == "anthropic"
    )
    assert anthropic["default_model"] == "claude-opus-4-6"


def test_half_set_per_node_env_pair_is_ignored(monkeypatch):
    """Provider and model resolve as a pair — a lone provider with no model (or
    vice versa) would pair one level's provider with another level's model, e.g.
    anthropic + gpt-5.6-luna, which is a runtime error, not a config."""
    import utils.setup_env as setup_env
    from chat.config import NODE_HARDCODED_DEFAULTS

    monkeypatch.setattr(setup_env, "RAG_CHAT_LLM_PROVIDER", None)
    monkeypatch.setattr(setup_env, "RAG_CHAT_LLM_MODEL", None)
    monkeypatch.setattr(
        setup_env, "RAG_CHAT_NODE_LLM_ENV",
        {"generate_answer": ("anthropic", None)},  # model missing
    )

    expected_provider, expected_model = NODE_HARDCODED_DEFAULTS["generate_answer"]
    entry = next(
        e for e in get_chat_model_catalog()
        if e["provider_name"] == expected_provider
    )
    assert entry["default_model"] == expected_model


def test_env_overrides_the_served_default(monkeypatch):
    """The flat pair still applies when no per-node pair is set, so a deployment
    pinning a chat model globally gets a matching preselection."""
    import utils.setup_env as setup_env

    monkeypatch.setattr(setup_env, "RAG_CHAT_NODE_LLM_ENV", {})
    monkeypatch.setattr(setup_env, "RAG_CHAT_LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(setup_env, "RAG_CHAT_LLM_MODEL", "claude-opus-4-6")

    served = get_chat_model_catalog()
    anthropic = next(e for e in served if e["provider_name"] == "anthropic")
    assert anthropic["default_model"] == "claude-opus-4-6"

    # The module constant must be untouched — the override applies to the copy.
    static = next(
        e for e in CHAT_MODEL_CATALOG if e["provider_name"] == "anthropic"
    )
    assert static["default_model"] == "claude-sonnet-4-6"


def test_env_pinned_model_absent_from_the_catalog_is_still_selectable(monkeypatch):
    """A default the picker cannot select is a broken default, so an env-pinned
    model this catalog doesn't list gets added rather than left dangling."""
    import utils.setup_env as setup_env

    monkeypatch.setattr(setup_env, "RAG_CHAT_NODE_LLM_ENV", {})
    monkeypatch.setattr(setup_env, "RAG_CHAT_LLM_PROVIDER", "openai")
    monkeypatch.setattr(setup_env, "RAG_CHAT_LLM_MODEL", "gpt-6-unreleased")

    openai = next(
        e for e in get_chat_model_catalog() if e["provider_name"] == "openai"
    )
    assert openai["default_model"] == "gpt-6-unreleased"
    assert "gpt-6-unreleased" in [m["model_name"] for m in openai["models"]]


def test_unknown_env_provider_leaves_the_catalog_alone(monkeypatch):
    """RAG_CHAT_LLM_PROVIDER naming a provider llm_factory cannot build must not
    invent a catalog entry the picker would offer and the factory would reject."""
    import utils.setup_env as setup_env

    monkeypatch.setattr(setup_env, "RAG_CHAT_NODE_LLM_ENV", {})
    monkeypatch.setattr(setup_env, "RAG_CHAT_LLM_PROVIDER", "no-such-provider")
    monkeypatch.setattr(setup_env, "RAG_CHAT_LLM_MODEL", "whatever")

    served = get_chat_model_catalog()
    assert [e["provider_name"] for e in served] == [
        e["provider_name"] for e in CHAT_MODEL_CATALOG
    ]
    for got, expected in zip(served, CHAT_MODEL_CATALOG):
        assert got["default_model"] == expected["default_model"]


def test_every_provider_has_at_least_one_model():
    for entry in CHAT_MODEL_CATALOG:
        assert entry["models"], f"{entry['provider_name']} has no models"


def test_no_duplicate_providers_or_models():
    providers = [e["provider_name"] for e in CHAT_MODEL_CATALOG]
    assert len(providers) == len(set(providers)), "duplicate provider in catalog"
    for entry in CHAT_MODEL_CATALOG:
        names = [m["model_name"] for m in entry["models"]]
        assert len(names) == len(set(names)), f"duplicate model under {entry['provider_name']}"


def test_get_chat_model_catalog_returns_an_isolated_copy():
    """A caller mutating the response must not corrupt the module constant
    for every subsequent request — the catalog is a process-lifetime global."""
    first = get_chat_model_catalog()
    original_count = len(first[0]["models"])
    first[0]["models"].clear()
    first[0]["provider_name"] = "mutated"

    second = get_chat_model_catalog()
    assert len(second[0]["models"]) == original_count
    assert second[0]["provider_name"] != "mutated"


def test_is_known_chat_model():
    assert is_known_chat_model("openai", "gpt-5.4-mini") is True
    assert is_known_chat_model("openai", "no-such-model") is False
    assert is_known_chat_model("no-such-provider", "gpt-5.4-mini") is False


# ---------------------------------------------------------------------------
# The drift guard — catalog vs. what the factory can actually build
# ---------------------------------------------------------------------------

def test_catalog_providers_match_factory_known_providers():
    from chat.llm_factory import _KNOWN_PROVIDERS

    assert set(CATALOG_PROVIDERS) == set(_KNOWN_PROVIDERS), (
        "The catalog advertises providers llm_factory cannot build (or vice "
        "versa). _KNOWN_PROVIDERS is derived from CATALOG_PROVIDERS — if this "
        "fails, someone re-hardcoded one of them."
    )


def test_every_catalog_pair_round_trips_through_normalize():
    """normalize_provider_and_model reroutes mislabeled ids (Groq-hosted OSS
    models tagged openai, gemini aliases). Every pair we advertise must
    survive it unchanged, or the picker's selection is not what runs."""
    from chat.llm_factory import normalize_provider_and_model

    for entry in CHAT_MODEL_CATALOG:
        for model in entry["models"]:
            provider, name = entry["provider_name"], model["model_name"]
            got_provider, got_model = normalize_provider_and_model(provider, name)
            assert (got_provider, got_model) == (provider, name), (
                f"{provider}/{name} normalizes to {got_provider}/{got_model}"
            )


def test_every_catalog_provider_has_a_create_llm_branch():
    """_create_llm's else-branch silently falls back to OpenAI. A provider
    reaching it would look configured and answer on the wrong model."""
    import inspect
    from chat import llm_factory

    source = inspect.getsource(llm_factory._create_llm)
    for provider in CATALOG_PROVIDERS:
        assert f'"{provider}"' in source, (
            f"_create_llm has no branch for {provider!r} — it would hit the "
            f"OpenAI fallback"
        )


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    import routers.knowledge_config_new as router_module
    from utils.auth.dependencies import get_api_key_session_or_scoped_key

    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[get_api_key_session_or_scoped_key] = lambda: {"auth_type": "master_api_key"}
    return TestClient(app)


def test_chat_models_endpoint_returns_the_catalog(client):
    response = client.get("/v1/chat-models")
    assert response.status_code == 200

    providers = response.json()["providers"]
    assert [p["provider_name"] for p in providers] == [
        e["provider_name"] for e in CHAT_MODEL_CATALOG
    ], "provider order must be stable — the dashboard renders them in this order"

    for served, expected in zip(providers, CHAT_MODEL_CATALOG):
        assert served["default_model"] == expected["default_model"]
        assert [m["model_name"] for m in served["models"]] == [
            m["model_name"] for m in expected["models"]
        ]


def test_chat_models_are_objects_not_bare_strings(client):
    """Models are objects so per-model tags (G8: recommended / faster) can be
    added without breaking callers. A regression to bare strings is breaking."""
    providers = client.get("/v1/chat-models").json()["providers"]
    for provider in providers:
        for model in provider["models"]:
            assert isinstance(model, dict) and "model_name" in model


def test_chat_models_is_distinct_from_embedding_models(client):
    """Two unrelated lists on one router — a chat model must never appear in
    the embedding list, which is write-once per collection."""
    chat_names = {
        m["model_name"]
        for p in client.get("/v1/chat-models").json()["providers"]
        for m in p["models"]
    }
    embedding_names = set(client.get("/v1/embedding-models").json()["embedding_models"])
    assert not (chat_names & embedding_names)
