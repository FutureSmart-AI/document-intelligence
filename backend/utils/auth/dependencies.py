import os
from typing import Optional
from urllib.parse import urlparse
from fastapi import Query, Request
from utils.logger import logger
import httpx
from fastapi import Security, HTTPException, status, Depends
from fastapi.security.api_key import APIKeyHeader
from utils.setup_env import FASTAPI_SECRET_KEY
from utils.pydantic_models_knowledge import GetKnowledgeParamsAuth
from pydantic import ValidationError

from utils.tracing import track_time

API_KEY_NAME = FASTAPI_SECRET_KEY

api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)

def _extract_host_from_request(request: Optional["Request"]) -> str:
    try:
        if not request:
            return ""
        origin = request.headers.get("origin") or ""
        referer = request.headers.get("referer") or ""
        xfh = request.headers.get("x-forwarded-host") or ""
        host_hdr = request.headers.get("host") or ""

        if origin:
            return (urlparse(origin).hostname or "").lower()
        if referer:
            return (urlparse(referer).hostname or "").lower()
        if xfh:
            return xfh.split(":")[0].lower()
        if host_hdr:
            return host_hdr.split(":")[0].lower()
        return ""
    except Exception:
        return ""


def resolve_access_cookie_key_from_host(host: Optional[str]) -> str:
    """Map a request host to the access token cookie key.

    Examples:
    - agent.futuresmart.ai -> agent_access_token
    - staging.agent.futuresmart.ai -> staging_access_token
    - nl2sql.futuresmart.ai -> nl2sql_access_token

    Always use the left-most label (e.g., 'aidemos' for aidemos.com, 'agent' for agent.futuresmart.ai).
    For unknown/empty hosts, fall back to "access_token".
    """
    try:
        h = (host or "").strip().lower()
    except Exception:
        h = ""

    parts = [p for p in h.split(".") if p]
    first_label = parts[0] if parts else None
    if first_label:
        return f"{first_label}_access_token"
    return "access_token"


def resolve_access_cookie_key_from_request(request: Optional["Request"]) -> str:
    host = _extract_host_from_request(request)
    return resolve_access_cookie_key_from_host(host)

async def get_auth_details(request: Request, include_all_claims=False):
    # Memoize the /user/me result per-request, keyed by include_all_claims, so the
    # audit dependency and the route's own auth dependency don't each make a
    # network call. Net cost: one auth call per request.
    cache = getattr(request.state, "_auth_details_cache", None)
    if cache is None:
        cache = {}
        try:
            request.state._auth_details_cache = cache
        except Exception:
            pass
    if include_all_claims in cache:
        return cache[include_all_claims]

    result = await _fetch_auth_details(request, include_all_claims=include_all_claims)
    # Cache only resolved results (None included); transient errors raise and are
    # intentionally not cached so a later dependency can retry / surface them.
    cache[include_all_claims] = result
    return result


@track_time("API: Get Auth Details")
async def _fetch_auth_details(request: Request, include_all_claims=False):
    if os.getenv("AUTH_MODE") == "standalone":
        from utils.auth.standalone import local_profile
        return local_profile(request)
    logger.info(f"Attempting to retrieve auth details for host: {_extract_host_from_request(request)}")
    # 1) Try Authorization: Bearer <token> header first
    auth_header = request.headers.get("authorization") or ""
    access_token = None
    if auth_header.lower().startswith("bearer "):
        access_token = auth_header[7:].strip()

    # 2) Fall back to cookies
    if not access_token:
        cookie_key = resolve_access_cookie_key_from_request(request)
        access_token = (
            request.cookies.get(cookie_key)
            or request.cookies.get("agent_access_token")
            or request.cookies.get("access_token")
        )

    if not access_token:
        return None

    url = f"https://auth.futuresmart.ai/user/me?include_all_claims={include_all_claims}&access_token={access_token}"
    headers = {"accept": "application/json"}

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)

    if response.status_code == 200:
        logger.info(f"Successfully retrieved auth details for host: {_extract_host_from_request(request)}")
        return response.json()
    else:
        logger.error(f"Invalid Access Token: {response.status_code} - {response.text}")
        return None


