"""Enrichment Analytics — read endpoints for the Analytics screen, plus the
worker-facing ingest that feeds them.

The enrichment counterpart of routers/knowledge_health.py (chat Agent Health).
See docs/plan/enrichment-analytics/plan.md for the design and
docs/api/enrichment-health.md for the contract.

Paths are grouped under `/v1/{knowledge_id}/health/enrichment/...` — under
`health`, not under `enrichment` — for two reasons: the Analytics screen then
calls one path family for both halves of itself, and `/v1/{knowledge_id}/enrichment`
already means *enrichment configuration*
(routers/enrichment_configuration_new.py), which is a different thing from
enrichment analytics and should not be reachable by guessing a suffix.

Auth:
- Reads use `read` RBAC (owner fast-path), matching every per-knowledge_id GET
  in this repo and the chat health endpoints specifically.
- The ingest uses `require_internal_service` (master `x-api-key` only, no
  session fallback), the same posture as routers/reports_internal.py. It is
  service-to-service only and never browser-facing: the worker is the only
  caller, and a row here is an assertion about what the pipeline did.

Unlike chat health, ingest is over HTTP rather than in-process. The enrichment
step runs in a separate Celery process in fs-agent-workers, so there is no
local graph state to build a row from — the trade-off is set out in the
migration 043 docstring.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from utils.auth.dependencies import require_internal_service, require_permission
from utils.db.session import async_session
from utils.knowledge_health.analytics import _window, _to_aware_utc
from utils.knowledge_health.enrichment_cost_finalizer import (
    backfill_enrichment_costs,
)
from utils.knowledge_health.enrichment_analytics import (
    get_enrichment_documents,
    get_enrichment_failures,
    get_enrichment_scorecard,
    get_model_comparison,
    record_enrichment_event,
)
from utils.logger import logger
from utils.pydantic_models_knowledge_enrichment import (
    DocumentsResponse,
    EnrichmentEventRequest,
    EnrichmentEventResponse,
    EnrichmentFailuresResponse,
    EnrichmentScorecardResponse,
    ModelComparisonResponse,
)

# Imported rather than redefined so there is exactly one definition of this
# repo's period/custom-range contract. Both halves of the Analytics screen must
# agree on what "last week" means, or the two panels silently describe
# different windows.
from routers.knowledge_health import _validate_custom_range, _validate_period

router = APIRouter()

VALID_STATUSES = ("success", "validation_failed", "error")
SORT_FIELDS = ("created_at", "duration_ms", "total_tokens", "char_count")


# ---------------------------------------------------------------------------
# Ingest (worker -> API)
# ---------------------------------------------------------------------------

@router.post(
    "/internal/enrichment-health/events/{knowledge_id}",
    response_model=EnrichmentEventResponse,
    tags=["Enrichment Analytics"],
)
async def api_record_enrichment_event(
    knowledge_id: str,
    request: EnrichmentEventRequest,
    _: str = Depends(require_internal_service),
) -> EnrichmentEventResponse:
    """Record one enrichment attempt on one document.

    Idempotent on `event_key`: a retried POST returns 200 with
    `duplicate: true` rather than inserting twice or erroring. The worker
    generates the key per attempt, so a Celery retry that re-runs the whole
    enrichment produces a *new* key (a genuinely new attempt), while a retried
    POST of the same attempt does not.
    """
    logger.info(
        f"POST /internal/enrichment-health/events/{knowledge_id} - "
        f"status={request.status} model={request.model} object_id={request.object_id}"
    )
    if request.status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {VALID_STATUSES}")
    try:
        event_key = UUID(request.event_key)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=422, detail="event_key must be a UUID")

    row = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    row["event_key"] = event_key
    # Pydantic models -> plain dicts for the JSONB column.
    row["field_data"] = [
        f.model_dump() if hasattr(f, "model_dump") else (f.dict() if hasattr(f, "dict") else f)
        for f in (request.field_data or [])
    ]

    try:
        async with async_session() as session:
            _, duplicate = await record_enrichment_event(session, knowledge_id, row)

        # Fire cost finalization ourselves rather than waiting for someone to press
        # sync. LangSmith prices runs asynchronously, so the finalizer sleeps
        # through a retry ladder (~150s) before giving up — which is exactly why it
        # must not be awaited here: the worker's POST would be held open for the
        # duration. Skipped on a duplicate, since the original POST already
        # scheduled one and a second would re-read the same run for the same answer.
        if not duplicate and request.langsmith_run_id:
            from utils.knowledge_health.enrichment_cost_finalizer import (
                schedule_enrichment_finalize,
            )
            schedule_enrichment_finalize(
                event_key=str(event_key), run_id=request.langsmith_run_id
            )

        return EnrichmentEventResponse(recorded=True, event_key=str(event_key), duplicate=duplicate)
    except Exception as e:
        logger.error(
            f"POST /internal/enrichment-health/events/{knowledge_id} - unexpected error: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

@router.get(
    "/v1/{knowledge_id}/health/enrichment/scorecard",
    response_model=EnrichmentScorecardResponse,
    tags=["Enrichment Analytics"],
)
async def api_enrichment_scorecard(
    knowledge_id: str,
    period: str = Query("week"),
    top_n: int = Query(10, ge=1, le=50, description="Length of perf.slowest / cost.most_expensive"),
    start_date: Optional[datetime] = Query(None, description="Custom range start (ISO-8601); with end_date overrides period"),
    end_date: Optional[datetime] = Query(None, description="Custom range end, exclusive (ISO-8601)"),
    _: Optional[str] = Depends(require_permission("read")),
) -> EnrichmentScorecardResponse:
    """Documents enriched, outcome mix, latency, cost and period-over-period
    trend for the enrichment step — the counterpart of the chat scorecard."""
    _validate_period(period)
    _validate_custom_range(start_date, end_date)
    try:
        async with async_session() as session:
            return await get_enrichment_scorecard(
                session, knowledge_id, period=period, top_n=top_n, start=start_date, end=end_date
            )
    except Exception as e:
        logger.error(
            f"GET /v1/{knowledge_id}/health/enrichment/scorecard - unexpected error: {e}", exc_info=True
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


@router.get(
    "/v1/{knowledge_id}/health/enrichment/model-comparison",
    response_model=ModelComparisonResponse,
    tags=["Enrichment Analytics"],
)
async def api_enrichment_model_comparison(
    knowledge_id: str,
    period: str = Query("week"),
    model: Optional[str] = Query(None, description="Narrow to one model — also scopes by_field to it"),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    _: Optional[str] = Depends(require_permission("read")),
) -> ModelComparisonResponse:
    """One row per (provider, model, strategy): success rate, latency (raw and
    per 1k characters), tokens, cost and field coverage, plus per-field coverage
    across the window.

    Read `success_rate` before the other columns. Every other column describes
    only the attempts that produced numbers, so a model that fails often reads
    as cheap and fast until you look at it.
    """
    _validate_period(period)
    _validate_custom_range(start_date, end_date)
    try:
        async with async_session() as session:
            return await get_model_comparison(
                session, knowledge_id, period=period, start=start_date, end=end_date, model=model
            )
    except Exception as e:
        logger.error(
            f"GET /v1/{knowledge_id}/health/enrichment/model-comparison - unexpected error: {e}", exc_info=True
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


@router.get(
    "/v1/{knowledge_id}/health/enrichment/documents",
    response_model=DocumentsResponse,
    tags=["Enrichment Analytics"],
)
async def api_enrichment_documents(
    knowledge_id: str,
    period: str = Query("week"),
    status_filter: Optional[str] = Query(None, alias="status", description=f"One of {VALID_STATUSES}"),
    failure_category: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    object_id: Optional[str] = Query(None, description="Exact document lookup — returns every attempt, window ignored"),
    include_field_data: bool = Query(False, description="Include the per-field breakdown on each row"),
    sort_by: str = Query("created_at", description=f"One of {SORT_FIELDS}"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    _: Optional[str] = Depends(require_permission("read")),
) -> DocumentsResponse:
    """Per-attempt drill-down, filterable and paginated.

    With `object_id` set, the resolved window is echoed but not applied — an
    exact document lookup returns every attempt on it, including the failures
    that preceded a success.
    """
    _validate_period(period)
    _validate_custom_range(start_date, end_date)
    if status_filter and status_filter not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {VALID_STATUSES}")
    if sort_by not in SORT_FIELDS:
        raise HTTPException(status_code=422, detail=f"sort_by must be one of {SORT_FIELDS}")
    try:
        async with async_session() as session:
            return await get_enrichment_documents(
                session, knowledge_id, period=period, status=status_filter,
                failure_category=failure_category, model=model, object_id=object_id,
                include_field_data=include_field_data, sort_by=sort_by,
                limit=limit, offset=offset, start=start_date, end=end_date,
            )
    except Exception as e:
        logger.error(
            f"GET /v1/{knowledge_id}/health/enrichment/documents - unexpected error: {e}", exc_info=True
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


@router.get(
    "/v1/{knowledge_id}/health/enrichment/failures",
    response_model=EnrichmentFailuresResponse,
    tags=["Enrichment Analytics"],
)
async def api_enrichment_failures(
    knowledge_id: str,
    period: str = Query("week"),
    failure_category: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    _: Optional[str] = Depends(require_permission("read")),
) -> EnrichmentFailuresResponse:
    """Failure counts by category and by model, plus the documents that keep
    failing.

    A window that predates the worker's failure-emit path shows zero failures
    regardless of what happened, because a failed enrichment used to produce no
    row anywhere.
    """
    _validate_period(period)
    _validate_custom_range(start_date, end_date)
    try:
        async with async_session() as session:
            return await get_enrichment_failures(
                session, knowledge_id, period=period, failure_category=failure_category,
                limit=limit, offset=offset, start=start_date, end=end_date,
            )
    except Exception as e:
        logger.error(
            f"GET /v1/{knowledge_id}/health/enrichment/failures - unexpected error: {e}", exc_info=True
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


@router.post(
    "/v1/{knowledge_id}/health/enrichment/sync-costs",
    tags=["Enrichment Analytics"],
)
async def api_sync_enrichment_costs(
    knowledge_id: str,
    period: str = Query("week"),
    start_date: Optional[datetime] = Query(None, description="Custom range start (ISO-8601); with end_date overrides period"),
    end_date: Optional[datetime] = Query(None, description="Custom range end, exclusive (ISO-8601)"),
    only_incomplete: bool = Query(True, description="Skip rows already finalized. False re-prices them."),
    limit: int = Query(200, ge=1, le=500, description="Cap rows priced by this call"),
    _: str = Depends(require_permission("write")),
):
    """Price a window's enrichment attempts from their stored LangSmith run ids.

    The enrichment counterpart of the chat side's `/health/sync-costs`. It
    existed as a function with no caller: `backfill_enrichment_costs` was
    written for "the manual endpoint and the periodic sweeper" and neither
    invoked it, so the ONLY path to a finalized enrichment cost was the eager
    per-document task fired at ingest. That task dies with its process — a
    deploy is enough — and nothing retried it, so rows silently kept a read-time
    estimate forever.

    A `write` operation: it UPDATEs cost_usd / cost_complete, and each row costs
    one LangSmith `read_run`. Hence POST, `write` permission, and a capped limit.

    **Rows with no stored `langsmith_run_id` are counted in `skipped`, never in
    `failed`.** They cannot be priced by this route at all — a run id cannot be
    created after the fact — so a large `skipped` is a permanent hole in cost
    history, not a backlog that a later call will clear. Rows written before
    migration 047, or with tracing off, are all in that bucket.

    Prefer letting the sweeper do this (`COST_SWEEPER_ENABLED`, which now covers
    enrichment as well as chat). This endpoint is for forcing a window
    immediately — after a deploy that killed in-flight finalize tasks, or to
    confirm the path works without waiting out the sweep interval.
    """
    _validate_period(period)
    _validate_custom_range(start_date, end_date)
    since, until, _prev = _window(period)
    if start_date and end_date:
        since, until = _to_aware_utc(start_date), _to_aware_utc(end_date)
    try:
        return await backfill_enrichment_costs(
            knowledge_id, since=since, until=until,
            only_incomplete=only_incomplete, limit=limit,
        )
    except Exception as e:
        logger.error(
            f"POST /v1/{knowledge_id}/health/enrichment/sync-costs - unexpected error: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


@router.post(
    "/v1/{knowledge_id}/health/enrichment/validate",
    tags=["Enrichment Analytics"],
)
async def api_validate_enrichment(
    knowledge_id: str,
    period: str = Query("week"),
    start_date: Optional[datetime] = Query(None, description="Custom range start (ISO-8601); with end_date overrides period"),
    end_date: Optional[datetime] = Query(None, description="Custom range end (ISO-8601)"),
    object_id: Optional[str] = Query(None, description="Validate one document; ignores the window"),
    only_unvalidated: bool = Query(True, description="Skip rows already scored. False re-scores them."),
    use_llm: bool = Query(True, description="Run the paid Tier 2 judge. False runs only the free deterministic checks."),
    limit: Optional[int] = Query(None, ge=1, le=200, description="Cap documents this call; defaults to EXTRACTION_VALIDATION_MAX_DOCS"),
    judge_provider: Optional[str] = Query(None, description="Override the judge's provider for this call only"),
    judge_model: Optional[str] = Query(None, description="Override the judge's model for this call only"),
    judge_effort: Optional[str] = Query(None, description="Anthropic effort for the judge: low | medium | high"),
    repeats: Optional[int] = Query(None, ge=1, le=10, description="Grade each document N times and report mean plus spread; costs N judge calls per document"),
    _: str = Depends(require_permission("write")),
):
    """Score extraction quality for the enrichment events in a window.

    A `write` operation, not a read: it UPDATEs the validation columns on
    knowledge_enrichment_events, and (with `use_llm=true`) spends money on judge
    calls. Both are why this is a POST and why `limit` is capped.

    Two tiers — see utils/knowledge_health/extraction_validator.py:

      Tier 1  deterministic, free, always runs. Type/choice/structural
              conformance plus numeric-series reconciliation. This is the tier
              that caught the defect the feature exists for; `use_llm=false`
              runs it alone at zero cost.
      Tier 2  an LLM judge, for what arithmetic cannot see: whether a value was
              invented, and whether something in the source was dropped.

    `only_unvalidated=true` (the default) makes repeat calls cheap and
    idempotent. Set it false only to deliberately re-score, e.g. after changing
    the judge prompt — it will re-spend on every matched row.

    The response always carries `remaining`. A window larger than the cap is
    PARTLY validated, and reading a partial pass as a complete one is exactly
    the class of mistake this endpoint was built to stop.

    `judge_provider` / `judge_model` / `judge_effort` override the deployed judge
    for ONE call. They exist because those values come from .env.prod inside the
    image, so comparing two graders otherwise meant a build and deploy per arm.
    Scores produced under an override are not comparable with the deployed
    default and the response echoes the values used so a reader can tell.

    `repeats` grades each document N times and stores the MEAN, reporting
    `*_min`/`*_max` beside it. Measured on a 55-row statement, four judge calls
    on byte-identical input returned correctness 0.75/0.75/1.0/0.7273 — so a
    single call is one sample from a distribution while looking like a
    measurement. Cost scales linearly: N judge calls per document.
    """
    _validate_period(period)
    _validate_custom_range(start_date, end_date)
    try:
        from utils.knowledge_health.analytics import _resolve_window
        from utils.knowledge_health.validation_runner import validate_window
        from utils.setup_env import EXTRACTION_VALIDATION_ENABLED

        if not EXTRACTION_VALIDATION_ENABLED:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Extraction validation is disabled (EXTRACTION_VALIDATION_ENABLED=false)",
            )

        since, until, _prev = _resolve_window(period, start_date, end_date)
        async with async_session() as session:
            result = await validate_window(
                session, knowledge_id, since, until,
                only_unvalidated=only_unvalidated, use_llm=use_llm,
                limit=limit, object_id=object_id,
                judge_provider=judge_provider, judge_model=judge_model,
                judge_effort=judge_effort, repeats=repeats,
            )
        return {
            "knowledge_id": knowledge_id,
            "period": period,
            "since": since.isoformat().replace("+00:00", "Z"),
            "until": until.isoformat().replace("+00:00", "Z"),
            **result,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"POST /v1/{knowledge_id}/health/enrichment/validate - unexpected error: {e}", exc_info=True
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")
