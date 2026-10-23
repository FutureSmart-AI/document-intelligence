"""Unit tests for GET /v1 (routers/knowledge_management.py::list_knowledge)
including shared collections.

Confirmed via the UI's Share flow: the frontend
calls `GET /v1?owner_id=…` expecting every collection the account can reach
(owned + shared), then splits them into "My collections" / "Shared with me"
via a per-id `GET /v1/validate` call — see
docs/plan/access-and-sharing/decisions.md, 2026-08-25. Before that fix,
this endpoint only ever returned owned collections
(`get_knowledge_services_by_owner`), so an invited user's shared collections
never appeared in this list at all.

Pure unit tests: get_api_key_or_session is dependency-overridden, and every
utils/db function the router calls is monkeypatched — no real DB call.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.knowledge_management as knowledge_management_module

OWNER_EMAIL = "user@futuresmart.ai"


def _make_app(auth=None):
    app = FastAPI()
    app.include_router(knowledge_management_module.router)
    app.dependency_overrides[knowledge_management_module.get_api_key_or_session] = (
        lambda: (auth or {"auth_type": "session", "session_data": {"email": OWNER_EMAIL}})
    )
    return app


@pytest.fixture
def client():
    return TestClient(_make_app())


def _patch_owned(monkeypatch, ids):
    async def _fake(owner_id):
        return ids
    monkeypatch.setattr(knowledge_management_module, "get_knowledge_services_by_owner", _fake)


def _patch_shared(monkeypatch, rows):
    async def _fake(user_email):
        return rows
    monkeypatch.setattr(knowledge_management_module, "list_shared_knowledge", _fake)


def test_list_knowledge_includes_owned_and_shared(monkeypatch, client):
    _patch_owned(monkeypatch, ["k-owned-1"])
    _patch_shared(monkeypatch, [
        {"knowledge_id": "k-shared-1", "collection_name": "Docs", "role": "viewer",
         "invited_by_email": "owner2@futuresmart.ai", "invited_at": "2026-08-25T07:15:11+00:00"},
    ])
    resp = client.get("/v1", params={"owner_id": OWNER_EMAIL})
    assert resp.status_code == 200
    assert resp.json()["knowledge_services"] == ["k-owned-1", "k-shared-1"]
    assert resp.json()["count"] == 2


def test_list_knowledge_dedupes_owned_and_shared(monkeypatch, client):
    """A collection can never legitimately be both, but a stale/duplicate
    invitation row must not double-list one."""
    _patch_owned(monkeypatch, ["k-1"])
    _patch_shared(monkeypatch, [
        {"knowledge_id": "k-1", "collection_name": "Docs", "role": "viewer",
         "invited_by_email": "x@futuresmart.ai", "invited_at": None},
    ])
    resp = client.get("/v1", params={"owner_id": OWNER_EMAIL})
    assert resp.json()["knowledge_services"] == ["k-1"]


def test_list_knowledge_no_shared_is_owned_only(monkeypatch, client):
    _patch_owned(monkeypatch, ["k-owned-1", "k-owned-2"])
    _patch_shared(monkeypatch, [])
    resp = client.get("/v1", params={"owner_id": OWNER_EMAIL})
    assert resp.json()["knowledge_services"] == ["k-owned-1", "k-owned-2"]


def test_list_knowledge_session_cannot_list_other_owner(client):
    resp = client.get("/v1", params={"owner_id": "someone-else@futuresmart.ai"})
    assert resp.status_code == 403
