"""
Admin Config Router

Internal-only endpoints for managing global enrichment defaults.
No user-facing auth — these endpoints are not exposed to end users.

Endpoints:
- GET   /v1/admin/enrichment-config  — read current global default (latest versioned row)
- PATCH /v1/admin/enrichment-config  — insert new versioned row with updated fields
"""

from fastapi import HTTPException, APIRouter
from pydantic import BaseModel
from typing import Optional
import traceback

from utils.logger import logger
from utils.db.knowledge_setting_ops import (
    get_enrichment_default_config,
    update_enrichment_default_config,
)

router = APIRouter()


class EnrichmentDefaultConfigResponse(BaseModel):
    enrichment_model: str
    enrichment_provider: str
    enrichment_temperature: float
    enrichment_fallback_model: str
    enrichment_fallback_provider: str
    updated_at: Optional[str] = None


class PatchEnrichmentDefaultConfigBody(BaseModel):
    enrichment_model: Optional[str] = None
    enrichment_provider: Optional[str] = None
    enrichment_temperature: Optional[float] = None
    enrichment_fallback_model: Optional[str] = None
    enrichment_fallback_provider: Optional[str] = None



@router.get("/v1/admin/enrichment-config", response_model=EnrichmentDefaultConfigResponse)
async def get_enrichment_default():
    """Return the current global enrichment default config."""
    try:
        config = await get_enrichment_default_config()
        if not config:
            raise HTTPException(
                status_code=404,
                detail="enrichment_default_config row not found — run migration first"
            )
        return EnrichmentDefaultConfigResponse(**config)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving enrichment default config: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to retrieve enrichment default config")


@router.patch("/v1/admin/enrichment-config", response_model=EnrichmentDefaultConfigResponse)
async def patch_enrichment_default(body: PatchEnrichmentDefaultConfigBody):
    """
    Insert a new versioned row into enrichment_default_config.
    Only fields explicitly provided are changed; the rest carry forward from the latest row.
    Takes effect immediately for all knowledge bases with no per-knowledge override.
    """
    try:
        if all(v is None for v in [
            body.enrichment_model, body.enrichment_provider, body.enrichment_temperature,
            body.enrichment_fallback_model, body.enrichment_fallback_provider,
        ]):
            raise HTTPException(status_code=400, detail="At least one field must be provided")

        updated = await update_enrichment_default_config(
            enrichment_model=body.enrichment_model,
            enrichment_provider=body.enrichment_provider,
            enrichment_temperature=body.enrichment_temperature,
            enrichment_fallback_model=body.enrichment_fallback_model,
            enrichment_fallback_provider=body.enrichment_fallback_provider,
        )
        logger.info(f"Admin updated enrichment default config: {updated}")
        return EnrichmentDefaultConfigResponse(**updated)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating enrichment default config: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to update enrichment default config")