# async def get_api_key(
#     api_key: str = Security(api_key_header)
# ):
#     if api_key == API_KEY_NAME:
#         return True
#     else:
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Invalid API Key"
#         )


async def get_api_key_or_session(
    request: Request,
    api_key: Optional[str] = Security(api_key_header),
):
    """Accept a valid session (cookie/Bearer token), the master API key, or a
    per-user generated API key (see utils/db/api_key_ops.py /
    routers/api_key_management.py, docs/plan/access-and-sharing/)."""
    if api_key and API_KEY_NAME and api_key == API_KEY_NAME:
        return {"auth_type": "master_api_key", "api_key": api_key}
    session_data = await get_auth_details(request, include_all_claims=True)
    if session_data:
        logger.info(f"Authenticated via session - user: {session_data.get('email')}, user_id: {session_data.get('user_id')}")
        return {"auth_type": "session", "session_data": session_data}
    if api_key and api_key == API_KEY_NAME:
        logger.info("Authenticated via master API key")
        return {"auth_type": "master_api_key", "api_key": api_key}
    if api_key:
        from utils.db.api_key_ops import validate_api_key
        identity = await validate_api_key(api_key)
        if identity:
            logger.info(f"Authenticated via generated API key - user_id: {identity.get('user_id')}")
            return {"auth_type": "user_api_key", "api_key": api_key, **identity}
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated"
    )

async def require_internal_service(
    x_api_key: Optional[str] = Security(api_key_header),
) -> None:
    """Service-to-service only — for the Batch Query Reports Worker-facing
    surface (routers/reports_internal.py, docs/plan/
    batch-query-report-migration/plan.md). Requires the master x-api-key and
    explicitly does NOT fall back to a browser session, unlike
    ``require_permission``/``get_api_key_or_session`` — these endpoints must
    reject a session-authenticated browser caller even if one happens to be
    presented, since they carry no knowledge_id-scoped RBAC of their own
    (the run_id alone doesn't identify a caller's permitted knowledge base).
    Mirrors the `require_internal_service` dependency the source spec
    (fs-agent-dashboard's docs/batch-query-reports-spec.md §5) calls for.
    """
    if not x_api_key or x_api_key != API_KEY_NAME:
        raise HTTPException(status_code=401, detail="Invalid or missing internal service API key")


@track_time("API: Check User Access")
async def _check_access_locally(user_email: str, knowledge_id: str, permission: str) -> bool:
    """Resolve a non-owner caller's role from this service's own
    ``knowledge_invitations`` table and check it against the permission
    matrix — in-process replacement for the HTTP call this service used to
    make to fs-agent-chat's ``GET /v1/auth/check-access``
    (docs/plan/access-and-sharing/archive/design-share-invitations.md). Never raises; an unexpected
    DB error denies access rather than failing open.
    """
    try:
        from utils.auth.permissions import role_allows
        from utils.db.knowledge_invitations import get_invited_role

        role = await get_invited_role(knowledge_id, user_email)
        has_access = role_allows(role, permission)
        logger.info(
            f"Local RBAC check: user={user_email} knowledge={knowledge_id} "
            f"permission={permission} role={role} allowed={has_access}"
        )
        return has_access
    except Exception as e:
        logger.error(f"Local RBAC check failed for user={user_email} knowledge={knowledge_id}: {e}")
        return False

# Sentinel actor id recorded on rows created via the master API key (no session) —
# e.g. the ClickUp connector. require_permission() itself keeps returning None for
# this case, since many read/delete paths rely on None meaning "master key, skip the
# owner filter." Insert call sites should map None -> this sentinel via
# resolve_actor_id() so the row records a real actor instead of NULL.
MASTER_API_KEY_ACTOR = "master_api_key"

# Sentinel returned by require_permission(..., allow_public=True) for a
# credential-less public caller — distinguishable from the master-key path
# (also None) so routers/chat.py's GET /chat/config can tell "public, strip
# the gating fields" apart from "master key, an internal caller that may see
# them". Never returned by the credential-only (default) path.
PUBLIC_CALLER_SENTINEL = "__public__"


