"""Usage analytics query API, ported from fs-agent-usage-limits'
routers/usage_analytics_router.py, rewritten to `/v1/owners/{owner_id}/usage/analytics/...`.
Batch query mode was dropped in this port — see utils/pydantic_models_usage_analytics.py.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from utils.auth.dependencies import require_owner
from utils.pydantic_models_usage_analytics import AnalyticsQueryRequest, AnalyticsQueryResponse, ServiceOptionsResponse
from utils.usage.analytics import execute_analytics_query
from utils.usage.service_operations import get_service_operations_manager
from utils.db.session import async_session
from utils.logger import logger

router = APIRouter()


@router.post("/v1/owners/{owner_id}/usage/analytics/query", response_model=AnalyticsQueryResponse, response_model_exclude_none=True, tags=["Usage Analytics"])
async def api_analytics_query(
    owner_id: str, request: AnalyticsQueryRequest, _: str = Depends(require_owner())
) -> AnalyticsQueryResponse:
    """Flexible filtering/grouping/time-bucketing analytics query with cost breakdown,
    for this account across every knowledge base it has. `group_by=["knowledge_id"]`
    gives the per-knowledge-base breakdown."""
    logger.info(f"POST /v1/owners/{owner_id}/usage/analytics/query - group_by={request.group_by}, time_bucket={request.time_bucket}")
    try:
        async with async_session() as session:
            return await execute_analytics_query(session, owner_id, request)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"POST /v1/owners/{owner_id}/usage/analytics/query - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


@router.get("/v1/owners/{owner_id}/usage/analytics/service-options", response_model=ServiceOptionsResponse, tags=["Usage Analytics"])
async def api_get_service_options(owner_id: str, _: str = Depends(require_owner())) -> ServiceOptionsResponse:
    """Static catalogue of service types/operations/labels, for UI filter dropdowns.
    The catalogue itself is global; the path is owner-scoped only so this sits with
    the rest of the surface and goes through the same auth."""
    try:
        manager = get_service_operations_manager()
        return ServiceOptionsResponse(**manager.get_service_options_response())
    except Exception as e:
        logger.error(f"GET /v1/owners/{owner_id}/usage/analytics/service-options - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")
