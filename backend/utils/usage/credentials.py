"""Encrypted BYOK credential management, ported from fs-agent-usage-limits'
utils/credentials_management.py. Scoped by owner_id (see utils/models/usage_credentials.py
for the rationale).

The CRUD functions take owner_id directly — the routes are
`/v1/owners/{owner_id}/usage/credentials`. The one exception is
``get_credential_for_knowledge``, kept for in-process chat/RAG/embedding call sites
that hold only a knowledge_id; it resolves the owner and then defers to
``get_credential_for_owner``.
"""

import os
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from typing import Tuple, Optional, List
from datetime import datetime

from utils.models.usage_credentials import OwnerCredential
from utils.pydantic_models_usage_credentials import (
    CredentialAddRequest, CredentialData, CredentialAddResponse, CredentialListResponse,
    CredentialDeleteResponse, InternalCredentialListResponse, InternalCredentialItem,
)
from utils.usage.encryption import encrypt_api_key, mask_api_key, decrypt_api_key
from utils.usage.credential_health import schedule_revalidation_if_stale
from utils.logger import logger

# Canonical provider lists — single source of truth. routers/usage_credentials.py's
# provider-list endpoint and utils/usage/limit_checking.py's credentials-configured
# limit both read these, so the two can never disagree on how many providers exist.
LLM_PROVIDERS = ["openai", "anthropic", "groq", "google", "deepseek", "minimax"]
DOC_WEB_PROVIDERS = ["llamaparse", "firecrawl"]


async def resolve_owner_id_for_knowledge(knowledge_id: str) -> Optional[str]:
    """Public alias of ``_resolve_owner_id`` for callers outside this module.

    chat/byok_fallback.py needs it: a call site that only holds ``knowledge_id``
    still has to name the owner whose credential is being demoted.
    """
    return await _resolve_owner_id(knowledge_id)


async def _resolve_owner_id(knowledge_id: str) -> Optional[str]:
    try:
        from utils.db.knowledge_ops import get_knowledge_service

        service = await get_knowledge_service(knowledge_id)
        return service.get("owner_id") if service else None
    except Exception as e:
        logger.debug(f"Could not resolve owner_id for knowledge_id={knowledge_id}: {e}")
        return None


async def add_or_update_credential(
    session: AsyncSession, owner_id: str, request: CredentialAddRequest, validate: bool = True
) -> Tuple[CredentialAddResponse, bool]:
    """Encrypt and store (or replace) one provider's BYOK credential for this account.
    The credential applies to every knowledge base the owner has.

    **The key is live-checked against the provider before it is stored** (pass
    ``validate=False`` to skip, e.g. for a restore or a provider we cannot probe).
    A key that fails authentication or has no balance left is rejected with
    ``ValueError``, which the router turns into a 400 — storing a key we already
    know is dead would mean every subsequent request pays a provider round trip to
    rediscover that, and the owner would not find out until their chat broke.

    Rejection is narrow on purpose: only a *terminal* verdict (bad credentials,
    spent balance) refuses the write. A rate-limited or unreachable provider, or a
    provider ``check_provider_status`` has no probe for (deepseek, minimax), stores
    the key unvalidated — we cannot prove it bad, and refusing on "cannot tell"
    would make the endpoint fail whenever a provider has a bad day.
    """
    from utils.usage.credential_health import check_key_liveness, is_terminal

    validation_error: Optional[str] = None
    if validate:
        validation_error = await check_key_liveness(request.provider, request.api_key)
        if is_terminal(validation_error):
            logger.warning(
                f"Rejecting BYOK key for owner_id={owner_id} provider={request.provider}: {validation_error}"
            )
            raise ValueError(
                f"The {request.provider} API key was rejected by the provider "
                f"({validation_error}). Check the key and try again."
            )

    try:
        encrypted_key, key_hash = encrypt_api_key(request.api_key)
        masked_key = mask_api_key(request.api_key)
        # Only stamp "provably worked" when we actually proved it. An unprobeable
        # provider leaves this NULL so the re-validation sweep picks it up later.
        validated_at = datetime.utcnow() if (validate and validation_error is None) else None

        result = await session.execute(
            select(OwnerCredential).where(OwnerCredential.owner_id == owner_id, OwnerCredential.provider == request.provider)
        )
        existing = result.scalar_one_or_none()
        is_new = existing is None

        if existing:
            existing.encrypted_key = encrypted_key
            existing.key_hash = key_hash
            existing.key_mask = masked_key
            # Replacing the key clears the old key's failure history — otherwise a
            # freshly pasted working key would keep showing the dead key's error.
            existing.is_valid = True
            existing.last_error_type = None
            existing.last_error_at = None
            existing.last_validated_at = validated_at
            existing.updated_at = datetime.utcnow()
        else:
            session.add(
                OwnerCredential(
                    owner_id=owner_id, provider=request.provider,
                    encrypted_key=encrypted_key, key_hash=key_hash, key_mask=masked_key,
                    is_valid=True, last_validated_at=validated_at,
                    created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                )
            )

        await session.commit()
        response = CredentialAddResponse(
            status="success",
            data=CredentialData(
                provider=request.provider, mask=masked_key, is_valid=True,
                updated_at=datetime.utcnow().isoformat() + "Z",
                last_validated_at=_iso(validated_at),
            ),
        )
        return response, is_new
    except IntegrityError:
        await session.rollback()
        raise Exception("Database constraint violation")


