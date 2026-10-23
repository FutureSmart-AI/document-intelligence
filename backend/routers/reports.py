"""Batch Query Reports — public (user-facing) API.

Ported from fs-agent-dashboard's `batch_query_reports/router_restful.py` —
see docs/plan/batch-query-reports/schema.md (Feature 8).

Prefix: `/v1/{knowledge_id}/reports` — `knowledge_id` is a **path param**
(matches this service's own router convention, e.g. routers/
knowledge_invitations.py), not the donor's `?agent_id=` query param.
Auth: `require_permission(...)` — same owner/editor/viewer RBAC every other
route in this service uses (utils/auth/dependencies.py). Reports/runs are
soft-deleted, consistent with `knowledge_invitations`/`widgets_settings`.

Unlike the donor (which gated its per-agent settings sub-resource with a
bespoke owner-only check), the query-cap settings endpoints here use the
same `read`/`write` permissions as every other per-knowledge-base config
endpoint in this service (e.g. routers/usage_owner_config.py) — this
service's permission matrix already grants `write` to editors for
config-shaped resources, and introducing a second, narrower gate just for
this one endpoint would be inconsistent with how the rest of the service
already works.
"""
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

from utils.logger import logger
from utils.auth.dependencies import require_permission, resolve_kb_owner_id

from utils.reports import db_utils
from utils.reports.queue_utils import enqueue_run
from utils.reports.exporters import EXPORTERS
from utils.pydantic_models_reports import (
    CreateReportRequest,
    UpdateReportRequest,
    UpdateReportSettingsRequest,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _query_limit_message(limit: int) -> str:
    return (
        f"Report exceeds the maximum query limit ({limit}). "
        f"Reduce the number of queries or increase the configured limit."
    )


def _validate_query_groups(query_groups, limit: int) -> None:
    if not query_groups:
        raise HTTPException(status_code=400, detail="query_groups must contain at least one group")
    total_queries = 0
    for g in query_groups:
        if g.type not in ("independent", "sequential"):
            raise HTTPException(status_code=400, detail=f"Invalid group type: {g.type}")
        if not g.queries:
            raise HTTPException(status_code=400, detail="Each query group must contain at least one query")
        total_queries += len(g.queries)
    if total_queries > limit:
        raise HTTPException(status_code=400, detail=_query_limit_message(limit))


def _validate_schedule(schedule) -> None:
    if schedule and schedule.frequency == "cron" and not schedule.cron:
        raise HTTPException(status_code=400, detail="cron expression is required when frequency is 'cron'")


async def _verify_report_in_knowledge(report_id: str, knowledge_id: str) -> dict:
    report = await db_utils.get_report(report_id)
    if not report or report["knowledge_id"] != knowledge_id:
        raise HTTPException(status_code=404, detail="Report not found for this knowledge base")
    return report


async def _verify_run_in_report(run_id: str, report_id: str, knowledge_id: str) -> dict:
    await _verify_report_in_knowledge(report_id, knowledge_id)
    status_obj = await db_utils.get_run_status(run_id)
    if not status_obj or status_obj["report_id"] != report_id or status_obj["knowledge_id"] != knowledge_id:
        raise HTTPException(status_code=404, detail="Run not found for this report")
    return status_obj


def _dump(model):
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=True)
    return model.dict(exclude_unset=True)  # pragma: no cover


def _actor_email(auth_user_id: Optional[str]) -> Optional[str]:
    """`require_permission` returns a resolved user_id (usually the session
    email already — see utils/auth/auth_utils.py::extract_user_from_email);
    passed straight through as the acting caller's identity for
    owner_id/updated_by/deleted_by/cancelled_by."""
    return auth_user_id


# ---------------------------------------------------------------------------
# Report definition
# ---------------------------------------------------------------------------