def resolve_actor_id(user_id: Optional[str]) -> Optional[str]:
    """Map require_permission()'s None (master API key, no session) to a non-null
    actor id for storage on newly created rows. A session-derived user_id is
    already guaranteed non-empty (require_permission raises otherwise)."""
    return user_id or MASTER_API_KEY_ACTOR


def require_permission(permission: str, allow_public: bool = False):
    """
    Dependency factory. Usage: Depends(require_permission("write"))

    For session users:
      1. Extract user_id from session.
      2. Check DB ownership — if owner, role = "owner".
      3. Otherwise look up the caller's role in this service's own
         ``knowledge_invitations`` table (utils/db/knowledge_invitations.py) —
         previously an HTTP call to fs-agent-chat's ``check-access``, now
         in-process (docs/plan/access-and-sharing/archive/design-share-invitations.md).
      4. Verify the role permits the requested permission
         (utils/auth/permissions.py::role_allows).

    Master API key bypasses all checks.
    Returns user_id (str) for session callers, None for master API key.

    ``allow_public=True`` (only ever set by routers/chat.py's public chat
    surfaces — POST /chat and GET /chat/config) additionally lets a
    credential-less caller through when the target knowledge base has
    published one of the two embed surfaces public: widget
    (``is_public``/``allowed_domains``) or full-screen
    (``fullscreen_is_public``/``fullscreen_allowed_domains``), resolved from
    the request's ``from_full_screen`` flag. See utils/auth/public_access.py
    and docs/plan/access-and-sharing/archive/design-widget-fullscreen.md (Piece 2). Every other
    caller of require_permission is unaffected — the default stays
    credential-only.
    """
    async def dependency(
        knowledge_id: str,
        request: Request,
        x_api_key: Optional[str] = Security(api_key_header),
    ) -> Optional[str]:
        try:
            auth = await get_api_key_or_session(request, x_api_key)
        except HTTPException:
            if not allow_public:
                raise
            from utils.auth.public_access import authorize_public_chat_access
            await authorize_public_chat_access(request, knowledge_id)
            # A public caller has no user_id of its own — chat/session rows
            # attribute to the knowledge base itself via knowledge_id, not a user.
            return PUBLIC_CALLER_SENTINEL

        if auth.get("auth_type") == "master_api_key":
            return None

        from utils.db.knowledge_ops import get_knowledge_service
        from utils.auth.auth_utils import extract_owner_id_from_session, extract_user_from_email

        if auth.get("auth_type") == "user_api_key":
            # A generated key has no Cognito session — its user_id (the email
            # captured at generation time, see routers/api_key_management.py)
            # stands in for session_data.email/user_id so the same
            # owner-fast-path / chat-delegation logic below applies unchanged.
            session_data = {"email": auth["user_id"], "user_id": auth["user_id"]}
        else:
            session_data = auth["session_data"]

        # Resolve user_id
        user_id = await extract_user_from_email(session_data)
        if not user_id:
            raise HTTPException(status_code=401, detail="Could not resolve user identity")

        # Fetch the knowledge record
        service = await get_knowledge_service(knowledge_id)
        if service is None:
            raise HTTPException(status_code=404, detail="Knowledge not found")

        # Fast-path: owner has all permissions — no HTTP call needed
        caller_owner_id = extract_owner_id_from_session(session_data)
        service_owner_id = service.get("owner_id")
        if caller_owner_id and service_owner_id and str(service_owner_id).strip().lower() == caller_owner_id:
            logger.info(f"Access granted (owner): user={user_id} permission={permission} knowledge={knowledge_id}")
            return user_id

        # Non-owner — check this service's own invitations table + permission matrix
        user_email = session_data.get("email", "")
        has_access = await _check_access_locally(user_email, knowledge_id, permission)
        if not has_access:
            raise HTTPException(status_code=403, detail="Insufficient permissions")

        logger.info(f"Access granted (local-rbac): user={user_id} permission={permission} knowledge={knowledge_id}")
        return user_id

    return dependency


