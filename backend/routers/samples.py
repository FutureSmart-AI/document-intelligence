"""Sample collections — the shared, read-only collections every account can try.

``GET /v1/samples`` is how a signed-in account finds them (they are never in its own
collection list) and how much of the sample allowance it has left.
``PUT /v1/{knowledge_id}/sample`` marks a provisioned collection as the live copy
of a sample, and is master-key only. ``GET /v1/samples/usage`` is the adoption
record. The access rules themselves are in utils/auth/sample_access.py.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from utils.auth import sample_access
from utils.auth.dependencies import get_api_key_or_session, require_internal_service
from utils.logger import logger

router = APIRouter()


class SampleCollection(BaseModel):
    knowledge_id: str
    sample_key: str = Field(..., description='Which sample this is: "resumes", "invoices".')
    collection_name: Optional[str] = None
    service_description: Optional[str] = None
    document_count: int = 0


class SampleAllowance(BaseModel):
    audience: str = Field(..., description='"public", or "internal" for a verified FutureSmart / AI Demos account (unlimited).')
    unlimited: bool
    questions_today: int
    questions_total: int
    daily_limit: Optional[int] = None
    total_limit: Optional[int] = None


class SamplesResponse(BaseModel):
    samples: List[SampleCollection]
    allowance: Optional[SampleAllowance] = Field(
        None, description="The caller's sample question allowance. Absent for the master key."
    )


class SetSampleRequest(BaseModel):
    sample_key: Optional[str] = Field(
        None, description="Lowercase slug, e.g. \"resumes\". null removes the collection from the samples."
    )


class SetSampleResponse(BaseModel):
    knowledge_id: str
    sample_key: Optional[str] = None


class SampleAdoptionRow(BaseModel):
    knowledge_id: str
    sample_key: Optional[str] = None
    audience: str
    day: str
    questions: int
    visitors: int


class SampleAdoptionResponse(BaseModel):
    days: int
    rows: List[SampleAdoptionRow]


@router.get("/v1/samples", response_model=SamplesResponse)
async def list_sample_collections(auth: dict = Depends(get_api_key_or_session)):
    """The sample collections, and how many sample questions the caller has left."""
    try:
        samples = await sample_access.list_samples()
        allowance = None
        if auth.get("auth_type") == "session":
            if (auth["session_data"].get("email") or "").strip():
                allowance = await sample_access.sample_allowance_for(auth["session_data"])
        return SamplesResponse(samples=samples, allowance=allowance)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing sample collections: {e}")
        raise HTTPException(status_code=500, detail="Failed to list sample collections")


@router.get("/v1/samples/usage", response_model=SampleAdoptionResponse)
async def sample_usage(
    days: int = Query(30, ge=1, le=365),
    _: None = Depends(require_internal_service),
):
    """Questions and distinct visitors per sample per day (master key only)."""
    rows = await sample_access.sample_adoption(days)
    return SampleAdoptionResponse(days=days, rows=rows)


@router.put("/v1/{knowledge_id}/sample", response_model=SetSampleResponse)
async def set_sample(
    body: SetSampleRequest,
    knowledge_id: str = Path(..., description="Knowledge ID"),
    _: None = Depends(require_internal_service),
):
    """Mark a collection as the live copy of a sample, or (null) stop offering it.

    Master key only: whatever is marked here becomes readable by every signed-in
    account.
    """
    sample_key = body.sample_key.strip().lower() if body.sample_key else None
    if sample_key is not None and not sample_access.is_valid_sample_key(sample_key):
        raise HTTPException(status_code=422, detail="sample_key must be a lowercase slug such as \"resumes\"")
    try:
        updated = await sample_access.set_sample_key(knowledge_id, sample_key)
    except Exception as e:
        # The partial unique index: another live collection is already this sample.
        logger.error(f"Error setting sample_key={sample_key} on {knowledge_id}: {e}")
        raise HTTPException(status_code=409, detail=f"Another collection is already the {sample_key!r} sample")
    if not updated:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    logger.info(f"Sample marker set: knowledge={knowledge_id} sample_key={sample_key}")
    return SetSampleResponse(knowledge_id=knowledge_id, sample_key=sample_key)
