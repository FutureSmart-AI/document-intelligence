"""Unit tests for the universal access-audit dependency (`audit_access`) and its
request resolvers in utils/auth/dependencies.py.

These are pure unit tests: the auth lookup (`get_auth_details`) and the DB write
(`log_access_event`) are stubbed, so no auth service or database is required.
The DB write is stubbed by installing a fake `utils.audit_logging` module in
sys.modules — `audit_access` does `from utils.audit_logging import log_access_event`
lazily at call time, so the fake is what gets used.

Coroutines are driven with asyncio.run() (matching tests/unit/test_connector_sync.py),
so no pytest-asyncio plugin is needed.
"""

import asyncio
import sys
import types

import pytest
from starlette.requests import Request

from utils.auth import dependencies
from utils.auth.dependencies import (
    audit_access,
    resolve_knowledge_id_from_request,
    resolve_owner_id_from_request,
)

MASTER_KEY = "test-master-key"


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

class _Route:
    def __init__(self, name):
        self.name = name


def make_request(
    method="GET",
    path="/v1/k-123/config",
    route_name="get_knowledge_configuration_v2",
    path_params=None,
    query_string=b"",
    headers=None,
    body=None,
):
    """Build a minimal Starlette Request for exercising the dependency."""
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "server": ("test", 80),
        "path": path,
        "raw_path": path.encode(),
        "query_string": query_string,
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "path_params": path_params or {},
        "route": _Route(route_name),
    }

    sent = {"done": False}

    async def receive():
        payload = body if body is not None else b""
        if sent["done"]:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent["done"] = True
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(scope, receive)


@pytest.fixture
def audit(monkeypatch):
    """Install a fake utils.audit_logging that records calls instead of hitting the DB.

    Yields the recorded-calls list and the fake module (so a test can swap in a
    raising log function). Also pins API_KEY_NAME to a known value, and stubs
    the knowledge_config owner lookup to return None (no KB found) by default —
    override with `stub_owner_lookup` for tests that care about the resolved owner.
    """
    calls = []

    async def fake_log(**kwargs):
        calls.append(kwargs)
        return True

    mod = types.ModuleType("utils.audit_logging")
    mod.log_access_event = fake_log
    saved = sys.modules.get("utils.audit_logging")
    sys.modules["utils.audit_logging"] = mod

    monkeypatch.setattr(dependencies, "API_KEY_NAME", MASTER_KEY)
    stub_owner_lookup(monkeypatch, None)

    yield calls, mod

    if saved is not None:
        sys.modules["utils.audit_logging"] = saved
    else:
        sys.modules.pop("utils.audit_logging", None)


def stub_owner_lookup(monkeypatch, service: dict | None):
    """Stub utils.db.knowledge_ops.get_knowledge_service (looked up lazily by
    _resolve_actual_owner_id) so tests never hit a real database."""
    import utils.db.knowledge_ops as knowledge_ops

    async def _fake(knowledge_id):
        return service

    monkeypatch.setattr(knowledge_ops, "get_knowledge_service", _fake)


def patch_auth(monkeypatch, session_data):
    """Stub get_auth_details to return the given session data (or None)."""
    async def _fake(request, include_all_claims=False):
        return session_data
    monkeypatch.setattr(dependencies, "get_auth_details", _fake)


# ---------------------------------------------------------------------------
# Skip rules
# ---------------------------------------------------------------------------

def test_skips_health_route(audit, monkeypatch):
    """The health probe is not audited."""
    calls, _ = audit
    patch_auth(monkeypatch, None)
    req = make_request(method="GET", path="/health", route_name="health_check")
    asyncio.run(audit_access(req))
    assert calls == []


def test_search_is_audited(audit, monkeypatch):
    """Decision: every endpoint is audited, INCLUDING the high-volume search route."""
    calls, _ = audit
    patch_auth(monkeypatch, {"email": "alice@x.ai"})
    req = make_request(
        method="POST",
        path="/v1/k-123/search",
        route_name="search_knowledge",
        path_params={"knowledge_id": "k-123"},
    )
    asyncio.run(audit_access(req))

    assert len(calls) == 1
    assert calls[0]["action"] == "search_knowledge"
    assert calls[0]["knowledge_id"] == "k-123"


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------

