"""Router-level tests for both `/enrichment/generate` endpoints.

Covers the two things added for the collection-creation wizard's Step 1 (free
text -> fields) and CC5 (propose-without-persisting):

  1. `POST /v1/enrichment/generate` — NEW, owner-scoped, no knowledge_id. The
     Step 1 counterpart to the existing knowledge-scoped endpoint, mirroring
     `POST /v1/enrichment/preview`'s auth/quota pattern. See
     utils/config/field_generation.py::run_generate_preview.
  2. `POST /v1/{knowledge_id}/enrichment/generate?persist=false` — CC5. Same
     generation, same validation, no writes. What matters here is the
     DIFFERENTIAL: which DB-writing calls do NOT happen when persist=false,
     compared to the unchanged default (persist=true) behavior.

Both use fake auth (no real session/DB) and a monkeypatched generate_field_schema
so no real LLM call happens — same isolation discipline as
test_enrichment_preview.py.
"""

from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.enrichment_configuration_new as router_module
import utils.auth.dependencies as auth_deps
from utils.config.field_generation import FieldGenerationResult

OWNER_EMAIL = "owner@futuresmart.ai"


def _fake_result(**overrides) -> FieldGenerationResult:
    defaults = dict(
        accepted_fields=[{
            "field_name": "vendor_name", "field_type": "str", "field_type_label": "Text",
            "description": "Vendor's legal name", "level": "document", "is_custom": True,
            "is_required": False, "restrict_to_choices": False, "sample_values": None, "max_items": None,
        }],
        rejected_fields=[],
        provider="openai", model="gpt-5.4-mini", key_type="platform-key",
        input_tokens=500, output_tokens=120,
    )
    defaults.update(overrides)
    return FieldGenerationResult(**defaults)


# ---------------------------------------------------------------------------
# POST /v1/enrichment/generate — owner-scoped (Step 1)
# ---------------------------------------------------------------------------

@pytest.fixture
def owner_scoped_client(monkeypatch):
    """Session-authenticated client for the owner-scoped route only —
    dependency_overrides works here because Depends(get_api_key_or_session) is
    bound directly in that route's signature."""
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[router_module.get_api_key_or_session] = lambda: {
        "auth_type": "session",
        "session_data": {"email": OWNER_EMAIL},
    }
    return TestClient(app)


def test_generate_preview_route_is_registered_and_not_knowledge_scoped():
    paths = {route.path for route in router_module.router.routes}
    assert "/v1/enrichment/generate" in paths
    assert "/v1/{knowledge_id}/enrichment/generate" in paths


def test_owner_scoped_generate_master_key_is_rejected():
    """No knowledge_id exists yet to charge a master key's spend to — same
    reasoning as the Step 3 preview endpoint's _require_session_owner_id."""
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[router_module.get_api_key_or_session] = lambda: {
        "auth_type": "master_api_key", "api_key": "k",
    }
    client = TestClient(app)
    resp = client.post("/v1/enrichment/generate", data={"service_description": "Vendor invoices"})
    assert resp.status_code == 401


