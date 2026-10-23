"""Batch Query Reports — internal (Worker-facing) API.

Ported from fs-agent-dashboard's `batch_query_reports/router_internal.py` —
see docs/plan/batch-query-reports/execution.md's Worker-wiring decision.

Prefix: `/internal/reports/runs`. **Service-to-service only — never
browser-facing.** Auth: `require_internal_service` (master `x-api-key` only,
no session fallback — utils/auth/dependencies.py).

Two integration shapes are exposed, deliberately, per the Worker-wiring
decision (plan.md):
  1. The full **passive** contract (`GET` run definition, `POST` status/
     results/analysis) — 1:1 with the donor's `/internal/runs...`, so a
     Worker task left unchanged and simply reconfigured to point here instead
     of the dashboard keeps working with zero code change.
  2. A new **`POST .../execute`** — runs the whole pre-materialized run
     in-process (`chat/report_execution.py::execute_run`), so the per-query
     chat calls never leave this service. This is what actually satisfies
     "in-process, no HTTP hop" — see plan.md for why (2) requires a
     corresponding Worker-side change this task could not make (the Worker
     repo is unavailable/out of scope here).

Idempotency (shared with (1)): status transitions are idempotent on run_id +
target state; results are upserted per query_id — see utils/reports/
db_utils.py.
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from utils.logger import logger
from utils.auth.dependencies import require_internal_service

from utils.reports import db_utils
from utils.pydantic_models_reports import (
    StatusUpdateRequest,
    ResultsSubmitRequest,
    AnalysisSubmitRequest,
)

router = APIRouter(prefix="/internal/reports/runs")

_TERMINAL_RUN_STATES = {"completed", "failed", "cancelled"}


@router.get("/{run_id}")
async def get_run_definition(
    run_id: str,
    _auth: None = Depends(require_internal_service),
):
    """Worker fetches the pre-materialized run definition (stable group_id/query_id)."""
    definition = await db_utils.get_run_definition(run_id)
    if not definition:
        raise HTTPException(status_code=404, detail="Run not found")
    return definition


@router.post("/{run_id}/execute", status_code=202)
async def execute_run(
    run_id: str,
    background_tasks: BackgroundTasks,
    _auth: None = Depends(require_internal_service),
):
    """Run the whole pre-materialized run in-process (no per-query HTTP hop).

    Returns immediately (202) — execution happens as a background task so a
    slow run (many queries) doesn't hold the Worker's HTTP request open.
    Idempotent in practice: `execute_run`'s own `update_run_status("running")`
    call is a no-op if the run has already left `pending`, so a duplicate
    trigger doesn't re-run a job already underway or already terminal.
    """
    status_obj = await db_utils.get_run_status(run_id)
    if not status_obj:
        raise HTTPException(status_code=404, detail="Run not found")
    if status_obj["status"] in _TERMINAL_RUN_STATES or status_obj["status"] == "running":
        logger.info(f"[reports.internal] execute skipped for run {run_id}: status={status_obj['status']}")
        return {"run_id": run_id, "status": status_obj["status"], "started": False}

    from chat.report_execution import execute_run as _execute_run
    # BackgroundTasks awaits a coroutine function on the same event loop — no
    # separate thread/process, so this really is the in-process execution
    # path the Worker-wiring decision calls for, not a disguised subprocess.
    background_tasks.add_task(_execute_run, run_id)
    return {"run_id": run_id, "status": "pending", "started": True}


@router.post("/{run_id}/status")
async def update_run_status(
    run_id: str,
    request: StatusUpdateRequest,
    _auth: None = Depends(require_internal_service),
):
    """Lifecycle transition (running/completed/failed). Idempotent."""
    try:
        result = await db_utils.update_run_status(
            run_id,
            status=request.status,
            started_at=request.started_at,
            ended_at=request.ended_at,
            failure_reason=request.failure_reason,
        )
    except Exception as e:
        logger.error(
            f"POST /internal/reports/runs/{run_id}/status (status={request.status}) failed: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to update run status")
    if result is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run_id": run_id, "status": result}


@router.post("/{run_id}/results")
async def submit_results(
    run_id: str,
    request: ResultsSubmitRequest,
    _auth: None = Depends(require_internal_service),
):
    """Submit results as queries complete. Batchable; idempotent per query_id (upsert).

    Mirrors the status-transition idempotency guard: once a run is in a
    terminal state, late results are dropped (200 with `results_written: 0`)
    rather than mutating the closed-out run.
    """
    status_obj = await db_utils.get_run_status(run_id)
    if not status_obj:
        raise HTTPException(status_code=404, detail="Run not found")

    if status_obj["status"] in _TERMINAL_RUN_STATES:
        logger.info(
            f"Dropping {len(request.results)} late result(s) for run {run_id} "
            f"(status={status_obj['status']})"
        )
        return {
            "run_id": run_id,
            "results_written": 0,
            "ignored": True,
            "reason": f"run is {status_obj['status']}",
        }

    items = [r.model_dump() if hasattr(r, "model_dump") else r.dict() for r in request.results]
    count = await db_utils.upsert_results(run_id, items)
    return {"run_id": run_id, "results_written": count}


@router.post("/{run_id}/analysis")
async def submit_analysis(
    run_id: str,
    request: AnalysisSubmitRequest,
    _auth: None = Depends(require_internal_service),
):
    """Store the analysis-prompt output. Runs once after all queries complete."""
    ok = await db_utils.set_run_analysis(run_id, request.overall_summary)
    if not ok:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run_id": run_id, "message": "Analysis stored"}
