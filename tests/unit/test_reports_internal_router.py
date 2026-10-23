"""Unit tests for routers/reports_internal.py (Batch Query Reports' Worker-
facing internal API — docs/plan/batch-query-reports/execution.md).

Covers: require_internal_service gating (master key required, session
rejected even if present), the passive contract's idempotency (results
upsert, status transitions, late-result drop on a terminal run), and the new
`/execute` endpoint's terminal/in-flight guard. Every utils/reports/
db_utils.py function is monkeypatched — no real DB call.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.reports_internal as internal_router_module
from utils.auth import dependencies

RUN_ID = "run_abc123"
MASTER_KEY = "test-master-key"


@pytest.fixture(autouse=True)
def _pin_master_key(monkeypatch):
    monkeypatch.setattr(dependencies, "API_KEY_NAME", MASTER_KEY)


@pytest.fixture(autouse=True)
def _stub_execute_run(monkeypatch):
    """The /execute endpoint's BackgroundTasks callback actually runs (Starlette
    executes background tasks before TestClient returns) — stub the real
    chat.report_execution.execute_run so these router tests never touch a
    real DB. chat/report_execution.py's own behavior is covered by
    tests/unit/test_report_execution.py."""
    import chat.report_execution as report_execution_module

    async def _fake_execute(run_id):
        return None
    monkeypatch.setattr(report_execution_module, "execute_run", _fake_execute)


def _make_app():
    app = FastAPI()
    app.include_router(internal_router_module.router)
    return app


@pytest.fixture
def client():
    return TestClient(_make_app())


# ---------------------------------------------------------------------------
# require_internal_service gating
# ---------------------------------------------------------------------------

def test_missing_api_key_rejected(client):
    resp = client.get(f"/internal/reports/runs/{RUN_ID}")
    assert resp.status_code == 401


def test_wrong_api_key_rejected(client):
    resp = client.get(f"/internal/reports/runs/{RUN_ID}", headers={"x-api-key": "wrong"})
    assert resp.status_code == 401


def test_correct_master_key_accepted(monkeypatch, client):
    async def _fake_def(run_id):
        return {"run_id": run_id, "report_id": "rep_1", "knowledge_id": "k-1", "query_groups": []}
    monkeypatch.setattr(internal_router_module.db_utils, "get_run_definition", _fake_def)

    resp = client.get(f"/internal/reports/runs/{RUN_ID}", headers={"x-api-key": MASTER_KEY})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET run definition
# ---------------------------------------------------------------------------

def test_get_run_definition_not_found(monkeypatch, client):
    async def _fake_def(run_id):
        return None
    monkeypatch.setattr(internal_router_module.db_utils, "get_run_definition", _fake_def)

    resp = client.get(f"/internal/reports/runs/{RUN_ID}", headers={"x-api-key": MASTER_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST status — idempotent lifecycle
# ---------------------------------------------------------------------------

def test_update_status_not_found(monkeypatch, client):
    async def _fake_update(run_id, **kwargs):
        return None
    monkeypatch.setattr(internal_router_module.db_utils, "update_run_status", _fake_update)

    resp = client.post(
        f"/internal/reports/runs/{RUN_ID}/status",
        headers={"x-api-key": MASTER_KEY},
        json={"status": "running"},
    )
    assert resp.status_code == 404


def test_update_status_success(monkeypatch, client):
    captured = {}

    async def _fake_update(run_id, status, started_at=None, ended_at=None, failure_reason=None):
        captured.update({"run_id": run_id, "status": status, "failure_reason": failure_reason})
        return status
    monkeypatch.setattr(internal_router_module.db_utils, "update_run_status", _fake_update)

    resp = client.post(
        f"/internal/reports/runs/{RUN_ID}/status",
        headers={"x-api-key": MASTER_KEY},
        json={"status": "failed", "failure_reason": "worker crashed"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"run_id": RUN_ID, "status": "failed"}
    assert captured["failure_reason"] == "worker crashed"


# ---------------------------------------------------------------------------
# POST results — idempotent upsert + terminal-run drop
# ---------------------------------------------------------------------------

def _result_payload():
    return {
        "results": [
            {"query_id": "q1", "group_id": "g1", "answer": "The answer", "error": None},
        ]
    }


def test_submit_results_not_found(monkeypatch, client):
    async def _fake_status(run_id):
        return None
    monkeypatch.setattr(internal_router_module.db_utils, "get_run_status", _fake_status)

    resp = client.post(
        f"/internal/reports/runs/{RUN_ID}/results",
        headers={"x-api-key": MASTER_KEY},
        json=_result_payload(),
    )
    assert resp.status_code == 404


def test_submit_results_upserts_when_running(monkeypatch, client):
    async def _fake_status(run_id):
        return {"run_id": run_id, "status": "running"}
    monkeypatch.setattr(internal_router_module.db_utils, "get_run_status", _fake_status)

    captured = {}

    async def _fake_upsert(run_id, results):
        captured["run_id"] = run_id
        captured["results"] = results
        return len(results)
    monkeypatch.setattr(internal_router_module.db_utils, "upsert_results", _fake_upsert)

    resp = client.post(
        f"/internal/reports/runs/{RUN_ID}/results",
        headers={"x-api-key": MASTER_KEY},
        json=_result_payload(),
    )
    assert resp.status_code == 200
    assert resp.json() == {"run_id": RUN_ID, "results_written": 1}
    assert captured["results"][0]["query_id"] == "q1"


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled"])
def test_submit_results_dropped_when_terminal(monkeypatch, client, terminal_status):
    """A retried Worker/executor delivery must not resurrect a closed-out run."""
    async def _fake_status(run_id):
        return {"run_id": run_id, "status": terminal_status}
    monkeypatch.setattr(internal_router_module.db_utils, "get_run_status", _fake_status)

    called = {"upsert": False}

    async def _fake_upsert(run_id, results):
        called["upsert"] = True
        return len(results)
    monkeypatch.setattr(internal_router_module.db_utils, "upsert_results", _fake_upsert)

    resp = client.post(
        f"/internal/reports/runs/{RUN_ID}/results",
        headers={"x-api-key": MASTER_KEY},
        json=_result_payload(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["results_written"] == 0
    assert body["ignored"] is True
    assert called["upsert"] is False


# ---------------------------------------------------------------------------
# POST analysis
# ---------------------------------------------------------------------------

def test_submit_analysis_not_found(monkeypatch, client):
    async def _fake_set(run_id, overall_summary):
        return False
    monkeypatch.setattr(internal_router_module.db_utils, "set_run_analysis", _fake_set)

    resp = client.post(
        f"/internal/reports/runs/{RUN_ID}/analysis",
        headers={"x-api-key": MASTER_KEY},
        json={"overall_summary": "Some summary"},
    )
    assert resp.status_code == 404


def test_submit_analysis_success(monkeypatch, client):
    async def _fake_set(run_id, overall_summary):
        assert overall_summary == "Some summary"
        return True
    monkeypatch.setattr(internal_router_module.db_utils, "set_run_analysis", _fake_set)

    resp = client.post(
        f"/internal/reports/runs/{RUN_ID}/analysis",
        headers={"x-api-key": MASTER_KEY},
        json={"overall_summary": "Some summary"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST execute — the in-process one-shot execution trigger
# ---------------------------------------------------------------------------

def test_execute_not_found(monkeypatch, client):
    async def _fake_status(run_id):
        return None
    monkeypatch.setattr(internal_router_module.db_utils, "get_run_status", _fake_status)

    resp = client.post(f"/internal/reports/runs/{RUN_ID}/execute", headers={"x-api-key": MASTER_KEY})
    assert resp.status_code == 404


def test_execute_starts_pending_run(monkeypatch, client):
    async def _fake_status(run_id):
        return {"run_id": run_id, "status": "pending"}
    monkeypatch.setattr(internal_router_module.db_utils, "get_run_status", _fake_status)

    resp = client.post(f"/internal/reports/runs/{RUN_ID}/execute", headers={"x-api-key": MASTER_KEY})
    assert resp.status_code == 202
    assert resp.json() == {"run_id": RUN_ID, "status": "pending", "started": True}


@pytest.mark.parametrize("existing_status", ["running", "completed", "failed", "cancelled"])
def test_execute_skips_already_started_run(monkeypatch, client, existing_status):
    """A duplicate trigger (Worker retry) must not re-run a job already
    underway or already terminal."""
    async def _fake_status(run_id):
        return {"run_id": run_id, "status": existing_status}
    monkeypatch.setattr(internal_router_module.db_utils, "get_run_status", _fake_status)

    resp = client.post(f"/internal/reports/runs/{RUN_ID}/execute", headers={"x-api-key": MASTER_KEY})
    assert resp.status_code == 202
    body = resp.json()
    assert body["started"] is False
    assert body["status"] == existing_status
