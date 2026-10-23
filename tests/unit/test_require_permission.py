"""Unit tests for require_permission()'s non-owner branch after the
share-agent migration (docs/plan/access-and-sharing/archive/design-share-invitations.md).

Pure unit tests: get_auth_details, get_knowledge_service, and
get_invited_role are all stubbed, so no auth service, DB, or HTTP call is
made anywhere in these tests — including, crucially, no outbound HTTP call
to fs-agent-chat, which is the whole point of this migration.

Coroutines are driven with asyncio.run() (matching
tests/unit/test_audit_logging.py), so no pytest-asyncio plugin is needed.
"""

import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import utils.db.knowledge_invitations as knowledge_invitations
import utils.db.knowledge_ops as knowledge_ops
from utils.auth import dependencies

MASTER_KEY = "test-master-key"
OWNER_EMAIL = "owner@futuresmart.ai"
EDITOR_EMAIL = "editor@futuresmart.ai"
VIEWER_EMAIL = "viewer@futuresmart.ai"
STRANGER_EMAIL = "stranger@futuresmart.ai"
KNOWLEDGE_ID = "k-123"


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def make_request(headers=None):
    """Build a minimal Starlette Request — require_permission's dependency
    only reads headers off it (via get_api_key_or_session/get_auth_details);
    knowledge_id is passed as a plain argument when calling the dependency
    directly, not resolved from the request."""
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "server": ("test", 80),
        "path": f"/v1/{KNOWLEDGE_ID}/config",
        "raw_path": f"/v1/{KNOWLEDGE_ID}/config".encode(),
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "path_params": {"knowledge_id": KNOWLEDGE_ID},
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


@pytest.fixture(autouse=True)
def _pin_master_key(monkeypatch):
    monkeypatch.setattr(dependencies, "API_KEY_NAME", MASTER_KEY)


def patch_session(monkeypatch, email):
    """Stub get_auth_details to return a session for `email` (or None for
    no session at all)."""
    async def _fake(request, include_all_claims=False):
        if email is None:
            return None
        return {"email": email, "user_id": email}
    monkeypatch.setattr(dependencies, "get_auth_details", _fake)


def stub_knowledge_service(monkeypatch, owner_id):
    async def _fake(knowledge_id):
        assert knowledge_id == KNOWLEDGE_ID
        return {"knowledge_id": knowledge_id, "owner_id": owner_id}
    monkeypatch.setattr(knowledge_ops, "get_knowledge_service", _fake)


def stub_invited_role(monkeypatch, role, calls=None):
    async def _fake(knowledge_id, user_email):
        if calls is not None:
            calls.append((knowledge_id, user_email))
        return role
    monkeypatch.setattr(knowledge_invitations, "get_invited_role", _fake)


# ---------------------------------------------------------------------------
# Owner fast-path — unaffected by this migration
# ---------------------------------------------------------------------------

def test_owner_fast_path_grants_access_without_invitation_lookup(monkeypatch):
    """Owner gets in via the local email-match fast-path — the invitation
    table is never even queried."""
    patch_session(monkeypatch, OWNER_EMAIL)
    stub_knowledge_service(monkeypatch, owner_id=OWNER_EMAIL)
    calls = []
    stub_invited_role(monkeypatch, "owner", calls=calls)  # would be wrong if ever called

    dep = dependencies.require_permission("delete")
    req = make_request()
    result = asyncio.run(dep(knowledge_id=KNOWLEDGE_ID, request=req, x_api_key=None))

    assert result == OWNER_EMAIL
    assert calls == [], "owner fast-path must not consult the invitations table"


def test_owner_fast_path_is_case_insensitive(monkeypatch):
    """KnowledgeConfig.owner_id may be stored in whatever case it was
    created with; the session email is always lowercased. The comparison
    must not miss a real owner over casing alone."""
    patch_session(monkeypatch, OWNER_EMAIL)  # already lowercase
    stub_knowledge_service(monkeypatch, owner_id="Owner@Futuresmart.AI")
    calls = []
    stub_invited_role(monkeypatch, "owner", calls=calls)

    dep = dependencies.require_permission("delete")
    result = asyncio.run(dep(knowledge_id=KNOWLEDGE_ID, request=make_request(), x_api_key=None))

    assert result == OWNER_EMAIL
    assert calls == []


# ---------------------------------------------------------------------------
# Non-owner — local invitations table + permission matrix (the migration)
# ---------------------------------------------------------------------------

def test_invited_editor_granted_write(monkeypatch):
    patch_session(monkeypatch, EDITOR_EMAIL)
    stub_knowledge_service(monkeypatch, owner_id=OWNER_EMAIL)
    calls = []
    stub_invited_role(monkeypatch, "editor", calls=calls)

    dep = dependencies.require_permission("write")
    req = make_request()
    result = asyncio.run(dep(knowledge_id=KNOWLEDGE_ID, request=req, x_api_key=None))

    assert result == EDITOR_EMAIL
    assert calls == [(KNOWLEDGE_ID, EDITOR_EMAIL)]