def test_session_user_logged_with_email(audit, monkeypatch):
    """Session auth → actor is the email; knowledge_id from path; method captured."""
    calls, _ = audit
    patch_auth(monkeypatch, {"email": "alice@x.ai"})
    req = make_request(
        method="PUT",
        path="/v1/k-123/config",
        route_name="update_knowledge_configuration_v2",
        path_params={"knowledge_id": "k-123"},
    )
    asyncio.run(audit_access(req))

    assert len(calls) == 1
    row = calls[0]
    assert row["user_id"] == "alice@x.ai"
    assert row["knowledge_id"] == "k-123"
    assert row["action"] == "update_knowledge_configuration_v2"
    assert row["method"] == "PUT"
    assert row["path"] == "/v1/k-123/config"


def test_master_key_logged_as_master(audit, monkeypatch):
    """No session + valid master key → actor is 'master_api_key'."""
    calls, _ = audit
    patch_auth(monkeypatch, None)
    req = make_request(
        path="/v1/k-999/search",
        route_name="search_knowledge",
        path_params={"knowledge_id": "k-999"},
        headers={"x-api-key": MASTER_KEY},
    )
    asyncio.run(audit_access(req))

    assert len(calls) == 1
    assert calls[0]["user_id"] == "master_api_key"
    assert calls[0]["knowledge_id"] == "k-999"


def test_unauthenticated_logs_null_user(audit, monkeypatch):
    """No session and no/invalid key → still audited, with user_id=None."""
    calls, _ = audit
    patch_auth(monkeypatch, None)
    req = make_request(
        route_name="list_knowledge",
        headers={"x-api-key": "wrong-key"},
    )
    asyncio.run(audit_access(req))

    assert len(calls) == 1
    assert calls[0]["user_id"] is None
    assert calls[0]["action"] == "list_knowledge"


# ---------------------------------------------------------------------------
# knowledge_id / owner_id resolution from the request
# ---------------------------------------------------------------------------

def test_owner_id_me_falls_back_to_session_email(audit, monkeypatch):
    """owner_id='me' is treated as absent → falls back to the session email."""
    calls, _ = audit
    patch_auth(monkeypatch, {"email": "alice@x.ai"})
    req = make_request(
        path="/v1",
        route_name="list_knowledge",
        query_string=b"owner_id=me",
    )
    asyncio.run(audit_access(req))

    assert calls[0]["owner_id"] == "alice@x.ai"
    assert calls[0]["knowledge_id"] is None


def test_owner_id_from_body(audit, monkeypatch):
    """owner_id is resolved from the JSON body when not in path/query."""
    calls, _ = audit
    patch_auth(monkeypatch, None)
    req = make_request(
        method="POST",
        path="/v1/k-1/config",
        route_name="update_knowledge_configuration_v2",
        path_params={"knowledge_id": "k-1"},
        headers={"x-api-key": MASTER_KEY, "content-type": "application/json"},
        body=b'{"owner_id": "carol@x.ai"}',
    )
    asyncio.run(audit_access(req))

    assert calls[0]["owner_id"] == "carol@x.ai"


def test_create_knowledge_skipped_by_generic_audit(audit, monkeypatch):
    """create_knowledge is exempted from the generic dependency — it's logged
    explicitly by the handler instead, once knowledge_id actually exists (see
    tests/unit/test_knowledge_management.py)."""
    calls, _ = audit
    patch_auth(monkeypatch, None)
    req = make_request(
        method="POST",
        path="/v1",
        route_name="create_knowledge",
        headers={"x-api-key": MASTER_KEY, "content-type": "application/json"},
        body=b'{"owner_id": "carol@x.ai"}',
    )
    asyncio.run(audit_access(req))

    assert calls == []


def test_owner_id_resolved_for_shared_agent_access(audit, monkeypatch):
    """Non-owner (shared-agent) caller: owner_id must be the KB's real owner,
    not the caller's own email, since no explicit owner_id is in the request."""
    calls, _ = audit
    stub_owner_lookup(monkeypatch, {"owner_id": "real_owner@x.ai"})
    patch_auth(monkeypatch, {"email": "shared_user@x.ai"})
    req = make_request(
        path="/v1/k-123/search",
        route_name="search_knowledge",
        path_params={"knowledge_id": "k-123"},
    )
    asyncio.run(audit_access(req))

    assert calls[0]["user_id"] == "shared_user@x.ai"
    assert calls[0]["owner_id"] == "real_owner@x.ai"


