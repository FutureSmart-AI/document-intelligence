"""Unit tests for routers/reports.py (Batch Query Reports, Feature 8 —
docs/plan/batch-query-reports/schema.md).

Two layers, mirroring tests/unit/test_knowledge_invitations_router.py's
convention:
  1. CRUD/run-creation tests: every utils/reports/db_utils.py function the
     router calls is monkeypatched, and require_permission is dependency-
     overridden to bypass RBAC entirely — isolates the router's own logic
     (validation, response shaping, query-cap enforcement).
  2. RBAC gating tests: require_permission is NOT overridden — real
     dependency, with get_auth_details/get_knowledge_service/get_invited_role
     stubbed (same pattern as tests/unit/test_require_permission.py) — proves
     owner/editor/viewer/uninvited actually resolve to the right status codes
     end to end through the real router.

No real DB call in any test.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

import routers.reports as reports_router_module
import utils.db.knowledge_invitations as knowledge_invitations
import utils.db.knowledge_ops as knowledge_ops
from utils.auth import dependencies

KNOWLEDGE_ID = "k-123"
OWNER_EMAIL = "owner@futuresmart.ai"
EDITOR_EMAIL = "editor@futuresmart.ai"
VIEWER_EMAIL = "viewer@futuresmart.ai"
STRANGER_EMAIL = "stranger@futuresmart.ai"
MASTER_KEY = "test-master-key"


# ---------------------------------------------------------------------------
# Layer 1: CRUD / run creation, require_permission bypassed
# ---------------------------------------------------------------------------

def _make_app():
    """Mount the router with every require_permission(...) dependency
    overridden — same pattern as test_knowledge_invitations_router.py."""
    app = FastAPI()
    app.include_router(reports_router_module.router)
    for route in reports_router_module.router.routes:
        for dep in route.dependant.dependencies:
            if dep.call.__qualname__.endswith("require_permission.<locals>.dependency"):
                app.dependency_overrides[dep.call] = lambda: OWNER_EMAIL
    return app


@pytest.fixture
def client():
    return TestClient(_make_app())


def _sample_body(**overrides):
    body = {
        "name": "Weekly research check",
        "query_groups": [
            {"type": "independent", "queries": ["q1", "q2"]},
            {"type": "sequential", "queries": ["q3", "q4"]},
        ],
    }
    body.update(overrides)
    return body


# --- Create ------------------------------------------------------------

def test_create_report_success(monkeypatch, client):
    async def _fake_limit(knowledge_id):
        return 20
    monkeypatch.setattr(reports_router_module.db_utils, "get_effective_query_limit", _fake_limit)

    async def _fake_create(**kwargs):
        assert kwargs["knowledge_id"] == KNOWLEDGE_ID
        assert kwargs["name"] == "Weekly research check"
        return "rep_abc"
    monkeypatch.setattr(reports_router_module.db_utils, "create_report", _fake_create)

    resp = client.post(f"/v1/{KNOWLEDGE_ID}/reports", json=_sample_body())
    assert resp.status_code == 201
    assert resp.json() == {"report_id": "rep_abc"}


def test_create_report_run_immediately(monkeypatch, client):
    async def _fake_limit(knowledge_id):
        return 20
    monkeypatch.setattr(reports_router_module.db_utils, "get_effective_query_limit", _fake_limit)

    async def _fake_create(**kwargs):
        return "rep_abc"
    monkeypatch.setattr(reports_router_module.db_utils, "create_report", _fake_create)

    async def _fake_create_run(report_id, trigger="manual", owner_id=None):
        assert report_id == "rep_abc"
        assert trigger == "manual"
        return {"run_id": "run_xyz", "total_queries": 4}
    monkeypatch.setattr(reports_router_module.db_utils, "create_run", _fake_create_run)

    enqueued = []

    async def _fake_enqueue(run_id):
        enqueued.append(run_id)
        return True
    monkeypatch.setattr(reports_router_module, "enqueue_run", _fake_enqueue)

    resp = client.post(f"/v1/{KNOWLEDGE_ID}/reports", json=_sample_body(run_immediately=True))
    assert resp.status_code == 201
    assert resp.json() == {"report_id": "rep_abc", "run_id": "run_xyz"}
    assert enqueued == ["run_xyz"]


def test_create_report_rejects_empty_query_groups(client):
    resp = client.post(f"/v1/{KNOWLEDGE_ID}/reports", json=_sample_body(query_groups=[]))
    assert resp.status_code == 422  # pydantic validator on the model


def test_create_report_rejects_over_query_limit(monkeypatch, client):
    async def _fake_limit(knowledge_id):
        return 2  # lower than the 4 queries in _sample_body()
    monkeypatch.setattr(reports_router_module.db_utils, "get_effective_query_limit", _fake_limit)

    resp = client.post(f"/v1/{KNOWLEDGE_ID}/reports", json=_sample_body())
    assert resp.status_code == 400
    assert "maximum query limit" in resp.json()["detail"]


def test_create_report_rejects_cron_without_expression(monkeypatch, client):
    async def _fake_limit(knowledge_id):
        return 20
    monkeypatch.setattr(reports_router_module.db_utils, "get_effective_query_limit", _fake_limit)

    resp = client.post(
        f"/v1/{KNOWLEDGE_ID}/reports",
        json=_sample_body(schedule={"frequency": "cron"}),
    )
    assert resp.status_code == 400
    assert "cron expression" in resp.json()["detail"]


# --- List / get ----------------------------------------------------------

def test_list_reports(monkeypatch, client):
    async def _fake_list(knowledge_id):
        return [{"report_id": "rep_1", "knowledge_id": knowledge_id, "name": "x"}]
    monkeypatch.setattr(reports_router_module.db_utils, "list_reports", _fake_list)

    resp = client.get(f"/v1/{KNOWLEDGE_ID}/reports")
    assert resp.status_code == 200
    assert resp.json()["reports"][0]["report_id"] == "rep_1"


def test_get_report_not_found(monkeypatch, client):
    async def _fake_get(report_id):
        return None
    monkeypatch.setattr(reports_router_module.db_utils, "get_report", _fake_get)

    resp = client.get(f"/v1/{KNOWLEDGE_ID}/reports/rep_missing")
    assert resp.status_code == 404


def test_get_report_wrong_knowledge_id_404s(monkeypatch, client):
    """A report that belongs to a DIFFERENT knowledge base must 404, not leak."""
    async def _fake_get(report_id):
        return {"report_id": report_id, "knowledge_id": "some-other-kb", "name": "x"}
    monkeypatch.setattr(reports_router_module.db_utils, "get_report", _fake_get)

    resp = client.get(f"/v1/{KNOWLEDGE_ID}/reports/rep_1")
    assert resp.status_code == 404


# --- Update ----------------------------------------------------------------

def test_update_report_clears_schedule_on_explicit_null(monkeypatch, client):
    async def _fake_get(report_id):
        return {"report_id": report_id, "knowledge_id": KNOWLEDGE_ID, "name": "x"}
    monkeypatch.setattr(reports_router_module.db_utils, "get_report", _fake_get)

    captured = {}

    async def _fake_update(report_id, fields):
        captured.update(fields)
        return True
    monkeypatch.setattr(reports_router_module.db_utils, "update_report", _fake_update)

    resp = client.put(f"/v1/{KNOWLEDGE_ID}/reports/rep_1", json={"schedule": None})
    assert resp.status_code == 200
    assert "schedule" in captured
    assert captured["schedule"] is None


def test_update_report_ignores_absent_fields(monkeypatch, client):
    async def _fake_get(report_id):
        return {"report_id": report_id, "knowledge_id": KNOWLEDGE_ID, "name": "x"}
    monkeypatch.setattr(reports_router_module.db_utils, "get_report", _fake_get)

    captured = {}

    async def _fake_update(report_id, fields):
        captured.update(fields)
        return True
    monkeypatch.setattr(reports_router_module.db_utils, "update_report", _fake_update)

    resp = client.put(f"/v1/{KNOWLEDGE_ID}/reports/rep_1", json={"name": "New name"})
    assert resp.status_code == 200
    assert captured.get("name") == "New name"
    assert "schedule" not in captured
    assert "query_groups" not in captured


# --- Delete ------------------------------------------------------------

def test_delete_report(monkeypatch, client):
    async def _fake_get(report_id):
        return {"report_id": report_id, "knowledge_id": KNOWLEDGE_ID, "name": "x"}
    monkeypatch.setattr(reports_router_module.db_utils, "get_report", _fake_get)

    async def _fake_delete(report_id, deleted_by=None):
        return True
    monkeypatch.setattr(reports_router_module.db_utils, "soft_delete_report", _fake_delete)

    resp = client.delete(f"/v1/{KNOWLEDGE_ID}/reports/rep_1")
    assert resp.status_code == 200


# --- Runs ------------------------------------------------------------------

def test_run_report_enqueues(monkeypatch, client):
    async def _fake_get(report_id):
        return {"report_id": report_id, "knowledge_id": KNOWLEDGE_ID, "name": "x"}
    monkeypatch.setattr(reports_router_module.db_utils, "get_report", _fake_get)

    async def _fake_create_run(report_id, trigger="manual", owner_id=None):
        return {"run_id": "run_1", "total_queries": 2}
    monkeypatch.setattr(reports_router_module.db_utils, "create_run", _fake_create_run)

    enqueued = []

    async def _fake_enqueue(run_id):
        enqueued.append(run_id)
        return True
    monkeypatch.setattr(reports_router_module, "enqueue_run", _fake_enqueue)

    resp = client.post(f"/v1/{KNOWLEDGE_ID}/reports/rep_1/runs")
    assert resp.status_code == 202
    assert resp.json() == {"run_id": "run_1", "status": "pending"}
    assert enqueued == ["run_1"]


def test_run_report_over_query_limit(monkeypatch, client):
    async def _fake_get(report_id):
        return {"report_id": report_id, "knowledge_id": KNOWLEDGE_ID, "name": "x"}
    monkeypatch.setattr(reports_router_module.db_utils, "get_report", _fake_get)

    async def _fake_create_run(report_id, trigger="manual", owner_id=None):
        raise reports_router_module.db_utils.QueryLimitExceeded(25, 20)
    monkeypatch.setattr(reports_router_module.db_utils, "create_run", _fake_create_run)

    resp = client.post(f"/v1/{KNOWLEDGE_ID}/reports/rep_1/runs")
    assert resp.status_code == 400
    assert "maximum query limit" in resp.json()["detail"]


def test_get_run_status(monkeypatch, client):
    async def _fake_get_report(report_id):
        return {"report_id": report_id, "knowledge_id": KNOWLEDGE_ID, "name": "x"}
    monkeypatch.setattr(reports_router_module.db_utils, "get_report", _fake_get_report)

    async def _fake_status(run_id):
        return {
            "run_id": run_id, "report_id": "rep_1", "knowledge_id": KNOWLEDGE_ID,
            "status": "running",
            "progress": {"total_queries": 4, "completed_queries": 2, "failed_queries": 0, "percent_complete": 50},
            "started_at": None, "ended_at": None,
        }
    monkeypatch.setattr(reports_router_module.db_utils, "get_run_status", _fake_status)

    resp = client.get(f"/v1/{KNOWLEDGE_ID}/reports/rep_1/runs/run_1/status")
    assert resp.status_code == 200
    assert resp.json()["progress"]["percent_complete"] == 50


def test_get_run_status_wrong_report_404s(monkeypatch, client):
    async def _fake_get_report(report_id):
        return {"report_id": report_id, "knowledge_id": KNOWLEDGE_ID, "name": "x"}
    monkeypatch.setattr(reports_router_module.db_utils, "get_report", _fake_get_report)

    async def _fake_status(run_id):
        return {
            "run_id": run_id, "report_id": "some-other-report", "knowledge_id": KNOWLEDGE_ID,
            "status": "running", "progress": {}, "started_at": None, "ended_at": None,
        }
    monkeypatch.setattr(reports_router_module.db_utils, "get_run_status", _fake_status)

    resp = client.get(f"/v1/{KNOWLEDGE_ID}/reports/rep_1/runs/run_1/status")
    assert resp.status_code == 404


def test_export_run_rejects_bad_format(client):
    resp = client.get(f"/v1/{KNOWLEDGE_ID}/reports/rep_1/runs/run_1/export?format=xml")
    assert resp.status_code == 400


def test_export_run_json(monkeypatch, client):
    async def _fake_get_report(report_id):
        return {"report_id": report_id, "knowledge_id": KNOWLEDGE_ID, "name": "x"}
    monkeypatch.setattr(reports_router_module.db_utils, "get_report", _fake_get_report)

    async def _fake_status(run_id):
        return {"run_id": run_id, "report_id": "rep_1", "knowledge_id": KNOWLEDGE_ID, "status": "completed",
                "progress": {}, "started_at": None, "ended_at": None}
    monkeypatch.setattr(reports_router_module.db_utils, "get_run_status", _fake_status)

    async def _fake_full(run_id):
        return {"run_id": run_id, "report_id": "rep_1", "knowledge_id": KNOWLEDGE_ID, "query_groups": []}
    monkeypatch.setattr(reports_router_module.db_utils, "get_run_full", _fake_full)

    resp = client.get(f"/v1/{KNOWLEDGE_ID}/reports/rep_1/runs/run_1/export?format=json")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert "attachment" in resp.headers["content-disposition"]


def test_cancel_run(monkeypatch, client):
    async def _fake_get_report(report_id):
        return {"report_id": report_id, "knowledge_id": KNOWLEDGE_ID, "name": "x"}
    monkeypatch.setattr(reports_router_module.db_utils, "get_report", _fake_get_report)

    async def _fake_status(run_id):
        return {"run_id": run_id, "report_id": "rep_1", "knowledge_id": KNOWLEDGE_ID, "status": "running",
                "progress": {}, "started_at": None, "ended_at": None}
    monkeypatch.setattr(reports_router_module.db_utils, "get_run_status", _fake_status)

    async def _fake_cancel(run_id, cancelled_by=None):
        return "cancelled"
    monkeypatch.setattr(reports_router_module.db_utils, "cancel_run", _fake_cancel)

    resp = client.post(f"/v1/{KNOWLEDGE_ID}/reports/rep_1/runs/run_1/cancel")
    assert resp.status_code == 200
    assert resp.json() == {"run_id": "run_1", "status": "cancelled"}


# --- Settings ------------------------------------------------------------

def test_get_settings(monkeypatch, client):
    async def _fake_settings(knowledge_id):
        return {"max_queries_per_report": 20, "default_max_queries_per_report": 20}
    monkeypatch.setattr(reports_router_module.db_utils, "get_report_settings", _fake_settings)

    resp = client.get(f"/v1/{KNOWLEDGE_ID}/reports/settings")
    assert resp.status_code == 200
    assert resp.json()["max_queries_per_report"] == 20


def test_update_settings_rejects_out_of_range(client):
    resp = client.put(f"/v1/{KNOWLEDGE_ID}/reports/settings", json={"max_queries_per_report": 0})
    assert resp.status_code == 400

    resp = client.put(f"/v1/{KNOWLEDGE_ID}/reports/settings", json={"max_queries_per_report": 5000})
    assert resp.status_code == 400


def test_update_settings_success(monkeypatch, client):
    async def _fake_update(knowledge_id, value, updated_by):
        return {"max_queries_per_report": value, "default_max_queries_per_report": 20}
    monkeypatch.setattr(reports_router_module.db_utils, "update_max_queries_per_report", _fake_update)

    resp = client.put(f"/v1/{KNOWLEDGE_ID}/reports/settings", json={"max_queries_per_report": 50})
    assert resp.status_code == 200
    assert resp.json()["max_queries_per_report"] == 50


# ---------------------------------------------------------------------------
# Layer 2: real RBAC gating (require_permission NOT overridden)
# ---------------------------------------------------------------------------

def _make_rbac_app():
    app = FastAPI()
    app.include_router(reports_router_module.router)
    return app


def _make_request_for(app, method, path, headers=None):
    scope = {
        "type": "http", "http_version": "1.1", "method": method, "scheme": "http",
        "server": ("test", 80), "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "path_params": {},
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


@pytest.fixture(autouse=True)
def _pin_master_key(monkeypatch):
    monkeypatch.setattr(dependencies, "API_KEY_NAME", MASTER_KEY)


def _patch_session(monkeypatch, email):
    async def _fake(request, include_all_claims=False):
        if email is None:
            return None
        return {"email": email, "user_id": email}
    monkeypatch.setattr(dependencies, "get_auth_details", _fake)


def _patch_knowledge_service(monkeypatch, owner_id=OWNER_EMAIL):
    async def _fake(knowledge_id):
        return {"knowledge_id": knowledge_id, "owner_id": owner_id}
    monkeypatch.setattr(knowledge_ops, "get_knowledge_service", _fake)


def _patch_invited_role(monkeypatch, role):
    async def _fake(knowledge_id, user_email):
        return role
    monkeypatch.setattr(knowledge_invitations, "get_invited_role", _fake)


@pytest.fixture
def rbac_client(monkeypatch):
    async def _fake_list(knowledge_id):
        return []
    monkeypatch.setattr(reports_router_module.db_utils, "list_reports", _fake_list)

    async def _fake_limit(knowledge_id):
        return 20
    monkeypatch.setattr(reports_router_module.db_utils, "get_effective_query_limit", _fake_limit)

    async def _fake_create(**kwargs):
        return "rep_1"
    monkeypatch.setattr(reports_router_module.db_utils, "create_report", _fake_create)

    async def _fake_get_report(report_id):
        return {"report_id": report_id, "knowledge_id": KNOWLEDGE_ID, "name": "x"}
    monkeypatch.setattr(reports_router_module.db_utils, "get_report", _fake_get_report)

    async def _fake_delete(report_id, deleted_by=None):
        return True
    monkeypatch.setattr(reports_router_module.db_utils, "soft_delete_report", _fake_delete)

    return TestClient(_make_rbac_app())


def test_rbac_owner_can_write_and_delete(monkeypatch, rbac_client):
    _patch_session(monkeypatch, OWNER_EMAIL)
    _patch_knowledge_service(monkeypatch, owner_id=OWNER_EMAIL)
    _patch_invited_role(monkeypatch, None)  # owner never needs the invitation table

    assert rbac_client.post(f"/v1/{KNOWLEDGE_ID}/reports", json=_sample_body()).status_code == 201
    assert rbac_client.delete(f"/v1/{KNOWLEDGE_ID}/reports/rep_1").status_code == 200


def test_rbac_editor_can_write_and_delete(monkeypatch, rbac_client):
    """Per this service's permission matrix (utils/auth/permissions.py),
    `editor` grants read/write/delete (delete is "editor-safe" — same
    posture as documents/chunks/connectors); only `admin_delete`-level
    ops are owner-only, and reports don't request that level."""
    _patch_session(monkeypatch, EDITOR_EMAIL)
    _patch_knowledge_service(monkeypatch, owner_id=OWNER_EMAIL)
    _patch_invited_role(monkeypatch, "editor")

    assert rbac_client.post(f"/v1/{KNOWLEDGE_ID}/reports", json=_sample_body()).status_code == 201
    assert rbac_client.get(f"/v1/{KNOWLEDGE_ID}/reports").status_code == 200
    assert rbac_client.delete(f"/v1/{KNOWLEDGE_ID}/reports/rep_1").status_code == 200


