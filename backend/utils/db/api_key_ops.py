"""DB ops for per-user/per-knowledge-base API keys (api_keys table).

Ported from fs-agent-dashboard's api_key_management/db_utils.py, hardened per
docs/plan/access-and-sharing/archive/design-api-keys.md decisions: the raw key is never
persisted. `hash_api_key()` is HMAC-SHA256 keyed on API_KEY_HASH_SECRET (a
pepper) rather than a bare hash, so a leaked DB row alone can't be brute-
forced offline without also having that secret.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlmodel import select, and_

from utils.db.session import async_session
from utils.db.retry_utils import retry_db_connection
from utils.logger import logger
from utils.models.db_tables import ApiKey
from utils.setup_env import API_KEY_HASH_SECRET
from utils.tracing import track_time

_KEY_PREFIX = "sk-"


def hash_api_key(raw_key: str) -> str:
    """HMAC-SHA256 of the raw key, keyed on the retained generated-key pepper. Deterministic
    (same raw key -> same hash) so it can be used as an equality-lookup column."""
    return hmac.new(
        API_KEY_HASH_SECRET.encode("utf-8"), raw_key.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _generate_raw_key() -> str:
    return f"{_KEY_PREFIX}{secrets.token_urlsafe(32)}"


def _to_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


@track_time("DB: Generate API Key")
@retry_db_connection(max_retries=3)
async def generate_api_key(user_id: str, knowledge_id: Optional[str] = None) -> Dict[str, Any]:
    """Issue a new active API key for user_id (+ optional knowledge_id).

    Unlike upstream (which reused an existing active key), this always issues
    a new one — the raw value can't be recovered from a prior row anyway
    since only its hash is stored.
    """
    raw_key = _generate_raw_key()
    key_hash = hash_api_key(raw_key)
    key_prefix = raw_key[: len(_KEY_PREFIX) + 5]

    async with async_session() as session:
        row = ApiKey(
            user_id=user_id,
            knowledge_id=knowledge_id,
            key_prefix=key_prefix,
            key_hash=key_hash,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

    logger.info(f"Generated API key for user_id={user_id} knowledge_id={knowledge_id} prefix={key_prefix}")
    return {
        "api_key": raw_key,
        "key_prefix": key_prefix,
        "created_at": _to_iso(row.created_at),
        "user_id": user_id,
    }


@track_time("DB: Revoke API Key")
@retry_db_connection(max_retries=3)
async def revoke_api_key(user_id: str, key_prefix: str) -> bool:
    """Revoke the caller's own active key identified by its prefix.

    Scoped to user_id so one user can't revoke another user's key by prefix.
    """
    async with async_session() as session:
        statement = select(ApiKey).where(
            and_(ApiKey.user_id == user_id, ApiKey.key_prefix == key_prefix, ApiKey.is_active == True)
        )
        result = await session.execute(statement)
        row = result.scalar_one_or_none()

        if not row:
            logger.warning(f"Revoke failed — no active key for user_id={user_id} prefix={key_prefix}")
            return False

        row.is_active = False
        row.revoked_at = datetime.now(timezone.utc)
        await session.commit()

    logger.info(f"Revoked API key for user_id={user_id} prefix={key_prefix}")
    return True


@track_time("DB: List API Keys")
@retry_db_connection(max_retries=3)
async def get_user_api_keys(user_id: str, knowledge_id: Optional[str] = None) -> List[Dict[str, Any]]:
    async with async_session() as session:
        statement = select(ApiKey).where(ApiKey.user_id == user_id)
        if knowledge_id:
            statement = statement.where(ApiKey.knowledge_id == knowledge_id)
        statement = statement.order_by(ApiKey.created_at.desc())
        result = await session.execute(statement)
        rows = result.scalars().all()

    logger.info(f"Retrieved {len(rows)} API keys for user_id={user_id} knowledge_id={knowledge_id}")
    return [
        {
            "key_prefix": row.key_prefix,
            "knowledge_id": row.knowledge_id,
            "is_active": row.is_active,
            "created_at": _to_iso(row.created_at),
            "revoked_at": _to_iso(row.revoked_at),
        }
        for row in rows
    ]


@track_time("DB: Validate API Key")
@retry_db_connection(max_retries=3)
async def validate_api_key(raw_key: str) -> Optional[Dict[str, Optional[str]]]:
    """Resolve an incoming x-api-key header to its owning identity, or None
    if unknown/inactive. Used by the auth dependency (utils/auth/dependencies.py)."""
    key_hash = hash_api_key(raw_key)
    async with async_session() as session:
        statement = select(ApiKey.user_id, ApiKey.knowledge_id).where(
            and_(ApiKey.key_hash == key_hash, ApiKey.is_active == True)
        )
        result = await session.execute(statement)
        row = result.first()

    if not row:
        return None
    return {"user_id": row[0], "knowledge_id": row[1]}