def test_owner_scoped_generate_full_round_trip(owner_scoped_client, monkeypatch):
    calls = {"enforced": [], "recorded": []}

    async def _fake_enforce(owner_id, estimated_tokens):
        calls["enforced"].append(owner_id)

    async def _fake_record(**kwargs):
        calls["recorded"].append(kwargs)

    async def _fake_generate(**kwargs):
        assert kwargs["owner_id"] == OWNER_EMAIL
        assert kwargs["knowledge_id"] is None  # deferred creation — no collection yet
        return _fake_result()

    import utils.config.field_generation as field_generation
    monkeypatch.setattr(field_generation, "_enforce_generation_quota", _fake_enforce)
    monkeypatch.setattr(field_generation, "_record_generation_usage", _fake_record)
    monkeypatch.setattr(field_generation, "generate_field_schema", _fake_generate)

    resp = owner_scoped_client.post(
        "/v1/enrichment/generate",
        data={
            "collection_name": "Vendor Invoices",
            "service_description": "Invoices from vendors",
            "extraction_instructions": "filter by vendor and amount",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["proposed_fields"][0]["field_name"] == "vendor_name"
    assert "default_field_verdicts" not in body
    assert body["run"]["persisted"] is False
    assert len(calls["enforced"]) == 1
    assert len(calls["recorded"]) == 1


def test_owner_scoped_generate_quota_refusal_is_429(owner_scoped_client, monkeypatch):
    from fastapi import HTTPException

    async def _fake_enforce(owner_id, estimated_tokens):
        raise HTTPException(status_code=429, detail="over budget")

    import utils.config.field_generation as field_generation
    monkeypatch.setattr(field_generation, "_enforce_generation_quota", _fake_enforce)

    resp = owner_scoped_client.post("/v1/enrichment/generate", data={"service_description": "x"})
    assert resp.status_code == 429


# ---------------------------------------------------------------------------
# POST /v1/enrichment/generate-upload-url — owner-scoped, feeds `file_url` above
# ---------------------------------------------------------------------------

def test_upload_url_master_key_is_rejected():
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[router_module.get_api_key_or_session] = lambda: {
        "auth_type": "master_api_key", "api_key": "k",
    }
    client = TestClient(app)
    resp = client.post("/v1/enrichment/generate-upload-url", data={"file_name": "invoice.pdf", "content_type": "application/pdf"})
    assert resp.status_code == 401


def test_upload_url_is_not_shadowed_by_the_knowledge_scoped_route(monkeypatch):
    """Regression test for a real bug, live-confirmed 2026-09-09: this router's
    POST /v1/enrichment/upload-url (3 path segments) was silently shadowed by
    routers/document_management.py's POST /v1/{knowledge_id}/upload-url — same
    shape, and that router is included in main.py BEFORE this one, so Starlette
    matched every call to the OLD handler with knowledge_id bound to the literal
    string "enrichment". A single-router TestClient (every other test in this
    file) can never catch this, because the shadowing route doesn't exist in
    isolation — this test mounts both routers in main.py's actual order and
    proves the request reaches the FastAPI generate-upload-url handler.

    Renamed to /v1/enrichment/generate-upload-url to break the shape collision
    (see the router's own docstring). If this test starts failing because
    someone reintroduced a same-shape path, that's the bug this test exists to
    catch — fix the path, don't loosen the assertion.
    """
    import routers.document_management as document_management_module

    async def _fake_presign(object_key, content_type):
        return f"https://s3.example/upload/{object_key}", f"https://s3.example/download/{object_key}"

    monkeypatch.setattr(document_management_module, "generate_s3_presigned_urls", _fake_presign)

    # require_permission("write")'s dependency (used by the OLD route) calls
    # utils.auth.dependencies.get_api_key_or_session directly rather than through
    # FastAPI's Depends(), so app.dependency_overrides can't reach it — same gap
    # documented in test_generate_router.py's persist=false fixture above. Patch
    # the module attribute instead; a master key makes it short-circuit to None
    # (bypass) without any DB call.
    async def _fake_auth(request, api_key=None):
        return {"auth_type": "master_api_key", "api_key": "k"}
    monkeypatch.setattr(auth_deps, "get_api_key_or_session", _fake_auth)

    app = FastAPI()
    # Same order as main.py: document_management_router before enrichment_configuration_router_new.
    app.include_router(document_management_module.router)
    app.include_router(router_module.router)
    app.dependency_overrides[router_module.get_api_key_or_session] = lambda: {
        "auth_type": "master_api_key", "api_key": "k",
    }
    client = TestClient(app)

    # The knowledge-scoped route's own behavior must be undisturbed: a master
    # key bypasses require_permission entirely, so this is a real 200.
    old_resp = client.post(
        "/v1/kb-1/upload-url",
        data={"file_name": "doc.pdf", "content_type": "application/pdf"},
    )
    assert old_resp.status_code == 200

    # The new route must resolve to ITS OWN handler (_require_session_owner_id
    # rejects a master key with 401) — a 200 here would mean it's still being
    # shadowed by the route above.
    new_resp = client.post(
        "/v1/enrichment/generate-upload-url",
        data={"file_name": "doc.pdf", "content_type": "application/pdf"},
    )
    assert new_resp.status_code == 401


def test_upload_url_returns_a_presigned_pair(owner_scoped_client, monkeypatch):
    async def _fake_presign(object_key, content_type):
        assert "generate_preview_uploads/" in object_key
        assert content_type == "application/pdf"
        return f"https://s3.example/upload/{object_key}", f"https://s3.example/download/{object_key}"

    monkeypatch.setattr(router_module, "generate_s3_presigned_urls", _fake_presign)

    resp = owner_scoped_client.post(
        "/v1/enrichment/generate-upload-url",
        data={"file_name": "invoice.pdf", "content_type": "application/pdf"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["object_id"]
    assert body["presigned_url"].startswith("https://s3.example/upload/")
    assert body["download_presigned_url"].startswith("https://s3.example/download/")


def test_upload_url_rejects_unsupported_content_type(owner_scoped_client):
    resp = owner_scoped_client.post(
        "/v1/enrichment/generate-upload-url",
        data={"file_name": "malware.exe", "content_type": "application/x-msdownload"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /v1/enrichment/generate with file_url — the one-file peek path
# ---------------------------------------------------------------------------

def test_generate_peeks_the_file_url_and_feeds_doc_previews(owner_scoped_client, monkeypatch):
    async def _fake_extract(file_url):
        return "invoice.pdf", "application/pdf", "obj-1"

    async def _fake_enforce(owner_id, estimated_tokens):
        pass

    async def _fake_record(**kwargs):
        pass

    seen = {}

    async def _fake_generate(**kwargs):
        seen["doc_previews"] = kwargs["doc_previews"]
        return _fake_result()

    import utils.config.field_generation as field_generation
    monkeypatch.setattr(router_module, "extract_filename_and_content_type", _fake_extract)
    monkeypatch.setattr(field_generation, "_enforce_generation_quota", _fake_enforce)
    monkeypatch.setattr(field_generation, "_record_generation_usage", _fake_record)
    monkeypatch.setattr(field_generation, "generate_field_schema", _fake_generate)
    monkeypatch.setattr(router_module, "peek_document_text", lambda content_type, name, raw: "peeked text")

    class _FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        async def aread(self):
            return b"%PDF-1.4 fake bytes"

    class _FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, follow_redirects=True):
            return _FakeStream()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    resp = owner_scoped_client.post(
        "/v1/enrichment/generate",
        data={"service_description": "Vendor invoices", "file_url": "https://s3.example/download/obj-1_invoice.pdf"},
    )
    assert resp.status_code == 200, resp.text
    assert seen["doc_previews"] == [("invoice.pdf", "peeked text")]


def test_generate_with_no_file_url_sends_no_doc_previews(owner_scoped_client, monkeypatch):
    seen = {}

    async def _fake_generate(**kwargs):
        seen["doc_previews"] = kwargs["doc_previews"]
        return _fake_result()

    async def _fake_enforce(owner_id, estimated_tokens):
        pass

    async def _fake_record(**kwargs):
        pass

    import utils.config.field_generation as field_generation
    monkeypatch.setattr(field_generation, "_enforce_generation_quota", _fake_enforce)
    monkeypatch.setattr(field_generation, "_record_generation_usage", _fake_record)
    monkeypatch.setattr(field_generation, "generate_field_schema", _fake_generate)

    resp = owner_scoped_client.post("/v1/enrichment/generate", data={"service_description": "Vendor invoices"})
    assert resp.status_code == 200, resp.text
    assert seen["doc_previews"] == []


# ---------------------------------------------------------------------------
# POST /v1/{knowledge_id}/enrichment/generate?persist=false — CC5
# ---------------------------------------------------------------------------

KNOWLEDGE_ID = "kb-123"


@pytest.fixture
def knowledge_scoped_client(monkeypatch):
    """Master-key auth for the knowledge-scoped route.

    require_permission("write")'s inner dependency short-circuits to `None`
    for a master key without touching the DB, so no ownership/invitation
    lookups are needed here. app.dependency_overrides can't target this
    closure directly (it isn't the object Depends() was given — see
    utils/auth/dependencies.py::require_permission), so the module-level
    get_api_key_or_session is monkeypatched instead: the closure looks that
    name up from utils.auth.dependencies's own globals at call time, so
    patching the attribute there is visible to it.
    """
    async def _fake_auth(request, api_key=None):
        return {"auth_type": "master_api_key", "api_key": "master"}
    monkeypatch.setattr(auth_deps, "get_api_key_or_session", _fake_auth)

    app = FastAPI()
    app.include_router(router_module.router)
    return TestClient(app)


@pytest.fixture
def knowledge_scoped_db_spies(monkeypatch):
    """Stub every DB-touching helper the knowledge-scoped endpoint calls, and
    record which ones actually ran."""
    calls: Dict[str, List[Any]] = {
        "seed_defaults": [], "save_config": [], "sync": [],
    }

    async def _knowledge_exists(knowledge_id):
        return True

    async def _get_config(knowledge_id):
        return {
            "embedding_model": "text-embedding-3-small",
            "owner_id": OWNER_EMAIL,
            "collection_name": "Vendor Invoices",
            "service_description": "Invoices from vendors",
            "chunk_level_metadata": False,
        }

    async def _seed_defaults(knowledge_id):
        calls["seed_defaults"].append(knowledge_id)

    async def _get_fields(knowledge_id):
        return [
            {"id": 10, "field_name": "topics", "level": "document", "is_active": True},
            {"id": 11, "field_name": "title", "level": "document", "is_active": True},
        ]

    async def _save_config(**kwargs):
        calls["save_config"].append(kwargs)

    async def _sync(knowledge_id, payload, delete_missing=False):
        calls["sync"].append((knowledge_id, payload))
        return {"created": len(payload), "updated": 0}

    async def _fake_generate(**kwargs):
        calls.setdefault("generate_kwargs", []).append(kwargs)
        return _fake_result()

    monkeypatch.setattr(router_module, "knowledge_exists_check", _knowledge_exists)
    monkeypatch.setattr(router_module, "get_knowledge_config", _get_config)
    monkeypatch.setattr(router_module, "initialize_default_field_configurations_sqlmodel", _seed_defaults)
    monkeypatch.setattr(router_module, "get_field_configurations_sqlmodel_v2", _get_fields)
    monkeypatch.setattr(router_module, "save_knowledge_config", _save_config)
    monkeypatch.setattr(router_module, "sync_field_configurations_sqlmodel_v2", _sync)
    monkeypatch.setattr(router_module, "generate_field_schema", _fake_generate)
    return calls


def test_persist_false_writes_nothing(knowledge_scoped_client, knowledge_scoped_db_spies):
    resp = knowledge_scoped_client.post(
        f"/v1/{KNOWLEDGE_ID}/enrichment/generate?persist=false",
        data={"extraction_instructions": "filter by vendor"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert knowledge_scoped_db_spies["seed_defaults"] == []
    assert knowledge_scoped_db_spies["save_config"] == []
    assert knowledge_scoped_db_spies["sync"] == []

    assert body["run"]["persisted"] is False
    assert body["sync_summary"] == {"created": 0, "updated": 0}
    # Response shape (key names) matches the persist=true path — see below —
    # so a caller does not have to branch on which fields are present.
    assert body["created_fields"] == ["vendor_name"]
    assert "nothing saved" in body["message"]


def test_persist_true_is_the_unchanged_default_behavior(knowledge_scoped_client, knowledge_scoped_db_spies):
    """Same request, no persist param — must still write, exactly as before
    this change. Guards against the default flipping by accident."""
    resp = knowledge_scoped_client.post(
        f"/v1/{KNOWLEDGE_ID}/enrichment/generate",
        data={"extraction_instructions": "filter by vendor"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert knowledge_scoped_db_spies["seed_defaults"] == [KNOWLEDGE_ID]
    assert knowledge_scoped_db_spies["save_config"] != []
    assert knowledge_scoped_db_spies["sync"] != []

    assert body["run"]["persisted"] is True
    assert body["created_fields"] == ["vendor_name"]
    assert "nothing saved" not in body["message"]


def test_knowledge_scoped_generate_passes_existing_names_and_leaves_defaults_alone(
    knowledge_scoped_client, knowledge_scoped_db_spies
):
    """The collection's own field names reach the generator (so a clash is
    rejected, not overwritten), and no default field is toggled - with chunk
    metadata off and no chunk fields present, the only synced row is the new
    field."""
    resp = knowledge_scoped_client.post(
        f"/v1/{KNOWLEDGE_ID}/enrichment/generate",
        data={"extraction_instructions": "filter by vendor"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert knowledge_scoped_db_spies["generate_kwargs"][0]["existing_field_names"] == ["topics", "title"]
    assert "default_field_verdicts" not in body
    assert len(knowledge_scoped_db_spies["sync"]) == 1
    _, payload = knowledge_scoped_db_spies["sync"][0]
    assert [f["field_name"] for f in payload] == ["vendor_name"]
