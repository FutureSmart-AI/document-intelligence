"""Quota/limit checking, ported from fs-agent-usage-limits'
routers/limit_checking_router.py, rewritten to `/v1/owners/{owner_id}/usage/limits/...`.

Both the ceilings (OwnerPlatformLimits) and the current usage they're compared
against are account-wide, so this surface is owner-scoped end to end — see
utils/usage/limit_checking.py.
"""

from fastapi import APIRouter, Depends, HTTPException

from utils.auth.dependencies import require_internal_service, require_owner
from utils.usage.internal_accounts import internal_spend
from utils.pydantic_models_usage_limits import UsageStatusResponse, UsageCheckRequest, UsageCheckResponse, LimitsScreenResponse
from utils.usage.limit_checking import get_usage_status, check_operation_allowed, get_usage_limits_screen
from utils.db.session import async_session
from utils.logger import logger

router = APIRouter()


@router.get("/v1/owners/{owner_id}/usage/limits/status", response_model=UsageStatusResponse, tags=["Usage Limits"])
async def api_get_usage_status(owner_id: str, _: str = Depends(require_owner())) -> UsageStatusResponse:
    """Current usage vs. account-wide platform limits for this account, for the
    current calendar month. null in `remaining` means unlimited."""
    try:
        async with async_session() as session:
            return await get_usage_status(session, owner_id)
    except Exception as e:
        logger.error(f"GET /v1/owners/{owner_id}/usage/limits/status - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/v1/owners/{owner_id}/usage/limits/screen", response_model=LimitsScreenResponse, tags=["Usage Limits"])
async def api_get_usage_limits_screen(owner_id: str, _: str = Depends(require_owner())) -> LimitsScreenResponse:
    """Screen-shaped Limits tab: the tier, the billing window, the owner's own BYOK
    providers, and one row per limit already carrying `used`/`limit`/`percent`/the
    display wording/`enforced`/`lifted` — so a UI draws the tab with no arithmetic.
    See docs/product/usage-and-limits-screen.md §7 — modelled on NL2SQL's own
    `GET /v1/owners/{owner_id}/limits`. Repackages
    /usage/limits/status and /usage/owner-config; adds no ceiling."""
    try:
        async with async_session() as session:
            return await get_usage_limits_screen(session, owner_id)
    except Exception as e:
        logger.error(f"GET /v1/owners/{owner_id}/usage/limits/screen - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/v1/usage/internal-accounts", tags=["Usage Limits"], dependencies=[Depends(require_internal_service)])
async def api_internal_account_spend() -> dict:
    """Master key only. Every verified internal (FutureSmart / AI Demos) account and
    its usage this calendar month — messages, LLM tokens and estimated cost — so
    internal spend, which no trial limit bounds, can be watched on its own. See
    utils/usage/internal_accounts.py."""
    try:
        accounts = await internal_spend()
    except Exception as e:
        logger.error(f"GET /v1/usage/internal-accounts - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    return {
        "accounts": accounts,
        "total_cost_usd_this_month": round(sum(a["cost_usd_this_month"] or 0 for a in accounts), 4),
        "total_messages_this_month": sum(a["messages_this_month"] or 0 for a in accounts),
    }


@router.post("/v1/owners/{owner_id}/usage/limits/check", response_model=UsageCheckResponse, tags=["Usage Limits"])
async def api_check_usage(owner_id: str, request: UsageCheckRequest, _: str = Depends(require_owner())) -> UsageCheckResponse:
    """Pre-check whether a requested operation would exceed the account's limits."""
    try:
        async with async_session() as session:
            try:
                return await check_operation_allowed(session, owner_id, request.operation, request.estimated_metrics)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"POST /v1/owners/{owner_id}/usage/limits/check - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