def require_owner():
    """Dependency factory for **owner-scoped** routes — those whose path param is
    ``owner_id`` rather than ``knowledge_id``. Usage: ``Depends(require_owner())``.

    Used by the usage/limits surface (`/v1/owners/{owner_id}/usage/...`), where the
    resource genuinely belongs to the account, not to one knowledge base.

    Rules:
      - Master API key bypasses all checks (returns None), same as
        ``require_permission`` — service-to-service callers.
      - A session (or generated per-user API key) caller must **be** the owner named
        in the path: their identity, resolved the same way knowledge ownership is
        (``extract_owner_id_from_session`` — the session email), must equal
        ``owner_id``. Anyone else gets 403.

    **Why there's no permission matrix here, unlike ``require_permission``.** The
    owner/editor/viewer roles in utils/auth/permissions.py describe access to a
    *knowledge base*. Usage totals, costs, quota limits and BYOK credentials are
    account-wide — they span every knowledge base the owner has — so an invited
    editor or viewer of one knowledge base must not see them at all, not even
    read-only. There is no meaningful "read but scoped" state to grant: either you
    are the account, or this data isn't about you.

    This closes a real leak that existed while the same data was served from
    `/v1/{knowledge_id}/usage/...` under ``require_permission("read")``: `viewer`
    holds `read`, so a user invited to a single knowledge base could pull the
    owner's totals across knowledge bases they had never been invited to. See
    docs/plan/usage-and-limits/decisions.md (2026-08-20).
    """
    async def dependency(
        owner_id: str,
        request: Request,
        x_api_key: Optional[str] = Security(api_key_header),
    ) -> Optional[str]:
        auth = await get_api_key_or_session(request, x_api_key)

        if auth.get("auth_type") == "master_api_key":
            return None

        from utils.auth.auth_utils import extract_owner_id_from_session

        if auth.get("auth_type") == "user_api_key":
            # A generated key carries no Cognito session; its captured email stands in.
            session_data = {"email": auth["user_id"], "user_id": auth["user_id"]}
        else:
            session_data = auth["session_data"]

        caller_owner_id = extract_owner_id_from_session(session_data)
        if not caller_owner_id:
            raise HTTPException(status_code=401, detail="Could not resolve caller identity")

        if caller_owner_id != str(owner_id).strip().lower():
            logger.warning(
                f"Owner-scope denied: caller={caller_owner_id} requested owner={owner_id}"
            )
            raise HTTPException(
                status_code=403,
                detail="Account-level usage data is only accessible to the account owner",
            )

        logger.info(f"Access granted (owner-scope): owner={caller_owner_id}")
        return caller_owner_id

    return dependency


# Backward-compatible alias used across existing routers (defaults to "write" — most restrictive)
verify_knowledge_owner = require_permission("write")


def validate_get_knowledge_query_params(
    # user_id: Optional[str] = Query(None),  # derived from session
    knowledge_id: str = Query(...),
    content_type: Optional[str] = Query(None)
) -> GetKnowledgeParamsAuth:
    try:
        return GetKnowledgeParamsAuth(user_id=None, knowledge_id=knowledge_id, content_type=content_type)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors())


# ---------------------------------------------------------------------------
# Universal per-request access audit
#
# A single router-level dependency (`audit_access`) that writes one standardized
# row to `knowledge_audit_log` per request. Attached at every router mount in
# main.py so it covers all current and future routes, including reads. See
# fs-agent-actions/docs/runbook-audit-logging.md for the pattern.
#
# Every endpoint is audited — including the high-volume `search` (RAG retrieval)
# route. Coverage was deliberately chosen over noise: the table is append-only and
# indexed, so retrieval rows are cheap. `health_check` lives on the app root (not
# under an audited router) so it is never reached; it is listed defensively only.
# ---------------------------------------------------------------------------

# create_knowledge is skipped here and logged explicitly by the handler instead —
# the knowledge_id doesn't exist yet when this dependency runs (it's generated inside
# the handler), so the automatic row would always carry knowledge_id=NULL.
_AUDIT_SKIP_ROUTE_NAMES = {"health_check", "create_knowledge"}

# The knowledge base is this service's target resource — almost always the
# `{knowledge_id}` path param. `service_id` is accepted as an alias because some
# cross-service callers name the same id that way (see check-access).
_KNOWLEDGE_ID_KEYS = ("knowledge_id", "service_id")
_OWNER_ID_KEYS = ("owner_id",)


