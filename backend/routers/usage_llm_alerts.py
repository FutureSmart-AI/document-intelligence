"""LLM provider status/quota checking, ported from fs-agent-usage-limits'
routers/llm_alerts_router.py. Email alerting is dropped (see
utils/usage/llm_alerts.py docstring) — a low-balance/rate-limit finding is
returned in the response and logged, not emailed.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from utils.auth.dependencies import require_owner
from utils.usage.llm_alerts import check_provider_status
from utils.logger import logger

router = APIRouter()


class ProviderStatusCheckRequest(BaseModel):
    provider: str = Field(..., description="openai, anthropic, groq, google, deepseek, minimax, llamaparse, firecrawl")
    api_key: str = Field(..., description="The API key to check")


@router.post("/v1/owners/{owner_id}/usage/llm-alerts/check-status", status_code=status.HTTP_200_OK, tags=["Usage LLM Alerts"])
async def check_provider_api_status(owner_id: str, payload: ProviderStatusCheckRequest, _: Optional[str] = Depends(require_owner())):
    """Test a provider API key by making a lightweight call, detecting low-balance
    and rate-limit conditions. owner_id scopes the auth check only — the provider
    check itself is not persisted or attributed to anything. Owner-scoped because
    BYOK keys are account-level, so validating one is an account-owner action."""
    try:
        result = check_provider_status(payload.provider, payload.api_key)
        return {
            "status": "success",
            "provider": payload.provider,
            "is_valid": result.is_valid,
            "has_low_balance": result.has_low_balance,
            "has_rate_limit": result.has_rate_limit,
            "error_type": result.error_type,
            "error_message": result.error_message,
            "http_status": result.http_status,
            "error_code": result.error_code,
            "metadata": result.metadata,
        }
    except Exception as exc:
        logger.error(f"Unexpected error checking provider status for '{payload.provider}': {exc}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to check provider status: {exc}")
