"""Unit tests for routers/knowledge_invitations.py (Share agent, Feature 6 —
docs/plan/access-and-sharing/archive/design-share-invitations.md).

Pure unit tests: require_permission and get_api_key_or_session are
dependency-overridden, and every utils/db/knowledge_invitations.py function
the router calls is monkeypatched — no real DB call in any test.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.knowledge_invitations as invitations_router_module

KNOWLEDGE_ID = "k-123"


def _make_app(auth=None):
    """Mount the router with every require_permission(...) dependency
    overridden (each call builds a distinct closure — same pattern as
    test_chat_config_router.py) and get_api_key_or_session overridden to a
    fixed auth dict."""
    app = FastAPI()
    app.include_router(invitations_router_module.router)
    for route in invitations_router_module.router.routes:
        for dep in route.dependant.dependencies:
            if dep.call.__qualname__.endswith("require_permission.<locals>.dependency"):
                app.dependency_overrides[dep.call] = lambda: None
    app.dependency_overrides[invitations_router_module.get_api_key_or_session] = (
        lambda: (auth or {"auth_type": "session", "session_data": {"email": "owner@futuresmart.ai"}})
    )
    return app


@pytest.fixture
def client():
    return TestClient(_make_app())


def _patch_create(monkeypatch, result):
    async def _fake(knowledge_id, actor_email, invitations):
        return result
    monkeypatch.setattr(invitations_router_module, "db_create_invitations", _fake)


def _patch_list(monkeypatch, rows):
    async def _fake(knowledge_id):
        return rows
    monkeypatch.setattr(invitations_router_module, "db_list_invitations", _fake)


def _patch_delete(monkeypatch, deleted):
    async def _fake(knowledge_id, user_email):
        return deleted
    monkeypatch.setattr(invitations_router_module, "db_delete_invitation", _fake)


def _patch_update_role(monkeypatch, updated):
    async def _fake(knowledge_id, user_email, role, actor=None):
        return updated
    monkeypatch.setattr(invitations_router_module, "db_update_invitation_role", _fake)


def _patch_shared(monkeypatch, rows):
    captured = {}

    async def _fake(user_email):
        captured["user_email"] = user_email
        return rows
    monkeypatch.setattr(invitations_router_module, "db_list_shared_knowledge", _fake)
    return captured


# ---------------------------------------------------------------------------
# POST — create invitations
# ---------------------------------------------------------------------------

def test_create_invitations_success(monkeypatch, client):
    _patch_create(monkeypatch, {
        "invited_users": [{"email": "editor@futuresmart.ai", "role": "editor"}],
        "failed_emails": [],
    })
    resp = client.post(
        f"/v1/{KNOWLEDGE_ID}/invitations",
        json={"invitations": [{"email": "editor@futuresmart.ai", "role": "editor"}]},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["message"] == "Invitations sent successfully"
    assert body["invited_users"] == [{"email": "editor@futuresmart.ai", "role": "editor"}]
    assert body["failed_emails"] == []


def test_create_invitations_partial_failure_message(monkeypatch, client):
    _patch_create(monkeypatch, {
        "invited_users": [{"email": "editor@futuresmart.ai", "role": "editor"}],
        "failed_emails": ["already-invited@futuresmart.ai"],
    })
    resp = client.post(
        f"/v1/{KNOWLEDGE_ID}/invitations",
        json={"invitations": [
            {"email": "editor@futuresmart.ai", "role": "editor"},
            {"email": "already-invited@futuresmart.ai", "role": "viewer"},
        ]},
    )
    assert resp.status_code == 201
    assert resp.json()["message"] == "Invitations sent with some failures"


def test_create_invitations_returns_failures_with_reason(monkeypatch, client):
    """The UI's Share flow reads `failures[].reason`
    to explain why a specific address wasn't granted access — `failed_emails`
    alone can't distinguish "already invited" from anything else."""
    _patch_create(monkeypatch, {
        "invited_users": [],
        "failed_emails": ["already-invited@futuresmart.ai"],
        "failures": [{"email": "already-invited@futuresmart.ai", "reason": "already invited"}],
    })
    resp = client.post(
        f"/v1/{KNOWLEDGE_ID}/invitations",
        json={"invitations": [{"email": "already-invited@futuresmart.ai", "role": "viewer"}]},
    )
    assert resp.status_code == 201
    assert resp.json()["failures"] == [
        {"email": "already-invited@futuresmart.ai", "reason": "already invited"}
    ]


