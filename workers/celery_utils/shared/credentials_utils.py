"""
Credentials Utility Module

This module provides functions to retrieve owner-specific API credentials
from fs-agent-knowledge's usage-credentials API and resolve which API key to
use (owner-specific or default).

Previously called the standalone fs-agent-usage-limits service directly
(USAGE_TRACKING_BASE_URL); that service's credential-lookup logic now lives
in-process inside fs-agent-knowledge (see fs-agent-knowledge's
docs/plan/usage-and-limits/), reachable over HTTP the same way
celery_utils/enrichment/enrichment_utils.py already calls that service for
enrichment context — same KNOWLEDGE_API_BASE_URL/KNOWLEDGE_API_KEY, same
x-api-key header.
"""

import os
from typing import Optional, Tuple
from services.celery_logger import logger
import requests

from services.celery_setup_env import (
    KNOWLEDGE_API_BASE_URL as _KNOWLEDGE_API_BASE_URL,
    KNOWLEDGE_API_KEY as _KNOWLEDGE_API_KEY,
)


def get_owner_credential(owner_id: str, provider: str) -> Optional[str]:
    """
    Fetch owner credential from fs-agent-knowledge's internal credentials endpoint.

    Args:
        owner_id (str): The owner ID (email) to fetch credentials for
        provider (str): The provider name (e.g., "openai", "groq", "llama_parse", "firecrawl")

    Returns:
        Optional[str]: The API key if found and valid, None otherwise
    """
    if not owner_id or not provider:
        logger.warning(f"Invalid parameters: owner_id={owner_id}, provider={provider}")
        return None

    if not _KNOWLEDGE_API_BASE_URL:
        logger.error("KNOWLEDGE_API_BASE_URL is not configured")
        return None

    try:
        endpoint = f"{_KNOWLEDGE_API_BASE_URL}/v1/owners/{owner_id}/usage/credentials/{provider}/internal"

        headers = {
            "accept": "application/json",
            "x-api-key": _KNOWLEDGE_API_KEY
        }

        logger.debug(f"Fetching owner credential for owner_id={owner_id}, provider={provider}")
        response = requests.get(endpoint, headers=headers, timeout=10)

        if response.status_code == 200:
            data = response.json()
            # Check if credential is valid
            if data.get("is_valid", False) and data.get("api_key"):
                logger.info(f"Successfully retrieved owner credential for owner_id={owner_id}, provider={provider}")
                return data.get("api_key")
            else:
                if os.getenv("BYOK_PLATFORM_FALLBACK", "true").lower() in {"false", "0", "no", "off"}:
                    raise ValueError(f"The configured {provider} key is invalid; replace or remove it")
                logger.info(f"Owner credential found but not valid for owner_id={owner_id}, provider={provider}")
                return None
        elif response.status_code == 404:
            logger.debug(f"No credential found for owner_id={owner_id}, provider={provider}")
            return None
        else:
            if os.getenv("BYOK_PLATFORM_FALLBACK", "true").lower() in {"false", "0", "no", "off"}:
                raise ValueError(f"Credential lookup failed for {provider}: HTTP {response.status_code}")
            logger.warning(f"Failed to fetch owner credential: status_code={response.status_code}, response={response.text}")
            return None

    except requests.exceptions.RequestException as e:
        if os.getenv("BYOK_PLATFORM_FALLBACK", "true").lower() in {"false", "0", "no", "off"}:
            raise
        logger.error(f"Error fetching owner credential for owner_id={owner_id}, provider={provider}: {e}")
        return None
    except Exception as e:
        if os.getenv("BYOK_PLATFORM_FALLBACK", "true").lower() in {"false", "0", "no", "off"}:
            raise
        logger.error(f"Unexpected error fetching owner credential for owner_id={owner_id}, provider={provider}: {e}")
        return None


# ── BYOK failure classification + reporting ──────────────────────────────────
#
# This mirrors fs-agent-knowledge's utils/usage/credential_health.py — the
# knowledge service holds the credential row and does the real classification for
# its own (chat) call sites, but this repo resolves keys over HTTP and has no DB
# access to demote a row itself, so it classifies locally and reports the verdict
# back over HTTP (report_credential_failure below). Keeping the same three-way
# split (auth_error / low_balance / rate_limit) and the same rule — only the first
# two are terminal — matters: the two services must agree on what "the key is
# dead" means, or a worker could demote a key chat would have kept trying.

TERMINAL_ERROR_TYPES = frozenset({"auth_error", "low_balance"})

_AUTH_PATTERNS = (
    "invalid_api_key", "invalid api key", "incorrect api key", "invalid x-api-key",
    "api key not valid", "api_key_invalid", "authenticationerror", "authentication_error",
    "authentication failed", "unauthorized", "permission_denied", "permissiondenied",
    "unauthenticated", "no auth credentials found",
)
_BALANCE_PATTERNS = (
    "insufficient_quota", "insufficient quota", "insufficient balance",
    "billing_hard_limit_reached", "exceeded your current quota",
    "credit balance is too low", "resource_exhausted", "payment_required",
)
_RATE_LIMIT_PATTERNS = ("rate_limit", "rate limit", "too many requests", "429")


