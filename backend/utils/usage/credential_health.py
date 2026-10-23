"""Is this BYOK key still alive, and may the platform key cover for it?

Three jobs, all of which used to be missing entirely — a stored key was assumed
good forever, and a request that hit a dead one simply failed:

1. **Classify** a provider exception (``classify_provider_error``). Every provider
   SDK raises its own exception type, so this reads the shapes they have in common
   (an HTTP status attribute, then the message text) rather than importing six SDKs.
2. **Demote** a key the provider rejected (``mark_credential_failed``), so
   ``get_credential_for_owner`` stops handing it out. That function already skips
   ``is_valid = False`` rows, which is why demotion alone is enough to make every
   subsequent request use the platform key — no call site has to know.
3. **Decide whether the platform may pay** (``platform_fallback_allowed``). This is
   the policy half, and it is deliberately *not* unconditional: covering an owner
   whose quota is already spent would turn a broken key into free unlimited usage.

## The one distinction that matters

`auth_error` and `low_balance` are terminal for the key: nothing about the next
request will make a revoked key work or a spent balance refill. A `rate_limit` is
the opposite — it clears on its own, and demoting on it would hand a *busy* owner's
traffic to the platform key permanently, which is the exact opposite of the
intent. So only the terminal kinds demote. Anything unrecognised is treated as
transient (a network blip, a provider outage) and demotes nothing: a false negative
here costs one failed request, while a false positive silently moves a paying
customer onto our bill.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Set, Tuple

from sqlalchemy import select

from utils.logger import logger
from utils.models.usage_credentials import OwnerCredential

# Error kinds that mean "this key cannot be used again until the owner changes
# something". Only these demote a credential — see the module docstring.
TERMINAL_ERROR_TYPES = frozenset({"auth_error", "low_balance"})

# Substrings that identify an authentication failure in a provider's error text.
# Matched case-insensitively against str(exc). Kept as text matching because the
# alternative is importing openai/anthropic/groq/google/deepseek SDK exception
# classes here, which would make this module fail to import whenever any one of
# those optional packages is absent (they are lazily imported by design — see
# chat/llm_factory.py).
_AUTH_PATTERNS = (
    "invalid_api_key",
    "invalid api key",
    "incorrect api key",
    "invalid x-api-key",
    "api key not valid",
    "api_key_invalid",
    "authenticationerror",
    "authentication_error",
    "authentication failed",
    "unauthorized",
    "permission_denied",
    "permissiondenied",
    "unauthenticated",
    "no auth credentials found",
)

# Spent balance / exhausted quota on the OWNER's provider account. Distinct from
# our own usage limits, and distinct from a rate limit.
_BALANCE_PATTERNS = (
    "insufficient_quota",
    "insufficient quota",
    "insufficient balance",
    "billing_hard_limit_reached",
    "exceeded your current quota",
    "credit balance is too low",
    "resource_exhausted",
    "payment_required",
)

_RATE_LIMIT_PATTERNS = (
    "rate_limit",
    "rate limit",
    "too many requests",
    "429",
)


def _http_status(exc: BaseException) -> Optional[int]:
    """Best-effort HTTP status from a provider exception.

    The OpenAI-family SDKs (openai, groq, deepseek, and MiniMax via ChatOpenAI)
    expose ``status_code``; some wrappers only carry a ``response`` object; a few
    use ``code``. Checked in that order, and any attribute access that misbehaves
    is ignored rather than allowed to mask the original error.
    """
    for attr in ("status_code", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    return None


def classify_provider_error(exc: BaseException) -> Optional[str]:
    """Name the failure kind, or ``None`` when it is not a credential problem.

    Returns one of ``auth_error``, ``low_balance``, ``rate_limit``, or ``None``.
    ``None`` covers everything we should not act on: timeouts, connection resets,
    provider 5xx, model-not-found, context-length errors, and anything unfamiliar.

    Status codes are checked before text because they are unambiguous, with one
    exception: a 429 may be either a rate limit or a spent balance (OpenAI uses 429
    with ``code: insufficient_quota`` for the latter), so the text decides which.
    """
    text = f"{type(exc).__name__} {exc}".lower()
    status = _http_status(exc)

    # A spent balance can arrive as 429 or 402, so test it before the rate-limit
    # branch — otherwise every insufficient_quota would read as transient and the
    # key would never be demoted.
    if any(p in text for p in _BALANCE_PATTERNS) or status == 402:
        return "low_balance"

    if status in (401, 403):
        return "auth_error"

    if status == 429 or any(p in text for p in _RATE_LIMIT_PATTERNS):
        return "rate_limit"

    if any(p in text for p in _AUTH_PATTERNS):
        return "auth_error"

    return None


def is_terminal(error_type: Optional[str]) -> bool:
    """True when this failure kind means the key itself is unusable."""
    return error_type in TERMINAL_ERROR_TYPES


async def mark_credential_failed(owner_id: str, provider: str, error_type: str, message: str = "") -> bool:
    """Record a provider rejection against this credential.

    A terminal kind also flips ``is_valid`` to False, which is what makes every
    later request fall back to the platform key. A non-terminal kind records the
    reason and timestamp but leaves the key in service.

    Returns True when the credential was demoted. Never raises — this runs on an
    error path, and failing to record a failure must not replace the original
    error the caller is already handling with a second, less useful one.
    """
    from utils.db.session import async_session

    if not owner_id or not provider:
        return False

    demote = is_terminal(error_type)
    try:
        async with async_session() as session:
            result = await session.execute(
                select(OwnerCredential).where(
                    OwnerCredential.owner_id == owner_id,
                    OwnerCredential.provider == provider,
                )
            )
            credential = result.scalar_one_or_none()
            if credential is None:
                logger.warning(
                    "[credential_health] no stored credential to demote for owner_id=%s provider=%s",
                    owner_id, provider,
                )
                return False

            credential.last_error_type = error_type
            credential.last_error_at = datetime.utcnow()
            if demote:
                credential.is_valid = False
            credential.updated_at = datetime.utcnow()
            await session.commit()

        if demote:
            logger.error(
                "[credential_health] BYOK key DEMOTED owner_id=%s provider=%s reason=%s: %s "
                "— later requests will use the platform key until the owner replaces it",
                owner_id, provider, error_type, message,
            )
        else:
            logger.warning(
                "[credential_health] BYOK key failed but kept in service owner_id=%s "
                "provider=%s reason=%s: %s",
                owner_id, provider, error_type, message,
            )
        return demote
    except Exception as e:
        logger.error(
            "[credential_health] could not record credential failure for owner_id=%s provider=%s: %s",
            owner_id, provider, e, exc_info=True,
        )
        return False


async def mark_credential_validated(owner_id: str, provider: str) -> None:
    """Stamp ``last_validated_at`` and clear a previous failure.

    Called after a key provably works — a successful save-time check, or a
    successful re-validation. Clearing ``last_error_type`` matters: a key that
    failed on Monday and was replaced on Tuesday should not still show Monday's
    error in the dashboard.
    """
    from utils.db.session import async_session

    if not owner_id or not provider:
        return
    try:
        async with async_session() as session:
            result = await session.execute(
                select(OwnerCredential).where(
                    OwnerCredential.owner_id == owner_id,
                    OwnerCredential.provider == provider,
                )
            )
            credential = result.scalar_one_or_none()
            if credential is None:
                return
            credential.is_valid = True
            credential.last_error_type = None
            credential.last_error_at = None
            credential.last_validated_at = datetime.utcnow()
            credential.updated_at = datetime.utcnow()
            await session.commit()
        logger.info("[credential_health] validated owner_id=%s provider=%s", owner_id, provider)
    except Exception as e:
        logger.error(
            "[credential_health] could not stamp validation for owner_id=%s provider=%s: %s",
            owner_id, provider, e, exc_info=True,
        )


# ── Platform-key fallback policy ──────────────────────────────────────────────

def _fallback_enabled() -> bool:
    """Read fresh each call so ops can toggle without a restart — same convention
    as ``USAGE_ENFORCE_LIMITS`` in utils/usage/enforcement.py. Defaults ON: a
    broken key silently degrading to a working answer is the behaviour this whole
    change exists to provide."""
    return os.getenv("BYOK_PLATFORM_FALLBACK", "true").strip().lower() in {"1", "true", "yes", "on"}


async def platform_fallback_allowed(owner_id: Optional[str], operation: str) -> Tuple[bool, str]:
    """May the platform key cover for this owner's dead BYOK key right now?

    Returns ``(allowed, reason)``. ``reason`` is a short machine-ish tag for logs
    and for the client-facing warning, not a sentence.

    The gate is the owner's own usage limit. While the owner funded their own
    provider calls they were exempt from platform quotas
    (``utils/usage/enforcement.py``) — there was no platform spend to ration. The
    moment we start paying for them that exemption no longer applies, so an owner
    who is already past their limit does not get covered; they get a clear error
    telling them to fix the key. Covering them anyway would make a broken key the
    cheapest way to get unlimited usage.

    Fails **open** on a limit-check error, matching ``enforce_operation``: a
    metering hiccup should not turn a recoverable request into a failed one.
    """
    if not _fallback_enabled():
        return False, "fallback_disabled"

    # No owner means nothing to meter and nothing to exempt — this is an unmetered
    # path already, so covering it changes no accounting.
    if not owner_id:
        return True, "no_owner"

    try:
        from utils.db.session import async_session
        from utils.usage.limit_checking import check_operation_allowed

        async with async_session() as session:
            result = await check_operation_allowed(session, owner_id, operation, [])
    except Exception as e:
        logger.error(
            "[credential_health] limit check failed for owner_id=%s op=%s: %s — allowing "
            "platform fallback (fail open)",
            owner_id, operation, e, exc_info=True,
        )
        return True, "limit_check_failed"

    if result.allowed:
        return True, "within_limit"

    logger.info(
        "[credential_health] refusing platform fallback for owner_id=%s op=%s — over limit",
        owner_id, operation,
    )
    return False, "over_limit"


# ── Lazy re-validation ────────────────────────────────────────────────────────

def _revalidation_ttl() -> Optional[timedelta]:
    """How stale a key may be before a read triggers a background re-check.

    ``BYOK_REVALIDATION_TTL_HOURS=0`` disables re-validation entirely.
    """
    try:
        hours = float(os.getenv("BYOK_REVALIDATION_TTL_HOURS", "24"))
    except ValueError:
        hours = 24.0
    return timedelta(hours=hours) if hours > 0 else None


# In-flight guard. Without it, a burst of concurrent chat requests for the same
# owner would each spawn their own live provider check for the same key — the
# stampede this is meant to avoid. Process-local, which is enough: the worst case
# across N workers is N checks per TTL window, not N per request.
_revalidating: Set[Tuple[str, str]] = set()


def schedule_revalidation_if_stale(
    owner_id: str, provider: str, last_validated_at: Optional[datetime]
) -> None:
    """Kick off a background re-check when this key has not been proven recently.

    Deliberately fire-and-forget and deliberately *not* awaited by the caller: the
    point is to catch an expired key before the owner's next chat, without making
    any request wait on a live provider round trip. The credential just read is
    still returned and used as-is; if the check finds it dead, the demotion lands
    before the next request.

    A no-op when re-validation is disabled, when the key was checked inside the
    TTL, or when a check for this exact key is already running.
    """
    ttl = _revalidation_ttl()
    if ttl is None or not owner_id or not provider:
        return

    if last_validated_at is not None:
        # Stored naive (TIMESTAMP without time zone), written from datetime.utcnow().
        age = datetime.utcnow() - last_validated_at
        if age < ttl:
            return

    key = (owner_id, provider)
    if key in _revalidating:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop (a sync caller) — skip rather than block. The next async
        # read of this credential will schedule it.
        return

    _revalidating.add(key)
    loop.create_task(_revalidate(owner_id, provider))


async def _revalidate(owner_id: str, provider: str) -> None:
    """Live-check one stored key and record the verdict."""
    from utils.usage.credentials import get_credential_for_owner

    try:
        api_key, key_type = await get_credential_for_owner(owner_id, provider)
        if not api_key or key_type != "owner-key":
            return

        error_type = await check_key_liveness(provider, api_key)
        if error_type is None:
            await mark_credential_validated(owner_id, provider)
        elif is_terminal(error_type):
            await mark_credential_failed(
                owner_id, provider, error_type, "background re-validation"
            )
        else:
            # Transient at check time (rate limited, provider down). Leave the key
            # alone and leave last_validated_at stale so the next read retries.
            logger.info(
                "[credential_health] re-validation inconclusive for owner_id=%s "
                "provider=%s (%s) — leaving key in service",
                owner_id, provider, error_type,
            )
    except Exception as e:
        logger.error(
            "[credential_health] re-validation crashed for owner_id=%s provider=%s: %s",
            owner_id, provider, e, exc_info=True,
        )
    finally:
        _revalidating.discard((owner_id, provider))


async def check_key_liveness(provider: str, api_key: str) -> Optional[str]:
    """Test a key against its provider. Returns an error kind, or ``None`` if healthy.

    Wraps ``utils/usage/llm_alerts.check_provider_status``, which is synchronous
    httpx, in a worker thread so this never blocks the FastAPI event loop.

    Providers that checker has no probe for (``deepseek``, ``minimax``) report
    ``unsupported_provider``; that is mapped to ``None`` — "we cannot tell". Any
    other reading would mean an unprobeable provider's key is treated as broken
    the moment it is saved.
    """
    from utils.usage.llm_alerts import check_provider_status

    try:
        status = await asyncio.to_thread(check_provider_status, provider, api_key)
    except Exception as e:
        logger.warning("[credential_health] liveness probe crashed for provider=%s: %s", provider, e)
        return None

    if status.error_type == "unsupported_provider":
        return None
    if status.is_valid and not status.has_low_balance:
        return None
    if status.has_low_balance:
        return "low_balance"
    if status.has_rate_limit:
        return "rate_limit"
    return status.error_type or "other"
