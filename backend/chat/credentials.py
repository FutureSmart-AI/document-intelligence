"""
BYOK credential lookup for the chat hot path (chat/llm_factory.py::get_llm).

Prefers the in-process, `knowledge_id`-keyed lookup
(utils/usage/credentials.py::get_credential_for_knowledge) — no outbound HTTP
call. `get_owner_credential_async` (the `owner_id`-keyed HTTP call to
USAGE_TRACKING_BASE_URL via httpx.AsyncClient, so it never blocks the FastAPI
event loop the way a sync `requests.get` would) is now only a fallback for a
call site that hasn't been updated to pass `knowledge_id` yet.
"""

from typing import Optional, Tuple

import httpx

from utils.logger import logger
from utils.usage.credentials import get_credential_for_knowledge
import utils.setup_env as setup_env

USAGE_TRACKING_BASE_URL = setup_env.USAGE_TRACKING_BASE_URL
FASTAPI_SECRET_KEY = setup_env.FASTAPI_SECRET_KEY

# Matches utils/connectors/clickup.py's shared timeout budget.
_HTTPX_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)


async def get_owner_credential_async(owner_id: str, provider: str) -> Optional[str]:
    """Fetch an owner's API key for `provider` from the credentials API, async.

    Returns None on any failure (missing config, 404, invalid key, network
    error) so callers fall back to the platform default key.
    """
    if not owner_id or not provider:
        logger.warning(f"Invalid parameters: owner_id={owner_id}, provider={provider}")
        return None

    if not USAGE_TRACKING_BASE_URL:
        logger.warning("USAGE_TRACKING_BASE_URL is not configured")
        return None

    endpoint = f"{USAGE_TRACKING_BASE_URL}/v1/owners/{owner_id}/credentials/{provider}/internal"
    headers = {"accept": "application/json", "x-api-key": FASTAPI_SECRET_KEY}

    try:
        async with httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as client:
            response = await client.get(endpoint, headers=headers)

        if response.status_code == 200:
            data = response.json()
            if data.get("is_valid", False) and data.get("api_key"):
                logger.info(f"Retrieved owner credential for owner_id={owner_id}, provider={provider}")
                return data["api_key"]
            logger.info(f"Owner credential not valid for owner_id={owner_id}, provider={provider}")
            return None
        elif response.status_code == 404:
            logger.info(f"No credential found for owner_id={owner_id}, provider={provider}")
            return None
        else:
            logger.warning(f"Failed to fetch owner credential: status_code={response.status_code}")
            return None

    except httpx.HTTPError as e:
        logger.error(f"Error fetching owner credential for owner_id={owner_id}, provider={provider}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching owner credential for owner_id={owner_id}, provider={provider}: {e}")
        return None


async def resolve_owner_credential_async(
    owner_id: Optional[str], provider: str, knowledge_id: Optional[str] = None
) -> Tuple[Optional[str], str]:
    """Resolve a BYOK credential, preferring the in-process `knowledge_id`-keyed
    lookup (utils/usage/credentials.py) over the external HTTP round trip.

    `knowledge_id` is optional only so this stays callable from any spot that
    hasn't been updated to pass it yet — every call site added in this repo
    supplies it. Falls back to the old owner_id-keyed HTTP lookup when it's
    missing, logging a warning so a missed call site is visible in prod.
    """
    if knowledge_id:
        return await get_credential_for_knowledge(knowledge_id, provider)

    if not owner_id:
        return None, "platform-key"

    logger.warning(
        f"[credentials] resolve_owner_credential_async called without knowledge_id "
        f"(owner_id={owner_id}, provider={provider}) — falling back to the external "
        f"Usage service HTTP lookup instead of the in-process one"
    )
    owner_key = await get_owner_credential_async(owner_id, provider)
    if owner_key:
        return owner_key, "owner-key"
    return None, "platform-key"


async def get_api_key_async(
    owner_id: Optional[str], provider: str, default_key: str, knowledge_id: Optional[str] = None,
    force_platform_key: bool = False,
) -> Tuple[str, str]:
    """Async twin of owner_credentials.get_api_key — used by chat/llm_factory.py.

    Returns (api_key, key_type) where key_type is "owner-key" or "platform-key".

    ``force_platform_key`` skips the BYOK lookup entirely. It exists for the
    same-request retry in chat/byok_fallback.py: once the owner's key has been
    rejected by the provider, the retry must not resolve that same key again.
    Relying on the demotion written by ``mark_credential_failed`` would be a race —
    the retry can read the row before the demotion commits — so the retry says
    "platform key" explicitly rather than hoping the lookup now agrees.
    """
    if force_platform_key:
        return default_key, "platform-key"

    owner_key, key_type = await resolve_owner_credential_async(owner_id, provider, knowledge_id)
    if owner_key:
        return owner_key, key_type
    return default_key, "platform-key"