def _http_status(exc: BaseException) -> Optional[int]:
    for attr in ("status_code", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def classify_provider_error(exc: BaseException) -> Optional[str]:
    """Name the failure kind, or None when it is not a credential problem.

    Returns ``auth_error``, ``low_balance``, ``rate_limit``, or ``None`` (timeouts,
    connection errors, provider 5xx, model-not-found — anything we should not act
    on). See the module-level note above: this must classify the same way
    fs-agent-knowledge's copy does.
    """
    text = f"{type(exc).__name__} {exc}".lower()
    status = _http_status(exc)

    # Checked before the rate-limit branch: OpenAI reports a spent balance as 429
    # with code=insufficient_quota, so text has to win that ambiguity.
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
    return error_type in TERMINAL_ERROR_TYPES


def report_credential_failure(owner_id: str, provider: str, error_type: str, message: str = "") -> bool:
    """Tell fs-agent-knowledge a provider rejected this owner's key, so it can
    demote the stored credential. Returns True if the report demoted it.

    Best-effort: this runs on an already-failing call's error path, so a failure
    here is logged and swallowed rather than raised — losing the demotion means
    the NEXT request repeats this same failed call once more, which is a cost, not
    a correctness problem (get_api_key still falls back to the platform key this
    request either way, via the caller's own retry).
    """
    if not owner_id or not provider:
        return False
    if not _KNOWLEDGE_API_BASE_URL:
        logger.error("KNOWLEDGE_API_BASE_URL is not configured — cannot report credential failure")
        return False

    endpoint = f"{_KNOWLEDGE_API_BASE_URL}/v1/owners/{owner_id}/usage/credentials/{provider}/report-failure"
    headers = {"accept": "application/json", "content-type": "application/json", "x-api-key": _KNOWLEDGE_API_KEY}
    try:
        response = requests.post(
            endpoint, headers=headers, timeout=10,
            json={"error_type": error_type, "message": message[:500]},
        )
        if response.status_code == 200:
            demoted = bool(response.json().get("demoted", False))
            if demoted:
                logger.error(
                    f"BYOK key DEMOTED owner_id={owner_id} provider={provider} reason={error_type} "
                    f"— later tasks will use the platform key until the owner replaces it"
                )
            return demoted
        logger.warning(
            f"Failed to report credential failure: status_code={response.status_code}, "
            f"response={response.text}"
        )
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Error reporting credential failure for owner_id={owner_id}, provider={provider}: {e}")
        return False


def platform_fallback_allowed(owner_id: Optional[str], operation: str = "llm_call") -> bool:
    """May the platform key cover for this owner's dead BYOK key right now?

    Mirrors fs-agent-knowledge's utils/usage/credential_health.py::
    platform_fallback_allowed — same policy (an owner already over their usage
    limit does not get free platform coverage just because their key died), just
    reached over HTTP via the existing /usage/limits/check endpoint instead of a
    direct DB read, since this repo has no DB access to the usage tables.

    Fails OPEN (returns True) on any error reaching the endpoint — a metering
    hiccup must not turn a recoverable request into a failed one. No owner_id
    means nothing to meter, so that also allows.
    """
    if os.getenv("BYOK_PLATFORM_FALLBACK", "true").lower() in {"false", "0", "no", "off"}:
        return False
    if not owner_id:
        return True
    if not _KNOWLEDGE_API_BASE_URL:
        return True

    endpoint = f"{_KNOWLEDGE_API_BASE_URL}/v1/owners/{owner_id}/usage/limits/check"
    headers = {"accept": "application/json", "content-type": "application/json", "x-api-key": _KNOWLEDGE_API_KEY}
    try:
        response = requests.post(
            endpoint, headers=headers, timeout=10,
            json={"operation": operation, "estimated_metrics": []},
        )
        if response.status_code == 200:
            return bool(response.json().get("allowed", True))
        logger.warning(
            f"Usage limit check failed: status_code={response.status_code} — allowing fallback (fail open)"
        )
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Error checking usage limit for owner_id={owner_id}: {e} — allowing fallback (fail open)")
        return True


def get_api_key(owner_id: Optional[str], provider: str, default_key: str) -> Tuple[str, str]:
    """
    Get the appropriate API key to use (owner-specific or default).

    Args:
        owner_id (Optional[str]): The owner ID to fetch credentials for. If None, uses default key.
        provider (str): The provider name (e.g., "openai", "groq", "llama_parse", "firecrawl")
        default_key (str): The default API key to use if owner credential is not available

    Returns:
        Tuple[str, str]: A tuple of (api_key, key_type) where key_type is "owner-key" or "platform-key"
    """
    try:
        if not owner_id:
            return default_key, "platform-key"

        owner_key = get_owner_credential(owner_id, provider)

        if owner_key:
            logger.info(f"Using owner-specific API key for owner_id={owner_id}, provider={provider}")
            return owner_key, "owner-key"
        else:
            logger.info(f"Using default API key for provider={provider}")
            return default_key, "platform-key"
    except Exception as e:
        logger.error(f"Error getting API key for owner_id={owner_id}, provider={provider}: {e}")
        if os.getenv("BYOK_PLATFORM_FALLBACK", "true").lower() in {"false", "0", "no", "off"}:
            raise
        return default_key, "platform-key"
