"""
Admin Config Router

Internal-only endpoints for managing global enrichment defaults.
Master x-api-key only (require_internal_service). Until the 2026-10-23 security
audit these had no auth at all and were reachable through the public API gateway.

Endpoints:
- GET   /v1/admin/enrichment-config  — read current global default (latest versioned row)
- PATCH /v1/admin/enrichment-config  — insert new versioned row with updated fields
"""

from fastapi import Depends, HTTPException, APIRouter
from pydantic import BaseModel
from typing import Optional
import traceback

from utils.logger import logger
from utils.auth.dependencies import require_internal_service
from utils.enrichment import model_registry
from utils.db.knowledge_setting_ops import (
    get_enrichment_default_config,
    update_enrichment_default_config,
)

router = APIRouter(dependencies=[Depends(require_internal_service)])


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


async def _validated_models(body: PatchEnrichmentDefaultConfigBody):
    """The (model, provider, fallback model, fallback provider) to store, each pair
    checked against the enrichment registry (utils/enrichment/model_registry.py).

    The row is read by every worker on every document, so a name the worker cannot
    run is refused here with 422 instead of failing all extraction. A model sent
    without its provider gets the registry's provider rather than inheriting the
    previous row's (``gpt-5.6-luna-low`` after a Gemini row must not be stored as
    google). A provider sent alone is checked against the model it will pair with.
    """
    current = None
    if (body.enrichment_provider is not None and body.enrichment_model is None) or (
        body.enrichment_fallback_provider is not None and body.enrichment_fallback_model is None
    ):
        current = await get_enrichment_default_config() or {}

    def _pair(model, provider, current_key):
        if model is not None:
            return model_registry.validate(model, provider)
        if provider is not None:
            model_registry.validate((current or {}).get(current_key) or "", provider)
        return None, provider

    try:
        model, provider = _pair(body.enrichment_model, body.enrichment_provider, "enrichment_model")
        fb_model, fb_provider = _pair(
            body.enrichment_fallback_model, body.enrichment_fallback_provider, "enrichment_fallback_model"
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return model, provider, fb_model, fb_provider


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

        model, provider, fb_model, fb_provider = await _validated_models(body)
        updated = await update_enrichment_default_config(
            enrichment_model=model,
            enrichment_provider=provider,
            enrichment_temperature=body.enrichment_temperature,
            enrichment_fallback_model=fb_model,
            enrichment_fallback_provider=fb_provider,
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