def test_create_invitations_omitted_failures_defaults_empty(monkeypatch, client):
    """A db layer that hasn't been updated to return `failures` yet (or a
    test double that omits it) must not 500 — the router falls back to []."""
    _patch_create(monkeypatch, {
        "invited_users": [{"email": "editor@futuresmart.ai", "role": "editor"}],
        "failed_emails": [],
    })
    resp = client.post(
        f"/v1/{KNOWLEDGE_ID}/invitations",
        json={"invitations": [{"email": "editor@futuresmart.ai", "role": "editor"}]},
    )
    assert resp.status_code == 201
    assert resp.json()["failures"] == []


def test_create_invitations_defaults_role_to_viewer(monkeypatch, client):
    captured = {}

    async def _fake(knowledge_id, actor_email, invitations):
        captured["invitations"] = invitations
        return {"invited_users": [], "failed_emails": []}
    monkeypatch.setattr(invitations_router_module, "db_create_invitations", _fake)

    client.post(
        f"/v1/{KNOWLEDGE_ID}/invitations",
        json={"invitations": [{"email": "someone@futuresmart.ai"}]},
    )
    assert captured["invitations"] == [{"email": "someone@futuresmart.ai", "role": "viewer"}]


def test_create_invitations_rejects_empty_list(client):
    resp = client.post(f"/v1/{KNOWLEDGE_ID}/invitations", json={"invitations": []})
    assert resp.status_code == 422


def test_create_invitations_rejects_bad_email(client):
    resp = client.post(
        f"/v1/{KNOWLEDGE_ID}/invitations",
        json={"invitations": [{"email": "not-an-email", "role": "editor"}]},
    )
    assert resp.status_code == 422


