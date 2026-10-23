"""Account-wide tier/platform-limit configuration, ported from
fs-agent-usage-limits' routers/owner_config_router.py.

Tier/platform limits are genuinely account-wide (one owner, many knowledge bases) —
see utils/models/usage_owner_config.py — so these routes are owner-scoped
(`/v1/owners/{owner_id}/usage/owner-config/...`) and gated by require_owner: only the
account owner (or a master-key service caller) may read or change an account's tier,
limits and enabled services.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import JSONResponse

from utils.auth.dependencies import require_owner
from utils.pydantic_models_usage_owner_config import (
    OwnerConfigResponse, OwnerConfigCreateRequest, OwnerConfigPatchRequest,
    ServiceEnableRequest, ServiceDisableRequest, ServiceResponse, ServicesListResponse, ValidServicesResponse, ServiceMetadata,
)
from utils.usage.owner_config import get_owner_config, create_or_update_owner_config, patch_owner_config, enable_service, disable_service, get_enabled_services
from utils.usage.owner_defaults import get_defaults_manager
from utils.db.session import async_session
from utils.logger import logger

router = APIRouter()


@router.get("/v1/owners/{owner_id}/usage/owner-config", response_model=OwnerConfigResponse, tags=["Usage Owner Config"])
async def api_get_owner_config(owner_id: str, _: str = Depends(require_owner())):
    """Owner (billing account) configuration that applies to this knowledge base.
    Returns defaults, not 404, when the owner has no DB record yet."""
    try:
        async with async_session() as session:
            result = await get_owner_config(session, owner_id)
            if not result:
                result = get_defaults_manager().get_default_config(owner_id)
            return result
    except Exception as e:
        logger.error(f"GET /v1/owners/{owner_id}/usage/owner-config - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put(
    "/v1/owners/{owner_id}/usage/owner-config", response_model=OwnerConfigResponse, status_code=status.HTTP_201_CREATED,
    responses={200: {"model": OwnerConfigResponse}}, tags=["Usage Owner Config"],
)
async def api_put_owner_config(owner_id: str, payload: OwnerConfigCreateRequest, _: str = Depends(require_owner())):
    try:
        async with async_session() as session:
            result, is_new = await create_or_update_owner_config(session, owner_id, payload)
            return result if is_new else JSONResponse(result.dict(), status_code=200)
    except Exception as e:
        logger.error(f"PUT /v1/owners/{owner_id}/usage/owner-config - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/v1/owners/{owner_id}/usage/owner-config", response_model=OwnerConfigResponse, tags=["Usage Owner Config"])
async def api_patch_owner_config(owner_id: str, payload: OwnerConfigPatchRequest, _: str = Depends(require_owner())):
    if not (payload.tier_type or payload.base_tier or payload.platform_limits or payload.default_models):
        raise HTTPException(status_code=400, detail="No fields to update provided")
    try:
        async with async_session() as session:
            try:
                return await patch_owner_config(session, owner_id, payload)
            except ValueError:
                raise HTTPException(status_code=404, detail=f"Owner not found: {owner_id}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PATCH /v1/owners/{owner_id}/usage/owner-config - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/v1/owners/{owner_id}/usage/owner-config/services/enable", response_model=ServiceResponse, tags=["Usage Owner Config"])
async def api_enable_service(owner_id: str, payload: ServiceEnableRequest, _: str = Depends(require_owner())):
    defaults_manager = get_defaults_manager()
    if not defaults_manager.validate_service_name(payload.service_name):
        raise HTTPException(status_code=400, detail=f"Invalid service_name: '{payload.service_name}'. Valid: {defaults_manager.valid_services}")
    try:
        async with async_session() as session:
            result = await enable_service(session, owner_id, payload.service_name, payload.features)
            status_code = 201 if result.enabled_at and result.enabled else 200
            return JSONResponse(result.dict(), status_code=status_code)
    except Exception as e:
        logger.error(f"POST /v1/owners/{owner_id}/usage/owner-config/services/enable - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/v1/owners/{owner_id}/usage/owner-config/services/disable", response_model=ServiceResponse, tags=["Usage Owner Config"])
async def api_disable_service(owner_id: str, payload: ServiceDisableRequest, _: str = Depends(require_owner())):
    defaults_manager = get_defaults_manager()
    if not defaults_manager.validate_service_name(payload.service_name):
        raise HTTPException(status_code=400, detail=f"Invalid service_name: '{payload.service_name}'. Valid: {defaults_manager.valid_services}")
    try:
        async with async_session() as session:
            return await disable_service(session, owner_id, payload.service_name)
    except Exception as e:
        logger.error(f"POST /v1/owners/{owner_id}/usage/owner-config/services/disable - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/v1/owners/{owner_id}/usage/owner-config/valid-services", response_model=ValidServicesResponse, tags=["Usage Owner Config"])
async def api_get_valid_services(owner_id: str, _: str = Depends(require_owner())):
    defaults_manager = get_defaults_manager()
    return ValidServicesResponse(services=[ServiceMetadata(**meta) for meta in defaults_manager.get_all_services_metadata()])


@router.get("/v1/owners/{owner_id}/usage/owner-config/services", response_model=ServicesListResponse, tags=["Usage Owner Config"])
async def api_list_services_for_owner(owner_id: str, enabled_only: bool = Query(True), _: str = Depends(require_owner())):
    try:
        async with async_session() as session:
            return await get_enabled_services(session, owner_id, enabled_only=enabled_only)
    except Exception as e:
        logger.error(f"GET /v1/owners/{owner_id}/usage/owner-config/services - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
