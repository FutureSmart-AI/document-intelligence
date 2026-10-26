"""
API Key Management Router

Self-service generation/revocation/listing of per-user, optionally
per-knowledge-base, API keys — ported from fs-agent-dashboard's
api_key_management/router.py (see docs/plan/access-and-sharing/archive/design-api-keys.md).

Session-only: a caller must be logged in to manage their own keys. The
master API key is deliberately rejected here — it has no owning user_id to
scope keys to, and letting it mint per-user keys would let it impersonate
any user.

Endpoints:
- POST   /v1/api-keys           — generate a new key for the caller
- POST   /v1/api-keys/revoke    — revoke one of the caller's keys (by prefix)
- GET    /v1/api-keys           — list the caller's keys (raw value never returned)
"""

from fastapi import APIRouter, Depends, HTTPException

from utils.logger import logger
from utils.auth.dependencies import get_api_key_or_session
from utils.auth.auth_utils import extract_owner_id_from_session
from utils.pydantic_models_api_keys import (
    GenerateKeyRequest,
    GenerateKeyResponse,
    RevokeKeyRequest,
    ApiKeysResponse,
)
from utils.db.api_key_ops import generate_api_key, revoke_api_key, get_user_api_keys

router = APIRouter()


def _require_session_user_id(auth: dict) -> str:
    """API key management is self-service only — the master key has no owning
    user_id, so it's rejected here rather than being allowed to mint/revoke/list
    keys on behalf of an unspecified identity."""
    if auth.get("auth_type") != "session":
        raise HTTPException(status_code=401, detail="A logged-in session is required to manage API keys")
    user_id = extract_owner_id_from_session(auth["session_data"])
    if not user_id:
        raise HTTPException(status_code=401, detail="Could not resolve user identity")
    return user_id


@router.post("/v1/api-keys", response_model=GenerateKeyResponse)
async def create_api_key(request: GenerateKeyRequest, auth: dict = Depends(get_api_key_or_session)):
    """Generate a new API key for the caller. The raw key is returned exactly
    once here and cannot be recovered afterwards — only its prefix is stored
    for display."""
    user_id = _require_session_user_id(auth)
    try:
        result = await generate_api_key(user_id=user_id, knowledge_id=request.knowledge_id)
        return GenerateKeyResponse(**result)
    except Exception as e:
        logger.error(f"Error generating API key for user_id={user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to generate API key")


@router.post("/v1/api-keys/revoke")
async def revoke_own_api_key(request: RevokeKeyRequest, auth: dict = Depends(get_api_key_or_session)):
    """Revoke one of the caller's own keys, identified by its prefix."""
    user_id = _require_session_user_id(auth)
    revoked = await revoke_api_key(user_id=user_id, key_prefix=request.key_prefix)
    if not revoked:
        raise HTTPException(status_code=404, detail="No active API key found with that prefix")
    return {"revoked": True}


@router.get("/v1/api-keys", response_model=ApiKeysResponse)
async def list_api_keys(auth: dict = Depends(get_api_key_or_session)):
    """List the caller's API keys. Raw key values are never returned — only
    each key's prefix, status, and timestamps."""
    user_id = _require_session_user_id(auth)
    keys = await get_user_api_keys(user_id=user_id)
    return ApiKeysResponse(api_keys=keys)