def _iso(value: Optional[datetime]) -> Optional[str]:
    """UTC ISO8601 with the trailing Z, or None. Stored values are naive UTC."""
    return value.isoformat() + "Z" if value else None


async def list_credentials(session: AsyncSession, owner_id: str) -> CredentialListResponse:
    result = await session.execute(select(OwnerCredential).where(OwnerCredential.owner_id == owner_id).order_by(OwnerCredential.provider))
    credentials = result.scalars().all()
    return CredentialListResponse(
        data=[
            CredentialData(
                provider=c.provider, mask=c.key_mask, is_valid=c.is_valid,
                updated_at=c.updated_at.isoformat() + "Z",
                last_error_type=c.last_error_type,
                last_error_at=_iso(c.last_error_at),
                last_validated_at=_iso(c.last_validated_at),
            )
            for c in credentials
        ]
    )


async def delete_credential(session: AsyncSession, owner_id: str, provider: str) -> CredentialDeleteResponse:
    result = await session.execute(select(OwnerCredential).where(OwnerCredential.owner_id == owner_id, OwnerCredential.provider == provider))
    credential = result.scalar_one_or_none()
    if not credential:
        raise ValueError(f"Credential for provider '{provider}' not found")
    await session.delete(credential)
    await session.commit()
    return CredentialDeleteResponse(status="success", message=f"Credential for provider '{provider}' deleted successfully")


async def list_all_internal_plain_credentials(session: AsyncSession, provider: Optional[str] = None) -> InternalCredentialListResponse:
    """INTERNAL ONLY. Used by service-to-service callers (e.g. workers) that need
    the raw key to make provider calls on the owner's behalf."""
    stmt = select(OwnerCredential).where(OwnerCredential.provider == provider) if provider else select(OwnerCredential)
    stmt = stmt.order_by(OwnerCredential.owner_id)
    result = await session.execute(stmt)
    items: List[InternalCredentialItem] = []
    for cred in result.scalars().all():
        items.append(
            InternalCredentialItem(owner_id=cred.owner_id, provider=cred.provider, api_key=decrypt_api_key(cred.encrypted_key), is_valid=cred.is_valid)
        )
    return InternalCredentialListResponse(data=items)