def test_owner_id_resolved_for_master_key_clickup_call(audit, monkeypatch):
    """Master-key (e.g. ClickUp connector) call with no session: owner_id should
    come from the KB's knowledge_config row instead of staying NULL."""
    calls, _ = audit
    stub_owner_lookup(monkeypatch, {"owner_id": "kb_owner@x.ai"})
    patch_auth(monkeypatch, None)
    req = make_request(
        path="/v1/k-555/documents",
        route_name="add_document",
        path_params={"knowledge_id": "k-555"},
        headers={"x-api-key": MASTER_KEY},
    )
    asyncio.run(audit_access(req))

    assert calls[0]["user_id"] == "master_api_key"
    assert calls[0]["owner_id"] == "kb_owner@x.ai"


def test_owner_id_falls_back_to_session_when_kb_lookup_misses(audit, monkeypatch):
    """No explicit owner_id and no KB found (e.g. list_knowledge with no knowledge_id) →
    still falls back to session email, matching prior behavior."""
    calls, _ = audit
    stub_owner_lookup(monkeypatch, None)
    patch_auth(monkeypatch, {"email": "alice@x.ai"})
    req = make_request(path="/v1", route_name="list_knowledge")
    asyncio.run(audit_access(req))

    assert calls[0]["owner_id"] == "alice@x.ai"


def test_service_id_alias_resolves_to_knowledge_id(audit, monkeypatch):
    """A cross-service caller naming the id `service_id` still lands in knowledge_id."""
    calls, _ = audit
    patch_auth(monkeypatch, None)
    req = make_request(
        path="/v1/whatever",
        route_name="some_route",
        query_string=b"service_id=svc-uuid-1",
        headers={"x-api-key": MASTER_KEY},
    )
    asyncio.run(audit_access(req))

    assert calls[0]["knowledge_id"] == "svc-uuid-1"


# ---------------------------------------------------------------------------
# Never break the request
# ---------------------------------------------------------------------------

def test_persistence_failure_never_raises(audit, monkeypatch):
    """If log_access_event blows up, audit_access swallows it (returns None)."""
    _, mod = audit

    async def boom(**kwargs):
        raise RuntimeError("db down")

    mod.log_access_event = boom
    patch_auth(monkeypatch, {"email": "alice@x.ai"})
    req = make_request(route_name="get_knowledge_configuration_v2", path_params={"knowledge_id": "k-123"})

    # Must not raise.
    assert asyncio.run(audit_access(req)) is None


def test_auth_lookup_failure_never_raises(audit, monkeypatch):
    """A failing/raising auth lookup must not break auditing or the request."""
    calls, _ = audit

    async def boom_auth(request, include_all_claims=False):
        raise RuntimeError("auth service unreachable")

    monkeypatch.setattr(dependencies, "get_auth_details", boom_auth)
    req = make_request(
        route_name="list_knowledge",
        headers={"x-api-key": MASTER_KEY},
    )
    asyncio.run(audit_access(req))

    # Auth failed softly → master key still recognized, row still written.
    assert len(calls) == 1
    assert calls[0]["user_id"] == "master_api_key"


# ---------------------------------------------------------------------------
# Resolver priority (direct unit tests)
# ---------------------------------------------------------------------------

def test_resolve_knowledge_id_prefers_path_over_query_and_body():
    req = make_request(
        method="POST",
        path="/x",
        route_name="whatever",
        path_params={"knowledge_id": "from_path"},
        query_string=b"knowledge_id=from_query",
        headers={"content-type": "application/json"},
        body=b'{"knowledge_id": "from_body"}',
    )
    assert asyncio.run(resolve_knowledge_id_from_request(req)) == "from_path"


def test_resolve_owner_id_me_is_absent():
    req = make_request(route_name="whatever", query_string=b"owner_id=me")
    assert asyncio.run(resolve_owner_id_from_request(req)) is None


def test_resolve_ids_none_when_absent():
    req = make_request(route_name="whatever")
    assert asyncio.run(resolve_knowledge_id_from_request(req)) is None
    assert asyncio.run(resolve_owner_id_from_request(req)) is None
