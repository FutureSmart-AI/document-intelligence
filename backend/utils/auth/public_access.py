"""Public-access gating for the widget and full-screen chat embed surfaces.

Ported from fs-agent-chat's ``auth_lib/identity.py::resolve_public_agent`` +
``agent_registry/db/agent_params_crud.py::host_matches_allowlist``, rekeyed to
``knowledge_id`` and reading ``knowledge_chat_config`` instead of
``agent_params``. See docs/plan/access-and-sharing/archive/design-widget-fullscreen.md (Piece 2).

Used only from ``require_permission(..., allow_public=True)``
(utils/auth/dependencies.py) — every other route stays credential-only.
"""

from typing import Any, List, Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


async def request_is_full_screen(request: Request) -> bool:
    """Resolve which embed surface a credential-less request is calling for.

    Checked as a query param first (covers the body-less config GET,
    ``?from_full_screen=true``), then the JSON/multipart body (covers the chat
    POST). Absent or unparseable => widget (the default surface, matching
    fs-agent-chat's pre-existing behavior). Reads the body Starlette already
    cached, so the route handler can still read it afterwards.
    """
    if _coerce_bool(request.query_params.get("from_full_screen")):
        return True
    content_type = request.headers.get("content-type", "")
    try:
        if "multipart/form-data" in content_type:
            form = await request.form()
            return _coerce_bool(form.get("from_full_screen"))
        data = await request.json()
        if isinstance(data, dict):
            return _coerce_bool(data.get("from_full_screen", False))
    except Exception:
        pass
    return False


def _extract_caller_origin_strict(request: Request) -> Optional[str]:
    """The caller's origin host, from Origin or Referer only — never Host or
    X-Forwarded-Host, which describe the request's destination, not its
    source, and would silently bypass the allowlist."""
    try:
        origin = request.headers.get("origin")
        if origin:
            host = (urlparse(origin).hostname or "").lower().strip(".")
            return host or None
        referer = request.headers.get("referer")
        if referer:
            host = (urlparse(referer).hostname or "").lower().strip(".")
            return host or None
    except Exception:
        pass
    return None


def host_matches_allowlist(request_host: Optional[str], allowed: Optional[List[str]]) -> bool:
    """True iff request_host matches one of ``allowed``.

    Supports exact match and leading-label wildcards (``*.example.com``
    matches any subdomain, not the apex itself). An empty/None ``allowed``
    means "no restriction" — any non-empty host matches.
    """
    if not request_host:
        return False
    host = request_host.strip().lower().strip(".")
    if not host:
        return False
    if not allowed:
        return True
    for entry in allowed:
        if not entry:
            continue
        candidate = str(entry).strip().lower()
        # Strip scheme/port if the caller sent a full URL instead of a bare host.
        candidate = (urlparse(candidate).hostname or candidate).strip(".")
        if candidate.startswith("*."):
            suffix = candidate[1:]  # ".example.com"
            if host.endswith(suffix) and host != suffix.lstrip("."):
                return True
        elif host == candidate:
            return True
    return False


async def authorize_public_chat_access(request: Request, knowledge_id: str) -> bool:
    """Authorize a credential-less request against a knowledge base's public
    chat gating. Returns the resolved ``is_full_screen`` flag on success.

    Raises 401 if the target surface is not public, 403 if the origin is
    missing or not in that surface's allowlist. Mirrors fs-agent-chat's
    resolve_public_agent, one-to-one, against knowledge_chat_config instead
    of agent_params.
    """
    from chat.db.config_ops import get_config_row

    row = await get_config_row(knowledge_id)
    if row is None:
        raise HTTPException(status_code=401, detail="Chat is not publicly available for this knowledge base.")

    is_full_screen = await request_is_full_screen(request)
    if is_full_screen:
        surface_is_public = row.fullscreen_is_public
        effective_allowlist = row.fullscreen_allowed_domains
    else:
        surface_is_public = row.is_public
        effective_allowlist = row.allowed_domains

    if not surface_is_public:
        raise HTTPException(status_code=401, detail="Chat is not publicly available for this knowledge base.")

    if effective_allowlist:
        request_host = _extract_caller_origin_strict(request)
        if request_host is None:
            raise HTTPException(
                status_code=403,
                detail="Missing Origin/Referer header. Public chat requests must come from a browser context on an allowed domain.",
            )
        if not host_matches_allowlist(request_host, effective_allowlist):
            raise HTTPException(status_code=403, detail="Request origin is not permitted for this knowledge base.")

    return is_full_screen