@router.post("/v1/{knowledge_id}/reports", status_code=status.HTTP_201_CREATED)
async def create_report(
    knowledge_id: str,
    request: CreateReportRequest,
    user_id: Optional[str] = Depends(require_permission("write")),
):
    total_start = time.time()
    try:
        limit = await db_utils.get_effective_query_limit(knowledge_id)
        _validate_query_groups(request.query_groups, limit)
        _validate_schedule(request.schedule)

        owner_id = _actor_email(user_id) or await resolve_kb_owner_id(knowledge_id)
        query_groups = [_dump(g) for g in request.query_groups]
        schedule = _dump(request.schedule) if request.schedule else None

        report_id = await db_utils.create_report(
            knowledge_id=knowledge_id,
            owner_id=owner_id,
            name=request.name,
            query_groups=query_groups,
            report_instructions=request.report_instructions,
            analysis_prompt=request.analysis_prompt,
            schedule=schedule,
        )

        response = {"report_id": report_id}
        if request.run_immediately:
            run = await db_utils.create_run(report_id, trigger="manual", owner_id=owner_id)
            if run:
                await enqueue_run(run["run_id"])
                response["run_id"] = run["run_id"]

        logger.info(f"[PERF] Batch Query Reports create TOTAL TIME: {time.time() - total_start:.4f}s")
        return response
    except HTTPException:
        raise
    except db_utils.QueryLimitExceeded as e:
        raise HTTPException(status_code=400, detail=_query_limit_message(e.limit))
    except Exception as e:
        logger.error(f"POST /v1/{knowledge_id}/reports - Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.get("/v1/{knowledge_id}/reports")
async def list_reports(
    knowledge_id: str,
    _: Optional[str] = Depends(require_permission("read")),
):
    try:
        reports = await db_utils.list_reports(knowledge_id)
        return {"reports": reports}
    except Exception as e:
        logger.error(f"GET /v1/{knowledge_id}/reports - Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


# ---------------------------------------------------------------------------
# Settings — per-knowledge_id, runtime-configurable per-report query cap.
# Declared BEFORE the dynamic /{report_id} routes so "settings" is not
# treated as a report_id.
# ---------------------------------------------------------------------------

_MAX_QUERIES_UPPER_BOUND = 1000  # sanity ceiling; keeps a typo from disabling the cap


@router.get("/v1/{knowledge_id}/reports/settings")
async def get_report_settings(
    knowledge_id: str,
    _: Optional[str] = Depends(require_permission("read")),
):
    try:
        return await db_utils.get_report_settings(knowledge_id)
    except Exception as e:
        logger.error(f"GET /v1/{knowledge_id}/reports/settings - Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.put("/v1/{knowledge_id}/reports/settings")
async def update_report_settings(
    knowledge_id: str,
    request: UpdateReportSettingsRequest,
    user_id: Optional[str] = Depends(require_permission("write")),
):
    value = request.max_queries_per_report
    if value < 1 or value > _MAX_QUERIES_UPPER_BOUND:
        raise HTTPException(
            status_code=400,
            detail=f"max_queries_per_report must be between 1 and {_MAX_QUERIES_UPPER_BOUND}.",
        )
    try:
        result = await db_utils.update_max_queries_per_report(knowledge_id, value, _actor_email(user_id))
        return {"message": "Report settings updated successfully", **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PUT /v1/{knowledge_id}/reports/settings - Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.get("/v1/{knowledge_id}/reports/{report_id}")
async def get_report(
    knowledge_id: str,
    report_id: str,
    _: Optional[str] = Depends(require_permission("read")),
):
    try:
        return await _verify_report_in_knowledge(report_id, knowledge_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"GET /v1/{knowledge_id}/reports/{report_id} - Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.put("/v1/{knowledge_id}/reports/{report_id}")
async def update_report(
    knowledge_id: str,
    report_id: str,
    request: UpdateReportRequest,
    user_id: Optional[str] = Depends(require_permission("write")),
):
    try:
        await _verify_report_in_knowledge(report_id, knowledge_id)

        provided = request.model_fields_set if hasattr(request, "model_fields_set") else request.__fields_set__
        fields = {"updated_by": _actor_email(user_id)}
        if "name" in provided and request.name is not None:
            fields["name"] = request.name
        if "report_instructions" in provided:
            fields["report_instructions"] = request.report_instructions
        if "analysis_prompt" in provided:
            fields["analysis_prompt"] = request.analysis_prompt
        if "query_groups" in provided and request.query_groups is not None:
            limit = await db_utils.get_effective_query_limit(knowledge_id)
            _validate_query_groups(request.query_groups, limit)
            fields["query_groups"] = [_dump(g) for g in request.query_groups]
        if "schedule" in provided:
            if request.schedule is not None:
                _validate_schedule(request.schedule)
                fields["schedule"] = _dump(request.schedule)
            else:
                fields["schedule"] = None

        await db_utils.update_report(report_id, fields)
        return {"message": "Report updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PUT /v1/{knowledge_id}/reports/{report_id} - Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.delete("/v1/{knowledge_id}/reports/{report_id}")
async def delete_report(
    knowledge_id: str,
    report_id: str,
    user_id: Optional[str] = Depends(require_permission("delete")),
):
    await _verify_report_in_knowledge(report_id, knowledge_id)
    await db_utils.soft_delete_report(report_id, deleted_by=_actor_email(user_id))
    return {"message": "Report deleted successfully"}


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

@router.post("/v1/{knowledge_id}/reports/{report_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def run_report(
    knowledge_id: str,
    report_id: str,
    user_id: Optional[str] = Depends(require_permission("write")),
):
    try:
        await _verify_report_in_knowledge(report_id, knowledge_id)
        owner_id = _actor_email(user_id)
        try:
            run = await db_utils.create_run(report_id, trigger="manual", owner_id=owner_id)
        except db_utils.QueryLimitExceeded as e:
            raise HTTPException(status_code=400, detail=_query_limit_message(e.limit))
        if not run:
            raise HTTPException(status_code=404, detail="Report not found")
        await enqueue_run(run["run_id"])
        return {"run_id": run["run_id"], "status": "pending"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"POST /v1/{knowledge_id}/reports/{report_id}/runs - Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.get("/v1/{knowledge_id}/reports/{report_id}/runs")
async def list_runs(
    knowledge_id: str,
    report_id: str,
    _: Optional[str] = Depends(require_permission("read")),
):
    await _verify_report_in_knowledge(report_id, knowledge_id)
    runs = await db_utils.list_runs(report_id)
    return {"runs": runs}


@router.get("/v1/{knowledge_id}/reports/{report_id}/runs/{run_id}/status")
async def get_run_status(
    knowledge_id: str,
    report_id: str,
    run_id: str,
    _: Optional[str] = Depends(require_permission("read")),
):
    return await _verify_run_in_report(run_id, report_id, knowledge_id)


@router.get("/v1/{knowledge_id}/reports/{report_id}/runs/{run_id}/export")
async def export_run(
    knowledge_id: str,
    report_id: str,
    run_id: str,
    format: str = Query(
        "json",
        description="Export format: json (default, full run data), md (human-readable report), or csv (spreadsheet).",
    ),
    _: Optional[str] = Depends(require_permission("read")),
):
    fmt = (format or "json").lower()
    if fmt not in EXPORTERS:
        raise HTTPException(status_code=400, detail="format must be one of: json, md, csv")
    await _verify_run_in_report(run_id, report_id, knowledge_id)

    run = await db_utils.get_run_full(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    renderer, media_type, ext = EXPORTERS[fmt]
    body = renderer(run)
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="report_run_{run_id}.{ext}"'},
    )


@router.get("/v1/{knowledge_id}/reports/{report_id}/runs/{run_id}")
async def get_run(
    knowledge_id: str,
    report_id: str,
    run_id: str,
    _: Optional[str] = Depends(require_permission("read")),
):
    await _verify_run_in_report(run_id, report_id, knowledge_id)
    run = await db_utils.get_run_full(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.post("/v1/{knowledge_id}/reports/{report_id}/runs/{run_id}/cancel")
async def cancel_run(
    knowledge_id: str,
    report_id: str,
    run_id: str,
    user_id: Optional[str] = Depends(require_permission("write")),
):
    await _verify_run_in_report(run_id, report_id, knowledge_id)
    result = await db_utils.cancel_run(run_id, cancelled_by=_actor_email(user_id))
    return {"run_id": run_id, "status": result}