def test_create_invitations_rejects_owner_role(client):
    """InvitationRole only accepts editor/viewer — "owner" is not invitable."""
    resp = client.post(
        f"/v1/{KNOWLEDGE_ID}/invitations",
        json={"invitations": [{"email": "someone@futuresmart.ai", "role": "owner"}]},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET — list invitations
# ---------------------------------------------------------------------------

def test_list_invitations(monkeypatch, client):
    _patch_list(monkeypatch, [
        {"email": "editor@futuresmart.ai", "role": "editor", "invited_at": "2026-08-19T00:00:00+00:00"},
    ])
    resp = client.get(f"/v1/{KNOWLEDGE_ID}/invitations")
    assert resp.status_code == 200
    assert resp.json()["invited_users"][0]["email"] == "editor@futuresmart.ai"


def test_list_invitations_empty(monkeypatch, client):
    _patch_list(monkeypatch, [])
    resp = client.get(f"/v1/{KNOWLEDGE_ID}/invitations")
    assert resp.status_code == 200
    assert resp.json() == {"invited_users": []}


# ---------------------------------------------------------------------------
# DELETE — revoke
# ---------------------------------------------------------------------------

def test_delete_invitation_success(monkeypatch, client):
    _patch_delete(monkeypatch, True)
    resp = client.delete(f"/v1/{KNOWLEDGE_ID}/invitations/editor@futuresmart.ai")
    assert resp.status_code == 200
    assert resp.json() == {"message": "Invitation removed successfully"}


def test_delete_invitation_not_found(monkeypatch, client):
    _patch_delete(monkeypatch, False)
    resp = client.delete(f"/v1/{KNOWLEDGE_ID}/invitations/nobody@futuresmart.ai")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH — update role
# ---------------------------------------------------------------------------

def test_update_role_success(monkeypatch, client):
    _patch_update_role(monkeypatch, True)
    resp = client.patch(
        f"/v1/{KNOWLEDGE_ID}/invitations/editor@futuresmart.ai",
        json={"role": "viewer"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"message": "Role updated successfully", "email": "editor@futuresmart.ai", "role": "viewer"}


def test_update_role_not_found(monkeypatch, client):
    _patch_update_role(monkeypatch, False)
    resp = client.patch(
        f"/v1/{KNOWLEDGE_ID}/invitations/nobody@futuresmart.ai",
        json={"role": "viewer"},
    )
    assert resp.status_code == 404


def test_update_role_rejects_invalid_role(client):
    resp = client.patch(
        f"/v1/{KNOWLEDGE_ID}/invitations/editor@futuresmart.ai",
        json={"role": "owner"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /v1/invitations/shared — reverse lookup
# ---------------------------------------------------------------------------

def test_get_shared_knowledge_uses_session_email(monkeypatch, client):
    captured = _patch_shared(monkeypatch, [
        {
            "knowledge_id": "k-456",
            "collection_name": "Docs",
            "role": "viewer",
            "invited_by_email": "owner@futuresmart.ai",
            "invited_at": "2026-08-25T07:15:11+00:00",
        },
    ])
    resp = client.get("/v1/invitations/shared")
    assert resp.status_code == 200
    assert resp.json() == {"shared_knowledge": [
        {
            "knowledge_id": "k-456",
            "collection_name": "Docs",
            "role": "viewer",
            "invited_by_email": "owner@futuresmart.ai",
            "invited_at": "2026-08-25T07:15:11+00:00",
        },
    ]}
    assert captured["user_email"] == "owner@futuresmart.ai"  # the fixture's session email


def test_get_shared_knowledge_empty(monkeypatch, client):
    _patch_shared(monkeypatch, [])
    resp = client.get("/v1/invitations/shared")
    assert resp.status_code == 200
    assert resp.json() == {"shared_knowledge": []}


def test_get_shared_knowledge_session_cannot_override_other_user(monkeypatch, client):
    _patch_shared(monkeypatch, [])
    resp = client.get("/v1/invitations/shared", params={"user_id": "someone-else@futuresmart.ai"})
    assert resp.status_code == 403


def test_get_shared_knowledge_master_key_can_pass_user_id(monkeypatch):
    captured = _patch_shared(monkeypatch, [])
    app = _make_app(auth={"auth_type": "master_api_key", "api_key": "x"})
    client = TestClient(app)
    resp = client.get("/v1/invitations/shared", params={"user_id": "anyone@futuresmart.ai"})
    assert resp.status_code == 200
    assert captured["user_email"] == "anyone@futuresmart.ai"


def test_get_shared_knowledge_master_key_without_user_id_is_401(monkeypatch):
    _patch_shared(monkeypatch, [])
    app = _make_app(auth={"auth_type": "master_api_key", "api_key": "x"})
    client = TestClient(app)
    resp = client.get("/v1/invitations/shared")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Actor email resolution (invited_by_email / updated_by)
# ---------------------------------------------------------------------------

def test_actor_email_resolved_from_session_for_invited_by(monkeypatch):
    captured = {}

    async def _fake(knowledge_id, actor_email, invitations):
        captured["actor_email"] = actor_email
        return {"invited_users": [], "failed_emails": []}
    monkeypatch.setattr(invitations_router_module, "db_create_invitations", _fake)

    app = _make_app(auth={"auth_type": "session", "session_data": {"email": "Owner@FutureSmart.AI"}})
    client = TestClient(app)
    client.post(
        f"/v1/{KNOWLEDGE_ID}/invitations",
        json={"invitations": [{"email": "someone@futuresmart.ai"}]},
    )
    assert captured["actor_email"] == "owner@futuresmart.ai"  # normalized/lowercased


def test_actor_email_falls_back_to_sentinel_for_master_key(monkeypatch):
    captured = {}

    async def _fake(knowledge_id, actor_email, invitations):
        captured["actor_email"] = actor_email
        return {"invited_users": [], "failed_emails": []}
    monkeypatch.setattr(invitations_router_module, "db_create_invitations", _fake)

    app = _make_app(auth={"auth_type": "master_api_key", "api_key": "x"})
    client = TestClient(app)
    client.post(
        f"/v1/{KNOWLEDGE_ID}/invitations",
        json={"invitations": [{"email": "someone@futuresmart.ai"}]},
    )
    assert captured["actor_email"] == invitations_router_module.MASTER_API_KEY_ACTOR
