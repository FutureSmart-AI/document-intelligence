"""BYOK credential management, ported from fs-agent-usage-limits'
routers/credentials_router.py, rewritten to `/v1/owners/{owner_id}/usage/credentials`.

A BYOK key belongs to the account: an owner supplies one key per provider and it is
used across every knowledge base they have (see utils/models/usage_credentials.py,
migration 039). So this surface is owner-scoped, and only the account owner (or a
master-key service caller) may touch it — an invited editor/viewer of one knowledge
base has no business reading or replacing the account's provider keys.

The internal (plaintext-returning) endpoints require the master API key — mirrored
from the source service's require_admin gate — since they exist only for
service-to-service calls (e.g. resolving a BYOK key before an LLM call), never for a
dashboard/UI caller.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Path
from typing import Optional

from utils.auth.dependencies import require_owner, get_api_key_or_session
from utils.pydantic_models_usage_credentials import (
    CredentialAddRequest, CredentialAddResponse, CredentialListResponse, CredentialDeleteResponse,
    ProviderListResponse, InternalCredentialResponse, InternalCredentialListResponse,
    CredentialFailureReport, CredentialFailureReportResponse,
)
from utils.usage.credentials import (
    add_or_update_credential, list_credentials, delete_credential,
    get_credential_for_owner,
    list_all_internal_plain_credentials,
    LLM_PROVIDERS as _LLM_PROVIDERS, DOC_WEB_PROVIDERS as _DOC_WEB_PROVIDERS,
)
from utils.usage.credential_health import mark_credential_failed
from utils.db.session import async_session
from utils.logger import logger

router = APIRouter()


def _require_master_key(auth: dict) -> None:
    if auth.get("auth_type") != "master_api_key":
        raise HTTPException(status_code=403, detail="Master API key required for internal credential access")


@router.get("/v1/owners/{owner_id}/usage/credentials/providers", response_model=ProviderListResponse, tags=["Usage Credentials"])
async def api_list_supported_providers(owner_id: str, _: str = Depends(require_owner())) -> ProviderListResponse:
    return ProviderListResponse(llm_providers=_LLM_PROVIDERS, document_and_web_providers=_DOC_WEB_PROVIDERS)


@router.post("/v1/owners/{owner_id}/usage/credentials", response_model=CredentialAddResponse, tags=["Usage Credentials"])
async def api_add_or_update_credential(
    owner_id: str, request: CredentialAddRequest, _: str = Depends(require_owner())
) -> CredentialAddResponse:
    """Encrypt and store (or replace) a BYOK provider credential for this account —
    it applies to every knowledge base the owner has."""
    logger.info(f"POST /v1/owners/{owner_id}/usage/credentials - provider={request.provider}")
    try:
        async with async_session() as session:
            response, _is_new = await add_or_update_credential(session, owner_id, request)
            return response
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"status": "error", "code": "INVALID_API_KEY", "message": str(e)})
    except Exception as e:
        logger.error(f"POST /v1/owners/{owner_id}/usage/credentials - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"status": "error", "code": "INTERNAL_ERROR", "message": str(e)})


@router.get("/v1/owners/{owner_id}/usage/credentials", response_model=CredentialListResponse, tags=["Usage Credentials"])
async def api_list_credentials(owner_id: str, _: str = Depends(require_owner())) -> CredentialListResponse:
    """List this account's configured providers (masked keys only)."""
    async with async_session() as session:
        return await list_credentials(session, owner_id)


@router.delete("/v1/owners/{owner_id}/usage/credentials/{provider}", response_model=CredentialDeleteResponse, tags=["Usage Credentials"])
async def api_delete_credential(owner_id: str, provider: str = Path(...), _: str = Depends(require_owner())) -> CredentialDeleteResponse:
    try:
        async with async_session() as session:
            return await delete_credential(session, owner_id, provider)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"status": "error", "code": "CREDENTIAL_NOT_FOUND", "message": str(e)})


@router.post(
    "/v1/owners/{owner_id}/usage/credentials/{provider}/report-failure",
    response_model=CredentialFailureReportResponse, tags=["Usage Credentials"],
)
async def api_report_credential_failure(
    owner_id: str, provider: str, request: CredentialFailureReport,
    auth: dict = Depends(get_api_key_or_session),
) -> CredentialFailureReportResponse:
    """INTERNAL ONLY — a service-to-service caller (fs-agent-workers) reports that
    a provider rejected this owner's key for `provider`.

    Master-key only, for the same reason the plaintext /internal endpoint below is:
    this demotes an account's credential, which no dashboard/UI caller should be
    able to trigger directly (that would let anyone who can reach the API 403
    someone else's key out of existence by lying about a failure).

    fs-agent-workers is the reason this exists at all: it resolves BYOK keys over
    HTTP (celery_utils/shared/credentials_utils.py) and has no access to the
    owner_provider_keys table, so it cannot call mark_credential_failed directly
    the way this repo's own chat path does (chat/byok_fallback.py).
    """
    _require_master_key(auth)
    demoted = await mark_credential_failed(owner_id, provider, request.error_type, request.message)
    return CredentialFailureReportResponse(demoted=demoted)


@router.get("/v1/owners/{owner_id}/usage/credentials/{provider}/internal", response_model=InternalCredentialResponse, tags=["Usage Credentials"])
async def api_get_internal_plain_credential(
    owner_id: str, provider: str = Path(...), auth: dict = Depends(get_api_key_or_session)
) -> InternalCredentialResponse:
    """INTERNAL ONLY — plaintext key, master-key callers only.

    Used by service-to-service callers (e.g. fs-agent-workers) that need the raw key
    to make a provider call on the owner's behalf.
    """
    _require_master_key(auth)
    api_key, _key_type = await get_credential_for_owner(owner_id, provider)
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"status": "error", "code": "CREDENTIAL_NOT_FOUND", "message": f"Credential for provider '{provider}' not found"})
    return InternalCredentialResponse(provider=provider, api_key=api_key, is_valid=True)


@router.get("/v1/usage/credentials/internal", response_model=InternalCredentialListResponse, tags=["Usage Credentials"])
async def api_list_all_internal_plain_credentials(provider: Optional[str] = None, auth: dict = Depends(get_api_key_or_session)) -> InternalCredentialListResponse:
    """INTERNAL ONLY — plaintext keys across all owners, master-key callers only.

    Note: unlike every other route in this file, this one has no owner in the path
    (it lists across every owner), so it does not go through require_owner — auth is
    master-key-only via get_api_key_or_session.
    """
    _require_master_key(auth)
    async with async_session() as session:
        return await list_all_internal_plain_credentials(session, provider)
