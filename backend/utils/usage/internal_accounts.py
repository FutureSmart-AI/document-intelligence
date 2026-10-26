"""Internal accounts: FutureSmart and AI Demos staff are not on the trial.

A person signed in with a VERIFIED email on one of ``INTERNAL_ACCOUNT_DOMAINS``
(``futuresmart.ai`` and ``aidemos.com``) has no trial ceilings at all — no daily or
monthly message allowance, no collection, document or token cap — so testing the
hosted product never needs a manual reset. It is the same policy Database Agent
applies (fs-agent-nl2sql ``chatbot_management/db_utils/demo_allowance.py``).

How an account becomes internal, and why it is recorded rather than worked out per
request:

- **Only a signed-in session can establish it.** The auth service's ``/user/me``
  claims must say ``email_verified: true`` for the session's own email, and the part
  after the last ``@`` must equal an internal domain exactly — a subdomain or a
  look-alike (``futuresmart.ai.example.com``) never qualifies. A request body, an
  ``owner_id`` parameter, the master key or a generated API key can never make an
  account internal.
- **Once established, it is a property of the account.** Limits are charged to the
  collection's owner, and many of an owner's operations do not arrive with that
  owner's session — their own API keys, the workers' ingestion calls, scripts using
  the master key on the owner's behalf. So a verified sign-in writes one row to
  ``internal_accounts`` (migration 054) and ``get_owner_limits`` reads it. An
  account named ``someone@futuresmart.ai`` that has never signed in with a verified
  email stays on its configured limits — the platform account that owns the sample
  collections is one of these, deliberately.

Nothing stops being recorded. Every message, token, latency and cost event is still
written against the owner exactly as before; ``internal_spend`` reports the month's
usage for internal accounts separately so internal spend can be watched.

Fails closed: if the row cannot be read, the account keeps its normal limits.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from utils.logger import logger

# owner_id -> (is_internal, read_at). Short-lived: a revoked row takes effect within
# the TTL on every task without a restart.
_CACHE_TTL_SECONDS = 60.0
_internal_cache: Dict[str, tuple] = {}

# owner_id -> last time this process wrote its verification. A verified session
# arrives on almost every request; one write an hour per account is plenty.
_RECORD_INTERVAL_SECONDS = 3600.0
_recorded_at: Dict[str, float] = {}


def internal_domains() -> frozenset:
    domains = os.getenv("INTERNAL_ACCOUNT_DOMAINS", "futuresmart.ai,aidemos.com")
    return frozenset(d.strip().lower() for d in domains.split(",") if d.strip())


def _claim_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def is_internal_address(email: Optional[str]) -> bool:
    """True when ``email`` has a non-empty local part and its domain is exactly internal."""
    local, _, domain = (email or "").strip().lower().rpartition("@")
    return bool(local) and domain in internal_domains()


def verified_internal_email(session_data: Optional[Dict[str, Any]]) -> Optional[str]:
    """The caller's lower-cased email when this ``/user/me`` result proves an internal
    account, else None.

    Requires ``all_claims`` (fetch with ``include_all_claims=True``): the claims' own
    email must be the session's email and ``email_verified`` must be true.
    """
    if not session_data:
        return None
    email = (session_data.get("email") or "").strip().lower()
    claims = session_data.get("all_claims") or {}
    claim_email = (claims.get("email") or "").strip().lower()
    if not email or claim_email != email or not _claim_true(claims.get("email_verified")):
        return None
    return email if is_internal_address(email) else None


async def _write_verification(owner_id: str) -> None:
    from utils.db.session import async_session

    async with async_session() as session:
        async with session.begin():
            await session.execute(
                text(
                    "INSERT INTO internal_accounts (owner_id, first_verified_at, last_verified_at)"
                    " VALUES (:owner_id, now(), now())"
                    " ON CONFLICT (owner_id) DO UPDATE SET last_verified_at = now()"
                ),
                {"owner_id": owner_id},
            )


async def record_verified_session(session_data: Optional[Dict[str, Any]]) -> None:
    """Record the account as internal when this session proves it. Never raises: a
    failed write only means the account keeps its limits until the next request."""
    owner_id = verified_internal_email(session_data)
    if not owner_id:
        return
    now = time.monotonic()
    last = _recorded_at.get(owner_id)
    if last is not None and now - last < _RECORD_INTERVAL_SECONDS:
        return
    try:
        await _write_verification(owner_id)
        _recorded_at[owner_id] = now
        _internal_cache[owner_id] = (True, now)
        logger.info(f"[internal_accounts] verified internal account {owner_id}")
    except Exception as e:
        logger.warning(f"[internal_accounts] could not record verification for {owner_id}: {e}")


async def _read_verification(owner_id: str) -> bool:
    from utils.db.session import async_session

    async with async_session() as session:
        row = (
            await session.execute(
                text("SELECT 1 FROM internal_accounts WHERE owner_id = :owner_id"),
                {"owner_id": owner_id},
            )
        ).first()
    return row is not None


async def is_internal_owner(owner_id: Optional[str]) -> bool:
    """Whether this account is a verified internal one. False on any error."""
    owner = (owner_id or "").strip().lower()
    if not is_internal_address(owner):
        return False
    cached = _internal_cache.get(owner)
    now = time.monotonic()
    if cached is not None and now - cached[1] < _CACHE_TTL_SECONDS:
        return cached[0]
    try:
        internal = await _read_verification(owner)
    except Exception as e:
        logger.error(f"[internal_accounts] lookup failed for {owner}; applying normal limits: {e}")
        return False
    _internal_cache[owner] = (internal, now)
    return internal


async def internal_spend() -> List[Dict[str, Any]]:
    """This calendar month's usage for every internal account — messages, LLM tokens
    and estimated cost, from the same usage events the Usage screen reads."""
    from utils.db.session import async_session
    from utils.usage.limit_checking import _month_bounds, get_current_usage

    month_start, month_end = _month_bounds()
    async with async_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT owner_id, first_verified_at, last_verified_at"
                    " FROM internal_accounts ORDER BY owner_id"
                )
            )
        ).all()
        report = []
        for owner_id, first_verified_at, last_verified_at in rows:
            usage = await get_current_usage(session, owner_id, month_start, month_end, include_cost=True)
            report.append({
                "owner_id": owner_id,
                "first_verified_at": first_verified_at.isoformat() if first_verified_at else None,
                "last_verified_at": last_verified_at.isoformat() if last_verified_at else None,
                "messages_this_month": usage.messages_this_month,
                "messages_today": usage.messages_today,
                "conversations_this_month": usage.conversations_this_month,
                "llm_tokens_this_month": usage.llm_tokens_this_month,
                "cost_usd_this_month": usage.cost_usd_this_month,
            })
    return report
