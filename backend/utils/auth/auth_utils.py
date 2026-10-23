"""
Authentication utilities for chatbot knowledge management.
Contains user extraction and authentication helper functions.
"""

from typing import Optional
from utils.tracing import track_time


def extract_owner_id_from_session(session_data: Optional[dict]) -> Optional[str]:
    """
    Return the owner identifier used for knowledge authorization.

    Knowledge ownership is stored against the session email, so authorization
    checks should compare against that value directly.
    """
    if not session_data:
        return None

    email = session_data.get("email")
    if isinstance(email, str):
        normalized_email = email.strip().lower()
        if normalized_email:
            return normalized_email

    return None


@track_time("Auth: Extract User From Email")
async def extract_user_from_email(session_data):
    """
    Return the `user_id` from the provided session data fetched from auth service.
    Falls back to `all_claims.sub` if `user_id` is missing.
    """

    from utils.db.db_utils import get_user_id_from_email
    
    user_id = await get_user_id_from_email(session_data.get("email"))
    if user_id:
        return user_id
    else:
        if not session_data:
            return None
        user_id = session_data.get("user_id")
        if user_id:
            return user_id
        claims = session_data.get("all_claims", {}) or {}
        return claims.get("sub")


def extract_caller_profile(session_data: Optional[dict]) -> tuple:
    """Return ``(email, display_name)`` for the caller, either half possibly None.

    Why this exists: a chat session row stores ``user_id``, which in this service is
    an account uuid (``get_user_id_from_email`` is stubbed out — the ``users`` table
    belongs to the auth service, not to us). Nothing in this database can turn that
    uuid back into a person, so the readable half of the identity has to be captured
    from the request that created the session. This pulls it out of whatever the auth
    service returned for ``/user/me``.

    The name is looked for under the shapes an OIDC provider actually uses, in
    decreasing order of specificity: a top-level ``name``/``user_name``, then the
    same inside ``all_claims``, then ``given_name`` + ``family_name`` joined. None of
    them is guaranteed — an account that never set a display name simply has no name,
    and the caller should fall back to the address rather than invent one.
    """
    if not session_data:
        return None, None

    email = session_data.get("email")
    email = email.strip().lower() if isinstance(email, str) and email.strip() else None

    claims = session_data.get("all_claims") or {}
    name = (
        session_data.get("name")
        or session_data.get("user_name")
        or claims.get("name")
        or claims.get("preferred_username")
    )
    if not name:
        parts = [claims.get("given_name"), claims.get("family_name")]
        joined = " ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
        name = joined or None
    if isinstance(name, str):
        name = name.strip() or None
    else:
        name = None

    return email, name