async def get_credential_for_knowledge(knowledge_id: str, provider: str) -> Tuple[Optional[str], str]:
    """In-process replacement for utils/qdrant/owner_credentials.py's HTTP round
    trip. Resolves knowledge_id -> owner_id, then looks up that owner's BYOK
    credential for the provider. Returns (api_key, key_type): (key, "owner-key") if
    a valid credential exists, else (None, "platform-key")."""
    from utils.db.session import async_session

    if not knowledge_id:
        return None, "platform-key"
    try:
        owner_id = await _resolve_owner_id(knowledge_id)
        if not owner_id:
            if os.getenv("BYOK_PLATFORM_FALLBACK", "true").lower() in {"false", "0", "no", "off"}:
                raise ValueError("Cannot resolve the knowledge owner to select a provider key")
            return None, "platform-key"
        return await get_credential_for_owner(owner_id, provider)
    except Exception as e:
        logger.error(f"Error resolving credential for knowledge_id={knowledge_id}, provider={provider}: {e}")
        if os.getenv("BYOK_PLATFORM_FALLBACK", "true").lower() in {"false", "0", "no", "off"}:
            raise
        return None, "platform-key"


async def get_credential_for_owner(owner_id: str, provider: str) -> Tuple[Optional[str], str]:
    """Same as get_credential_for_knowledge, for a caller that already has owner_id
    in hand (e.g. workers, which resolves it once per task rather than per call site)."""
    from utils.db.session import async_session

    if not owner_id:
        return None, "platform-key"
    try:
        async with async_session() as session:
            result = await session.execute(
                select(OwnerCredential).where(OwnerCredential.owner_id == owner_id, OwnerCredential.provider == provider)
            )
            credential = result.scalar_one_or_none()
            if credential and credential.is_valid:
                # A key that has not been proven recently gets re-checked in the
                # background. This request still uses it — the point is to catch an
                # expired key before the owner's NEXT request, without making this
                # one wait on a live provider round trip. Read the timestamp inside
                # the session; the task itself runs after it closes.
                last_validated_at = credential.last_validated_at
                api_key = decrypt_api_key(credential.encrypted_key)
                schedule_revalidation_if_stale(owner_id, provider, last_validated_at)
                return api_key, "owner-key"
            # Either no key at all, or one the provider already rejected. Both mean
            # "use the platform key" — which is the fallback, no call site involved.
            if credential is not None:
                if os.getenv("BYOK_PLATFORM_FALLBACK", "true").lower() in {"false", "0", "no", "off"}:
                    raise ValueError(f"The configured {provider} key is invalid; replace or remove it")
                logger.info(
                    f"BYOK key for owner_id={owner_id}, provider={provider} is marked invalid "
                    f"({credential.last_error_type}) — using the platform key"
                )
            return None, "platform-key"
    except Exception as e:
        logger.error(f"Error resolving credential for owner_id={owner_id}, provider={provider}: {e}")
        if os.getenv("BYOK_PLATFORM_FALLBACK", "true").lower() in {"false", "0", "no", "off"}:
            raise
        return None, "platform-key"


async def get_byok_providers(owner_id: str) -> set:
    """The set of providers this owner has supplied a *valid* key for.

    Used by the ingest exemption (utils/usage/pipeline_providers.py): if every provider
    a pipeline will touch is in this set, the account is spending its own money and the
    platform's knowledge-item ceiling does not apply.

    Returns an empty set on any error rather than raising — the caller treats "no BYOK
    keys" as "not exempt", which is the safe direction (the limit still applies).
    """
    from utils.db.session import async_session

    if not owner_id:
        return set()
    try:
        async with async_session() as session:
            result = await session.execute(
                select(OwnerCredential.provider).where(
                    OwnerCredential.owner_id == owner_id,
                    OwnerCredential.is_valid == True,  # noqa: E712
                )
            )
            return {row[0] for row in result.all()}
    except Exception as e:
        logger.error(f"Error listing BYOK providers for owner_id={owner_id}: {e}")
        return set()