def test_invited_editor_granted_delete(monkeypatch):
    patch_session(monkeypatch, EDITOR_EMAIL)
    stub_knowledge_service(monkeypatch, owner_id=OWNER_EMAIL)
    stub_invited_role(monkeypatch, "editor")

    dep = dependencies.require_permission("delete")
    result = asyncio.run(dep(knowledge_id=KNOWLEDGE_ID, request=make_request(), x_api_key=None))
    assert result == EDITOR_EMAIL


def test_invited_viewer_granted_read(monkeypatch):
    patch_session(monkeypatch, VIEWER_EMAIL)
    stub_knowledge_service(monkeypatch, owner_id=OWNER_EMAIL)
    stub_invited_role(monkeypatch, "viewer")

    dep = dependencies.require_permission("read")
    result = asyncio.run(dep(knowledge_id=KNOWLEDGE_ID, request=make_request(), x_api_key=None))
    assert result == VIEWER_EMAIL


def test_invited_viewer_granted_use(monkeypatch):
    patch_session(monkeypatch, VIEWER_EMAIL)
    stub_knowledge_service(monkeypatch, owner_id=OWNER_EMAIL)
    stub_invited_role(monkeypatch, "viewer")

    dep = dependencies.require_permission("use")
    result = asyncio.run(dep(knowledge_id=KNOWLEDGE_ID, request=make_request(), x_api_key=None))
    assert result == VIEWER_EMAIL


def test_invited_viewer_denied_write(monkeypatch):
    patch_session(monkeypatch, VIEWER_EMAIL)
    stub_knowledge_service(monkeypatch, owner_id=OWNER_EMAIL)
    stub_invited_role(monkeypatch, "viewer")

    dep = dependencies.require_permission("write")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(dep(knowledge_id=KNOWLEDGE_ID, request=make_request(), x_api_key=None))
    assert exc_info.value.status_code == 403


def test_invited_viewer_denied_delete(monkeypatch):
    patch_session(monkeypatch, VIEWER_EMAIL)
    stub_knowledge_service(monkeypatch, owner_id=OWNER_EMAIL)
    stub_invited_role(monkeypatch, "viewer")

    dep = dependencies.require_permission("delete")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(dep(knowledge_id=KNOWLEDGE_ID, request=make_request(), x_api_key=None))
    assert exc_info.value.status_code == 403


def test_uninvited_non_owner_denied(monkeypatch):
    """A stranger with no invitation row at all — get_invited_role returns
    None -> role_allows(None, ...) is False -> 403, regardless of permission
    level requested."""
    patch_session(monkeypatch, STRANGER_EMAIL)
    stub_knowledge_service(monkeypatch, owner_id=OWNER_EMAIL)
    calls = []
    stub_invited_role(monkeypatch, None, calls=calls)

    dep = dependencies.require_permission("read")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(dep(knowledge_id=KNOWLEDGE_ID, request=make_request(), x_api_key=None))

    assert exc_info.value.status_code == 403
    assert calls == [(KNOWLEDGE_ID, STRANGER_EMAIL)]


def test_invitation_lookup_failure_denies_rather_than_raises(monkeypatch):
    """A DB error resolving the role must deny access (fail closed), not
    propagate as a 500 and definitely not fail open."""
    patch_session(monkeypatch, STRANGER_EMAIL)
    stub_knowledge_service(monkeypatch, owner_id=OWNER_EMAIL)

    async def _boom(knowledge_id, user_email):
        raise RuntimeError("db unreachable")
    monkeypatch.setattr(knowledge_invitations, "get_invited_role", _boom)

    dep = dependencies.require_permission("read")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(dep(knowledge_id=KNOWLEDGE_ID, request=make_request(), x_api_key=None))
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Master key bypass — unaffected
# ---------------------------------------------------------------------------

def test_master_key_bypasses_everything(monkeypatch):
    patch_session(monkeypatch, None)
    calls = []
    stub_invited_role(monkeypatch, None, calls=calls)

    dep = dependencies.require_permission("delete")
    req = make_request(headers={"x-api-key": MASTER_KEY})
    result = asyncio.run(dep(knowledge_id=KNOWLEDGE_ID, request=req, x_api_key=MASTER_KEY))

    assert result is None
    assert calls == []


# ---------------------------------------------------------------------------
# The old HTTP call path is gone
# ---------------------------------------------------------------------------

def test_http_check_access_function_removed():
    """The HTTP client function this migration replaced must not exist on
    the rewired module — a stray import of it anywhere would mean the old
    network call is still reachable."""
    assert not hasattr(dependencies, "_check_access_via_chat")
    assert hasattr(dependencies, "_check_access_locally"), (
        "the in-process replacement must exist"
    )


def test_chat_auth_base_no_longer_configured():
    """CHAT_AUTH_BASE was the only config this HTTP dependency needed —
    it must not be importable from either module it used to live in."""
    import utils.setup_env as setup_env

    assert not hasattr(setup_env, "CHAT_AUTH_BASE")
    assert not hasattr(dependencies, "CHAT_AUTH_BASE")


def test_dependencies_module_does_not_import_check_access_via_chat_source():
    """Source-level guard against re-introducing the removed call: the
    rewired non-owner branch must not reference it even as dead code."""
    import inspect

    source = inspect.getsource(dependencies)
    assert "_check_access_via_chat" not in source
    assert "CHAT_AUTH_BASE" not in source
