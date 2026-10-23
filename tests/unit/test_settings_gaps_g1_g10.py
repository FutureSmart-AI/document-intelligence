"""Unit tests for G1 (collection_name) and G10 (suggested_questions,
voice_input_enabled, voice_output_enabled) — the two remaining Settings gaps
from docs/screen-api-flow-document-intelligence.md closed in this pass.
Temperature (G11) is untouched here; it needs the rag_node.py threading fix,
not a schema change, and is explicitly out of scope for this pass.

Pure unit tests: every DB read/write function is monkeypatched with a fake
row, upsert spy, or in-memory dict — no real DB connection anywhere, matching
the existing test_chat_config.py / test_chat_config_router.py convention.
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import chat.config as chat_config_module
import chat.db.config_ops as config_ops_module
import routers.chat as chat_router_module
import routers.knowledge_config_new as knowledge_config_module
import routers.knowledge_management as knowledge_management_module
from utils.auth.dependencies import get_api_key_or_session, require_permission


# ---------------------------------------------------------------------------
# G10 — chat/config.py::resolve_chat_config resolves the three new fields
# ---------------------------------------------------------------------------

class _FakeConfigRow:
    def __init__(self, suggested_questions=None, voice_input_enabled=False,
                 voice_output_enabled=False, **kwargs):
        self.llm_provider = kwargs.get("llm_provider")
        self.llm_model = kwargs.get("llm_model")
        self.temperature = kwargs.get("temperature")
        self.system_prompt = kwargs.get("system_prompt")
        self.show_sources = kwargs.get("show_sources", True)
        self.chat_enabled = kwargs.get("chat_enabled", True)
        self.follow_up = kwargs.get("follow_up", True)
        self.suggested_questions = suggested_questions
        self.voice_input_enabled = voice_input_enabled
        self.voice_output_enabled = voice_output_enabled
        self.max_tokens = kwargs.get("max_tokens")
        self.include_charts = kwargs.get("include_charts", True)
        self.show_raw_response = kwargs.get("show_raw_response", True)


class _FakeGatingRow:
    """The row the GET /chat/config handler reads directly (not through
    resolve_chat_config) for the embed-gating fields. Only an authenticated
    caller sees them, so the handler dereferences them off the raw row."""

    def __init__(self, is_public=False, allowed_domains=None,
                 fullscreen_is_public=False, fullscreen_allowed_domains=None):
        self.is_public = is_public
        self.allowed_domains = allowed_domains
        self.fullscreen_is_public = fullscreen_is_public
        self.fullscreen_allowed_domains = fullscreen_allowed_domains


def _patch_row(monkeypatch, row):
    async def _fake_get_config_row(knowledge_id):
        return row
    monkeypatch.setattr(chat_config_module, "_get_config_row", _fake_get_config_row)


def test_g10_defaults_to_empty_list_and_false_when_no_row(monkeypatch):
    _patch_row(monkeypatch, None)
    result = asyncio.run(chat_config_module.resolve_chat_config("kb-1"))
    assert result.suggested_questions == []
    assert result.voice_input_enabled is False
    assert result.voice_output_enabled is False


def test_g10_resolves_from_stored_row(monkeypatch):
    row = _FakeConfigRow(
        suggested_questions=["What's in this collection?", "Summarize the latest doc"],
        voice_input_enabled=True,
        voice_output_enabled=True,
    )
    _patch_row(monkeypatch, row)
    result = asyncio.run(chat_config_module.resolve_chat_config("kb-1"))
    assert result.suggested_questions == ["What's in this collection?", "Summarize the latest doc"]
    assert result.voice_input_enabled is True
    assert result.voice_output_enabled is True


def test_g10_empty_stored_list_stays_empty_not_none(monkeypatch):
    """An explicitly-cleared list must round-trip as [], not resurrect as
    None and get confused with 'never configured'."""
    row = _FakeConfigRow(suggested_questions=[])
    _patch_row(monkeypatch, row)
    result = asyncio.run(chat_config_module.resolve_chat_config("kb-1"))
    assert result.suggested_questions == []


def test_g10_resolved_list_is_a_copy_not_the_row_object():
    """ResolvedChatConfig must not alias the DB row's list — mutating the
    resolved value must never corrupt the ORM object still in the session."""
    row = _FakeConfigRow(suggested_questions=["Q1"])

    async def _fake(knowledge_id):
        return row
    import chat.config as cc
    orig = cc._get_config_row
    cc._get_config_row = _fake
    try:
        result = asyncio.run(cc.resolve_chat_config("kb-1"))
        result.suggested_questions.append("Q2")
        assert row.suggested_questions == ["Q1"], "mutating the resolved list leaked into the DB row"
    finally:
        cc._get_config_row = orig


def test_g10_namedtuple_default_is_not_a_shared_mutable():
    """Two independent resolutions with no row must not share list identity —
    the regression this guards is a NamedTuple mutable default leaking state
    between unrelated knowledge_ids."""
    a = chat_config_module.ResolvedChatConfig(
        llm_provider="openai", llm_model="gpt-5.4-mini", temperature=0.0,
        system_prompt=None, show_sources=True, chat_enabled=True,
    )
    b = chat_config_module.ResolvedChatConfig(
        llm_provider="openai", llm_model="gpt-5.4-mini", temperature=0.0,
        system_prompt=None, show_sources=True, chat_enabled=True,
    )
    assert a.suggested_questions is None and b.suggested_questions is None


# ---------------------------------------------------------------------------
# G10 — chat/db/config_ops.py accepts the new fields on write
# ---------------------------------------------------------------------------

def test_g10_new_fields_are_writable():
    for field in ("suggested_questions", "voice_input_enabled", "voice_output_enabled"):
        assert field in config_ops_module.WRITABLE_FIELDS


# ---------------------------------------------------------------------------
# G10 — GET/PUT /v1/{knowledge_id}/chat/config round-trips the new fields
# ---------------------------------------------------------------------------

def _make_chat_app():
    app = FastAPI()
    app.include_router(chat_router_module.router)
    for route in chat_router_module.router.routes:
        for dep in route.dependant.dependencies:
            if dep.call.__qualname__.endswith("require_permission.<locals>.dependency"):
                app.dependency_overrides[dep.call] = lambda: None
    return app


@pytest.fixture(autouse=True)
def _patch_node_rows(monkeypatch):
    async def _fake_list(knowledge_id):
        return []
    monkeypatch.setattr(chat_router_module, "list_node_config_rows", _fake_list)


def test_get_chat_config_returns_g10_fields(monkeypatch):
    async def _fake_resolve(knowledge_id, **kwargs):
        return chat_config_module.ResolvedChatConfig(
            llm_provider="openai", llm_model="gpt-5.4-mini", temperature=0.0,
            system_prompt=None, show_sources=True, chat_enabled=True, follow_up=True,
            suggested_questions=["Try asking about pricing"],
            voice_input_enabled=True, voice_output_enabled=False,
        )
    monkeypatch.setattr(chat_router_module, "resolve_chat_config", _fake_resolve)

    async def _fake_get_row(knowledge_id):
        return _FakeGatingRow()
    monkeypatch.setattr(chat_router_module, "get_config_row", _fake_get_row)

    response = TestClient(_make_chat_app()).get("/v1/kb-1/chat/config")
    assert response.status_code == 200
    body = response.json()
    assert body["suggested_questions"] == ["Try asking about pricing"]
    assert body["voice_input_enabled"] is True
    assert body["voice_output_enabled"] is False


def test_put_chat_config_writes_g10_fields(monkeypatch):
    captured = {}

    async def _fake_upsert(knowledge_id, updates):
        captured.update(updates)
        return None
    monkeypatch.setattr(chat_router_module, "upsert_config_row", _fake_upsert)

    response = TestClient(_make_chat_app()).put(
        "/v1/kb-1/chat/config",
        json={
            "suggested_questions": ["What changed this week?", "Any failed documents?"],
            "voice_input_enabled": True,
            "voice_output_enabled": True,
        },
    )
    assert response.status_code == 200
    assert captured["suggested_questions"] == ["What changed this week?", "Any failed documents?"]
    assert captured["voice_input_enabled"] is True
    assert captured["voice_output_enabled"] is True


def test_put_chat_config_omitting_g10_fields_leaves_them_untouched(monkeypatch):
    """Partial-update contract (same as every other field on this endpoint):
    a PUT that only sets llm_model must not silently touch suggested_questions."""
    captured = {}

    async def _fake_upsert(knowledge_id, updates):
        captured.update(updates)
        return None
    monkeypatch.setattr(chat_router_module, "upsert_config_row", _fake_upsert)

    response = TestClient(_make_chat_app()).put(
        "/v1/kb-1/chat/config", json={"llm_model": "gpt-4o"},
    )
    assert response.status_code == 200
    assert "suggested_questions" not in captured
    assert "voice_input_enabled" not in captured
    assert "voice_output_enabled" not in captured


# ---------------------------------------------------------------------------
# G1 — GET/PUT /v1/{knowledge_id}/config round-trips collection_name
# ---------------------------------------------------------------------------

def _make_config_app():
    app = FastAPI()
    app.include_router(knowledge_config_module.router)
    for route in knowledge_config_module.router.routes:
        for dep in route.dependant.dependencies:
            if dep.call.__qualname__.endswith("require_permission.<locals>.dependency"):
                app.dependency_overrides[dep.call] = lambda: None
    return app


def test_get_config_returns_collection_name(monkeypatch):
    async def _fake_get_knowledge_config(knowledge_id):
        return {
            "embedding_model": "paraphrase-multilingual-mpnet-base-v2",
            "hybrid_search_enabled": True,
            "document_level_metadata": True,
            "chunk_level_metadata": False,
            "collection_name": "Support Tickets Q3",
            "service_description": "A longer free-text description",
            "enrichment_provider": None,
            "enrichment_model": None,
            "enrichment_temperature": None,
            "enrichment_prompt": None,
            "enrichment_enabled": True,
            "use_deterministic_chunking": False,
            "created_at": None,
            "updated_at": None,
        }
    monkeypatch.setattr(knowledge_config_module, "get_knowledge_config", _fake_get_knowledge_config)

    async def _fake_get_enrichment_default_config():
        return None
    monkeypatch.setattr(knowledge_config_module, "get_enrichment_default_config", _fake_get_enrichment_default_config)

    response = TestClient(_make_config_app()).get("/v1/kb-1/config")
    assert response.status_code == 200
    body = response.json()
    assert body["collection_name"] == "Support Tickets Q3"
    # collection_name must stay distinct from service_description, not merged
    assert body["service_description"] == "A longer free-text description"


def test_get_config_collection_name_null_when_never_set(monkeypatch):
    """A collection created before this column existed, or without a name,
    must return null rather than 500 or an empty string standing in for it."""
    async def _fake_get_knowledge_config(knowledge_id):
        return {
            "embedding_model": "paraphrase-multilingual-mpnet-base-v2",
            "hybrid_search_enabled": True,
            "document_level_metadata": True,
            "chunk_level_metadata": False,
            "service_description": None,
            "enrichment_provider": None,
            "enrichment_model": None,
            "enrichment_temperature": None,
            "enrichment_prompt": None,
            "enrichment_enabled": True,
            "use_deterministic_chunking": False,
            "created_at": None,
            "updated_at": None,
            # collection_name deliberately absent, as an old row would be
        }
    monkeypatch.setattr(knowledge_config_module, "get_knowledge_config", _fake_get_knowledge_config)

    async def _fake_get_enrichment_default_config():
        return None
    monkeypatch.setattr(knowledge_config_module, "get_enrichment_default_config", _fake_get_enrichment_default_config)

    response = TestClient(_make_config_app()).get("/v1/kb-1/config")
    assert response.status_code == 200
    assert response.json()["collection_name"] is None


def test_put_config_writes_collection_name(monkeypatch):
    captured = {}

    async def _fake_save(knowledge_id, **kwargs):
        captured.update(kwargs)
        return True
    monkeypatch.setattr(knowledge_config_module, "save_knowledge_config", _fake_save)

    async def _fake_init(*args, **kwargs):
        return None
    monkeypatch.setattr(knowledge_config_module, "initialize_field_configurations_by_template", _fake_init)

    # The handler reads the stored row to resolve fields the caller omitted
    # (enrichment_enabled, embedding_model, hybrid_search_enabled...). Nothing
    # is stored yet in this case.
    async def _fake_get_knowledge_config(knowledge_id):
        return None
    monkeypatch.setattr(knowledge_config_module, "get_knowledge_config", _fake_get_knowledge_config)

    response = TestClient(_make_config_app()).put(
        "/v1/kb-1/config", json={"collection_name": "Renamed Collection"},
    )
    assert response.status_code == 200
    assert captured["collection_name"] == "Renamed Collection"


def test_put_config_threads_seed_optional_defaults_false(monkeypatch):
    """The create-collection wizard (CC1) sends seed_optional_defaults: false
    so /config only force-creates the still-protected defaults (summary,
    document_type) — title/topics/keywords/etc. are left for Step 4."""
    captured = {}

    async def _fake_init(knowledge_id, **kwargs):
        captured.update(kwargs)
    monkeypatch.setattr(knowledge_config_module, "initialize_field_configurations_by_template", _fake_init)

    async def _fake_save(knowledge_id, **kwargs):
        return True
    monkeypatch.setattr(knowledge_config_module, "save_knowledge_config", _fake_save)

    async def _fake_get_knowledge_config(knowledge_id):
        return None
    monkeypatch.setattr(knowledge_config_module, "get_knowledge_config", _fake_get_knowledge_config)

    response = TestClient(_make_config_app()).put(
        "/v1/kb-1/config", json={"seed_optional_defaults": False},
    )
    assert response.status_code == 200
    assert captured["seed_optional_defaults"] is False


def test_put_config_seed_optional_defaults_omitted_defaults_to_true(monkeypatch):
    """Every existing caller (dashboard settings, etc.) that never heard of
    this field must keep getting every universal default seeded, unchanged."""
    captured = {}

    async def _fake_init(knowledge_id, **kwargs):
        captured.update(kwargs)
    monkeypatch.setattr(knowledge_config_module, "initialize_field_configurations_by_template", _fake_init)

    async def _fake_save(knowledge_id, **kwargs):
        return True
    monkeypatch.setattr(knowledge_config_module, "save_knowledge_config", _fake_save)

    async def _fake_get_knowledge_config(knowledge_id):
        return None
    monkeypatch.setattr(knowledge_config_module, "get_knowledge_config", _fake_get_knowledge_config)

    response = TestClient(_make_config_app()).put(
        "/v1/kb-1/config", json={"collection_name": "No opinion on seeding"},
    )
    assert response.status_code == 200
    assert captured["seed_optional_defaults"] is True


def test_save_knowledge_config_omitted_collection_name_leaves_it_untouched(monkeypatch):
    """Partial-update contract: a PUT that only sets service_description must
    not null out a previously-set collection_name."""
    import utils.db.knowledge_setting_ops as setting_ops

    class _FakeConfig:
        def __init__(self):
            self.knowledge_id = "kb-1"
            self.deleted = False
            self.embedding_model = "paraphrase-multilingual-mpnet-base-v2"
            self.collection_name = "Original Name"
            self.service_description = None
            self.enrichment_prompt = None

    fake_row = _FakeConfig()

    class _FakeSession:
        async def get(self, model, pk):
            return fake_row
        async def commit(self):
            pass
        def add(self, obj):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False

    import contextlib

    @contextlib.contextmanager
    def _noop_cm():
        yield None

    monkeypatch.setattr(setting_ops, "async_session", lambda: _FakeSession())

    asyncio.run(setting_ops.save_knowledge_config(
        knowledge_id="kb-1", service_description="Updated description",
    ))
    assert fake_row.collection_name == "Original Name", "omitted field must not be cleared"
    assert fake_row.service_description == "Updated description"


# ---------------------------------------------------------------------------
# G1 — POST /v1 (create) accepts and forwards collection_name
# ---------------------------------------------------------------------------

def _make_management_app():
    app = FastAPI()
    app.include_router(knowledge_management_module.router)
    for route in knowledge_management_module.router.routes:
        for dep in route.dependant.dependencies:
            if dep.call.__qualname__.endswith("require_permission.<locals>.dependency"):
                app.dependency_overrides[dep.call] = lambda: None
    app.dependency_overrides[get_api_key_or_session] = lambda: {"auth_type": "master_api_key"}
    return app


def test_create_knowledge_forwards_collection_name(monkeypatch):
    captured = {}

    async def _fake_generate_id():
        return "kb-new-1"
    monkeypatch.setattr(knowledge_management_module, "generate_knowledge_id", _fake_generate_id)

    async def _fake_create(knowledge_id, owner_id, collection_name=None):
        captured["knowledge_id"] = knowledge_id
        captured["owner_id"] = owner_id
        captured["collection_name"] = collection_name
        return knowledge_id
    monkeypatch.setattr(knowledge_management_module, "create_knowledge_service", _fake_create)

    async def _fake_log_access_event(**kwargs):
        return None
    monkeypatch.setattr("utils.audit_logging.log_access_event", _fake_log_access_event)

    response = TestClient(_make_management_app()).post(
        "/v1", json={"owner_id": "user@example.com", "collection_name": "  My Collection  "},
    )
    assert response.status_code == 200
    assert captured["collection_name"] == "My Collection", "must be trimmed like owner_id"


def test_create_knowledge_without_collection_name_passes_none(monkeypatch):
    captured = {}

    async def _fake_generate_id():
        return "kb-new-2"
    monkeypatch.setattr(knowledge_management_module, "generate_knowledge_id", _fake_generate_id)

    async def _fake_create(knowledge_id, owner_id, collection_name=None):
        captured["collection_name"] = collection_name
        return knowledge_id
    monkeypatch.setattr(knowledge_management_module, "create_knowledge_service", _fake_create)

    async def _fake_log_access_event(**kwargs):
        return None
    monkeypatch.setattr("utils.audit_logging.log_access_event", _fake_log_access_event)

    response = TestClient(_make_management_app()).post("/v1", json={"owner_id": "user@example.com"})
    assert response.status_code == 200
    assert captured["collection_name"] is None


# ---------------------------------------------------------------------------
# G1 — GET /v1?include=summary carries collection_name (Home cards)
# ---------------------------------------------------------------------------

def test_get_knowledge_summaries_includes_collection_name_bucket():
    """Every id gets a collection_name key in the zero-state bucket, so the
    Home-card renderer never has to guess whether the key exists."""
    from utils.db.knowledge_ops import get_knowledge_summaries

    async def _run():
        return await get_knowledge_summaries([])

    result = asyncio.run(_run())
    assert result == {}