def _clean_identifier(value) -> Optional[str]:
    """Normalize a candidate id to a non-empty string, treating 'me' as absent."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "me":
        return None
    return text


async def _request_json_dict(request: Request) -> Optional[dict]:
    """Read the JSON body as a dict, or None. Starlette caches the parsed body,
    so the route handler can still read it afterwards."""
    try:
        payload = await request.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


async def _resolve_first_key(request: Request, keys) -> Optional[str]:
    """First non-empty value for any of `keys`, looked up in path → query → body."""
    for key in keys:
        found = _clean_identifier(request.path_params.get(key))
        if found:
            return found
    for key in keys:
        found = _clean_identifier(request.query_params.get(key))
        if found:
            return found
    body = await _request_json_dict(request)
    if body:
        for key in keys:
            found = _clean_identifier(body.get(key))
            if found:
                return found
    return None


async def resolve_knowledge_id_from_request(request: Request) -> Optional[str]:
    """Resolve the knowledge base the request targets (path/query/body)."""
    return await _resolve_first_key(request, _KNOWLEDGE_ID_KEYS)


async def resolve_owner_id_from_request(request: Request) -> Optional[str]:
    """Resolve an explicit owner_id from path params, query params, then JSON body.
    'me' is treated as absent (the caller's session email is used instead)."""
    return await _resolve_first_key(request, _OWNER_ID_KEYS)


async def resolve_kb_owner_id(knowledge_id: Optional[str]) -> Optional[str]:
    """Look up the KB's real owner_id from knowledge_config. Never raises — a
    lookup failure just means the caller falls back to its own heuristic."""
    if not knowledge_id:
        return None
    try:
        from utils.db.knowledge_ops import get_knowledge_service as _get_knowledge_service
        service = await _get_knowledge_service(knowledge_id)
        return service.get("owner_id") if service else None
    except Exception:
        return None


async def audit_access(request: Request) -> None:
    """Persist one standardized audit row per request.

    Soft-extracts the authenticated user — it must NEVER raise on unauthenticated
    requests, and never break the request if persistence fails.
    """
    try:
        # action = the operation (route handler name); HTTP verb stays in `method`.
        route = request.scope.get("route")
        action = getattr(route, "name", None) or request.url.path
        if action in _AUDIT_SKIP_ROUTE_NAMES:
            return

        # Soft identity extraction — do NOT raise on unauthenticated requests.
        user_id: Optional[str] = None
        session_email: Optional[str] = None
        try:
            api_key = request.headers.get("x-api-key")
            session_data = None if api_key and API_KEY_NAME and api_key == API_KEY_NAME else await get_auth_details(request, include_all_claims=True)
            if session_data:
                session_email = session_data.get("email")
                user_id = session_email
        except Exception:
            session_data = None

        if not user_id:
            api_key = request.headers.get("x-api-key")
            if api_key and api_key == API_KEY_NAME:
                user_id = "master_api_key"
            elif api_key:
                # Soft, exactly like the session lookup above: a DB hiccup while
                # resolving the key must not cost us the whole audit row. Without
                # this, any failure here escapes to the outer handler and the
                # request goes completely unaudited.
                try:
                    from utils.db.api_key_ops import validate_api_key
                    identity = await validate_api_key(api_key)
                except Exception:
                    identity = None
                if identity:
                    user_id = identity.get("user_id")
                    session_email = user_id

        knowledge_id = await resolve_knowledge_id_from_request(request)
        # Prefer an explicit owner_id (e.g. the create_knowledge body). Otherwise
        # resolve the KB's actual owner from knowledge_config — the caller (session_email)
        # is only correct when it IS the owner, which is false for shared-agent access
        # and always false for master-key/connector calls (no session at all).
        owner_id = (
            await resolve_owner_id_from_request(request)
            or await resolve_kb_owner_id(knowledge_id)
            or session_email
        )

        from utils.audit_logging import log_access_event

        await log_access_event(
            user_id=user_id,
            owner_id=owner_id,
            knowledge_id=knowledge_id,
            action=action,
            method=request.method,
            path=request.url.path,
        )
    except Exception as exc:  # never break the request because of auditing
        logger.error("audit_access: failed to persist audit row: %s", str(exc))