def test_rbac_viewer_can_read_but_not_write(monkeypatch, rbac_client):
    _patch_session(monkeypatch, VIEWER_EMAIL)
    _patch_knowledge_service(monkeypatch, owner_id=OWNER_EMAIL)
    _patch_invited_role(monkeypatch, "viewer")

    assert rbac_client.get(f"/v1/{KNOWLEDGE_ID}/reports").status_code == 200
    assert rbac_client.post(f"/v1/{KNOWLEDGE_ID}/reports", json=_sample_body()).status_code == 403
    assert rbac_client.delete(f"/v1/{KNOWLEDGE_ID}/reports/rep_1").status_code == 403


def test_rbac_uninvited_stranger_denied(monkeypatch, rbac_client):
    _patch_session(monkeypatch, STRANGER_EMAIL)
    _patch_knowledge_service(monkeypatch, owner_id=OWNER_EMAIL)
    _patch_invited_role(monkeypatch, None)  # never invited

    assert rbac_client.get(f"/v1/{KNOWLEDGE_ID}/reports").status_code == 403
    assert rbac_client.post(f"/v1/{KNOWLEDGE_ID}/reports", json=_sample_body()).status_code == 403


def test_rbac_master_key_bypasses_everything(monkeypatch, rbac_client):
    resp = rbac_client.get(f"/v1/{KNOWLEDGE_ID}/reports", headers={"x-api-key": MASTER_KEY})
    assert resp.status_code == 200
