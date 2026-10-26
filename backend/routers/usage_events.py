"""Usage event recording + retrieval, ported from fs-agent-usage-limits'
routers/usage_events_router.py.

Routes are **owner-scoped** (`/v1/owners/{owner_id}/usage/...`) — usage is recorded,
reported and limited per account, across every knowledge base the owner has (see
docs/plan/usage-and-limits/, migration 039). `knowledge_id` is an optional
*filter* on reads and an optional per-event field on writes, for a per-knowledge-base
breakdown — never the scope.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import Optional
from datetime import datetime

from utils.auth.dependencies import require_owner
from utils.pydantic_models_usage_events import (
    UsageEventsRequest, UsageEventsResponse, UsageEventsListResponse, AggregatedUsageResponse, UsageSummaryResponse,
)
from utils.usage.event_recording import record_usage_events
from utils.usage.analytics import get_usage_events, get_aggregated_usage, get_usage_summary
from utils.db.session import async_session
from utils.logger import logger

router = APIRouter()


@router.post("/v1/owners/{owner_id}/usage/events", response_model=UsageEventsResponse, tags=["Usage Events"])
async def api_record_usage_events(
    owner_id: str, request: UsageEventsRequest, _: Optional[str] = Depends(require_owner())
) -> UsageEventsResponse:
    """Record usage events (bulk or single) for this account. Always returns
    200 — check failed_count/failed_events in the body for per-event failures.

    Each event may carry its own `knowledge_id` to attribute it to one knowledge
    base; it's optional, and events in one batch may name different ones."""
    logger.info(f"POST /v1/owners/{owner_id}/usage/events - {len(request.events)} events")
    try:
        return await record_usage_events(owner_id, request.events)
    except Exception as e:
        logger.error(f"POST /v1/owners/{owner_id}/usage/events - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


@router.get("/v1/owners/{owner_id}/usage/events", response_model=UsageEventsListResponse, tags=["Usage Events"])
async def api_get_usage_events(
    owner_id: str,
    key_type: Optional[str] = Query(None, description="'platform-key' or 'owner-key'"),
    operation: Optional[str] = Query(None),
    knowledge_id: Optional[str] = Query(None, description="Optional filter — narrow to one knowledge base"),
    start_date: Optional[str] = Query(None, description="ISO8601"),
    end_date: Optional[str] = Query(None, description="ISO8601"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _: Optional[str] = Depends(require_owner()),
) -> UsageEventsListResponse:
    """Paginated usage events for this account, across every knowledge base, with
    optional filters."""
    try:
        start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00")) if start_date else None
        end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00")) if end_date else None
        async with async_session() as session:
            return await get_usage_events(session, owner_id, key_type, operation, knowledge_id, start_dt, end_dt, limit, offset)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {e}")
    except Exception as e:
        logger.error(f"GET /v1/owners/{owner_id}/usage/events - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


@router.get("/v1/owners/{owner_id}/usage/aggregate", response_model=AggregatedUsageResponse, tags=["Usage Events"])
async def api_get_aggregated_usage(
    owner_id: str,
    operation: Optional[str] = Query(None),
    label: Optional[str] = Query(None),
    knowledge_id: Optional[str] = Query(None, description="Optional filter — narrow to one knowledge base"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    group_by: str = Query("service_type,operation"),
    _: Optional[str] = Depends(require_owner()),
) -> AggregatedUsageResponse:
    """Aggregated usage metrics for this account, grouped by the given dimensions.
    `group_by=knowledge_id` gives the per-knowledge-base breakdown."""
    try:
        start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00")) if start_date else None
        end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00")) if end_date else None
        group_by_list = [f.strip() for f in group_by.split(",")]
        async with async_session() as session:
            return await get_aggregated_usage(session, owner_id, operation, label, knowledge_id, start_dt, end_dt, group_by_list)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid input: {e}")
    except Exception as e:
        logger.error(f"GET /v1/owners/{owner_id}/usage/aggregate - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


@router.get("/v1/owners/{owner_id}/usage/summary", response_model=UsageSummaryResponse, tags=["Usage Events"])
async def api_get_usage_summary(
    owner_id: str,
    knowledge_id: Optional[str] = Query(None, description="Optional filter — narrow to one knowledge base"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    _: Optional[str] = Depends(require_owner()),
) -> UsageSummaryResponse:
    """Usage summary for this account, across every knowledge base, grouped by
    service_type/operation. This is the endpoint a home-screen usage widget calls —
    it needs no knowledge base selected."""
    try:
        start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00")) if start_date else None
        end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00")) if end_date else None
        async with async_session() as session:
            return await get_usage_summary(session, owner_id, knowledge_id, start_dt, end_dt)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {e}")
    except Exception as e:
        logger.error(f"GET /v1/owners/{owner_id}/usage/summary - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")
