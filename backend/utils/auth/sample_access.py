"""Sample collections: one shared, read-only copy that every signed-in account may open.

A sample ("resumes", "invoices") is an ordinary knowledge base owned by a platform
account and marked with ``knowledge_config.sample_key`` (migration 053). It was
extracted and embedded once, when it was provisioned
(``scripts/provision_sample.py``). A visitor reads it in place — there is no copy
per user, so trying a sample never re-runs extraction, never duplicates vectors,
and costs only the questions that are asked.

Why shared and not a per-user copy (decided 2026-10-25):

  * **Isolation by construction.** Nothing a visitor can call writes to the
    collection. A copy would hand every user the owner's full permission set on
    rows that share S3 objects with the master — a "delete document" on a copy
    would have to be taught not to delete the master's file.
  * **Cost.** A copy is ~24 document rows, their chunks and their vectors per
    visitor, forever. Shared is one copy, and a fix to the sample reaches every
    visitor at once instead of leaving N stale copies behind.

What makes a shared copy safe is that a visitor is NOT given a role. The
owner/editor/viewer matrix (``utils/auth/permissions.py``) grants ``read`` on
every owner-internal route — conversations from everyone, analytics, the share
list — which on a collection opened by thousands of strangers would leak each
visitor's questions to all the others. A visitor instead gets the routes listed in
``SAMPLE_VISITOR_ROUTES`` and nothing else, the same allow-list shape as a
generated API key's content routes: a route added later is refused until someone
decides a visitor should have it.

Inside that allow-list, conversations are per visitor: a visitor lists, opens,
rates, deletes and continues only the threads they started.

Questions are charged to the sample's owner, not to the visitor — a visitor's own
trial is untouched by trying a sample. What bounds the spend instead is the
allowance below: ``SAMPLE_QUESTIONS_PER_DAY`` and ``SAMPLE_QUESTIONS_TOTAL`` per
visitor, and ``SAMPLE_QUESTIONS_GLOBAL_PER_DAY`` across every public visitor.
A verified internal account (``utils.usage.internal_accounts.verified_internal_email``:
a FutureSmart or AI Demos address whose sign-in says ``email_verified``) is
"internal" and unlimited — the same rule that lifts its trial limits. The
allowance is checked and recorded in one transaction under an advisory lock, and
it fails closed: if it cannot be counted, the question is refused.

Generated API keys never reach a sample. A key belongs to its account's own
collections; the sample's API is the owner's, and a visitor who wants an API
creates their own collection.
"""

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request
from sqlalchemy import text

from utils.logger import logger

# (method, route path) -> what the route needs checked for a visitor.
#   "open"     — no further check: the route reads the sample itself.
#   "sessions" — the conversation list; must be filtered to the caller.
#   "session"  — one conversation in the path; must be the caller's.
#   "chat"     — a question; the session in the body must be new or the caller's,
#                and the question spends the caller's allowance.
SAMPLE_VISITOR_ROUTES: Dict[tuple, str] = {
    # The collection and its fields.
    ("GET", "/v1/{knowledge_id}/config"): "open",
    ("GET", "/v1/{knowledge_id}/chat/config"): "open",
    ("GET", "/v1/{knowledge_id}/enrichment"): "open",
    # Documents, the extracted-fields table, one document's detail.
    ("GET", "/v1/{knowledge_id}/documents"): "open",
    ("GET", "/v2/{knowledge_id}/documents"): "open",
    ("GET", "/v1/{knowledge_id}/documents/{object_id}/status"): "open",
    ("GET", "/v1/{knowledge_id}/documents/{object_id}/metadata"): "open",
    ("GET", "/v1/{knowledge_id}/documents/{object_id}/chunks"): "open",
    ("POST", "/v1/{knowledge_id}/documents/summaries"): "open",
    ("POST", "/v1/{knowledge_id}/documents/metadata/all"): "open",
    ("POST", "/v2/{knowledge_id}/documents/metadata/all"): "open",
    ("GET", "/v1/{knowledge_id}/chunks/{chunk_id}"): "open",
    ("GET", "/v1/{knowledge_id}/chunks/{chunk_id}/metadata"): "open",
    ("POST", "/v1/{knowledge_id}/chunks/batch"): "open",
    # Which extraction produced each value — about the sample's documents only.
    ("GET", "/v1/{knowledge_id}/health/enrichment/documents"): "open",
    # Asking, and the visitor's own conversations.
    ("POST", "/v1/{knowledge_id}/chat"): "chat",
    ("GET", "/v1/{knowledge_id}/sessions"): "sessions",
    ("GET", "/v1/{knowledge_id}/sessions/{session_id}"): "session",
    ("DELETE", "/v1/{knowledge_id}/sessions/{session_id}"): "session",
    ("PATCH", "/v1/{knowledge_id}/sessions/{session_id}/messages/{chat_id}/feedback"): "session",
}

SAMPLE_READ_ONLY = "Sample collections are read-only. Create your own collection to change documents, fields or settings."
SAMPLE_OWN_CONVERSATIONS = "On a sample collection you can only see your own conversations."
SAMPLE_QUESTION_LIMIT_ERROR = "sample_question_limit"

# One lock for every visitor: the global daily ceiling is shared, so the check and
# the insert must not interleave across visitors either.
_ALLOWANCE_LOCK_KEY = 0x5A3D_1E01

_SAMPLE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,62}$")


def is_valid_sample_key(sample_key: str) -> bool:
    return bool(_SAMPLE_KEY_PATTERN.match(sample_key or ""))


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class SampleLimits:
    per_day: int
    total: int
    global_per_day: int

    @classmethod
    def from_env(cls) -> "SampleLimits":
        return cls(
            per_day=_int_env("SAMPLE_QUESTIONS_PER_DAY", 20),
            total=_int_env("SAMPLE_QUESTIONS_TOTAL", 100),
            global_per_day=_int_env("SAMPLE_QUESTIONS_GLOBAL_PER_DAY", 1000),
        )


def audience_for(session_data: Optional[Dict[str, Any]]) -> str:
    """"internal" for a verified internal account, "public" for everyone else.

    Read from the auth service's ``/user/me`` claims, never the request body, and by
    the same rule that lifts an account's trial limits: an address on an internal
    domain counts only when its sign-in says ``email_verified`` for that address. An
    unverified sign-up as someone@futuresmart.ai is public.
    """
    from utils.usage.internal_accounts import verified_internal_email

    return "internal" if verified_internal_email(session_data) else "public"


def allowance_refusal(
    *,
    audience: str,
    user_today: int,
    user_total: int,
    global_today: int,
    limits: SampleLimits,
) -> Optional[str]:
    """The sentence that refuses one more question, or None if it may be asked."""
    if audience == "internal":
        return None
    if user_total >= limits.total:
        return (
            f"You have asked all {limits.total} questions this sample allows. "
            "Create your own collection to keep going — your account's own allowance is untouched."
        )
    if user_today >= limits.per_day:
        return (
            f"You have asked {limits.per_day} sample questions today. "
            "The allowance resets at 00:00 UTC, or create your own collection to keep going."
        )
    if global_today >= limits.global_per_day:
        return "The samples have answered as many questions as they can today. Please try again tomorrow."
    return None


def _utc_day_start(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _route_key(request: Request) -> tuple:
    route_path = getattr(request.scope.get("route"), "path", None)
    return (request.method.upper(), route_path)


def visitor_route_kind(request: Request) -> Optional[str]:
    return SAMPLE_VISITOR_ROUTES.get(_route_key(request))


async def _request_body(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _owns_session(row: Dict[str, Any], user_id: Optional[str], email: str) -> bool:
    row_email = (row.get("user_email") or "").strip().lower()
    row_user = row.get("user_id")
    return bool((email and row_email == email) or (user_id and row_user and str(row_user) == str(user_id)))


async def authorize_sample_visitor(
    request: Request,
    knowledge_id: str,
    session_data: Dict[str, Any],
    user_id: Optional[str],
) -> None:
    """Admit a signed-in non-owner to a sample collection, or raise.

    Called by ``require_permission`` for a session caller on a collection whose
    ``sample_key`` is set and which the caller does not own. Every check a visitor
    is subject to lives here, so the whole policy reads in one place.
    """
    # A route can carry the permission dependency twice (router-level and
    # route-level); a question must be counted once.
    if getattr(request.state, "sample_visitor", False):
        return

    kind = visitor_route_kind(request)
    if kind is None:
        raise HTTPException(status_code=403, detail=SAMPLE_READ_ONLY)

    email = (session_data.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=401, detail="Could not resolve caller identity")

    if kind == "sessions":
        requested = (request.query_params.get("user_id") or "").strip()
        if not requested or requested.lower() not in {email, str(user_id or "").lower()}:
            raise HTTPException(status_code=403, detail=SAMPLE_OWN_CONVERSATIONS)

    elif kind == "session":
        session_id = request.path_params.get("session_id")
        row = await get_session_identity(session_id)
        if not row or row.get("knowledge_id") != knowledge_id or not _owns_session(row, user_id, email):
            # 404, not 403: whether somebody else's conversation exists is itself
            # something a visitor must not learn.
            raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    elif kind == "chat":
        body = await _request_body(request)
        session_id = body.get("session_id") or None
        if session_id:
            row = await get_session_identity(session_id)
            if row and (row.get("knowledge_id") != knowledge_id or not _owns_session(row, user_id, email)):
                raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
        await spend_sample_question(knowledge_id, email, audience_for(session_data), session_id)

    request.state.sample_visitor = True
    logger.info(f"Sample visitor admitted: knowledge={knowledge_id} route={_route_key(request)} user={email}")


# --- DB ---------------------------------------------------------------------------
# Module-level functions so the tests can stub them; each opens its own session.


async def get_session_identity(session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Who a chat session belongs to: its collection, account id and email."""
    if not session_id:
        return None
    from sqlmodel import select

    from utils.db.session import async_session
    from utils.models.db_tables import KnowledgeChatSessions

    async with async_session() as session:
        result = await session.execute(
            select(
                KnowledgeChatSessions.knowledge_id,
                KnowledgeChatSessions.user_id,
                KnowledgeChatSessions.user_email,
            ).where(KnowledgeChatSessions.session_id == session_id)
        )
        row = result.first()
    if not row:
        return None
    return {"knowledge_id": row[0], "user_id": row[1], "user_email": row[2]}


async def spend_sample_question(knowledge_id: str, email: str, audience: str, session_id: Optional[str]) -> None:
    """Count the visitor's questions and record this one, or refuse it with 429.

    Fails closed: an error while counting refuses the question (503) rather than
    letting an uncounted one through.
    """
    limits = SampleLimits.from_env()
    day_start = _utc_day_start()
    try:
        from utils.db.session import async_session

        async with async_session() as session:
            async with session.begin():
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ALLOWANCE_LOCK_KEY})
                counts = (
                    await session.execute(
                        text(
                            "SELECT"
                            " count(*) FILTER (WHERE user_email = :email AND created_at >= :day) AS user_today,"
                            " count(*) FILTER (WHERE user_email = :email) AS user_total,"
                            " count(*) FILTER (WHERE audience = 'public' AND created_at >= :day) AS global_today"
                            " FROM knowledge_sample_questions"
                            " WHERE user_email = :email OR created_at >= :day"
                        ),
                        {"email": email, "day": day_start},
                    )
                ).one()
                refusal = allowance_refusal(
                    audience=audience,
                    user_today=int(counts.user_today),
                    user_total=int(counts.user_total),
                    global_today=int(counts.global_today),
                    limits=limits,
                )
                if refusal is None:
                    await session.execute(
                        text(
                            "INSERT INTO knowledge_sample_questions"
                            " (knowledge_id, user_email, audience, session_id)"
                            " VALUES (:knowledge_id, :email, :audience, :session_id)"
                        ),
                        {"knowledge_id": knowledge_id, "email": email, "audience": audience, "session_id": session_id},
                    )
    except Exception as exc:
        logger.error(f"Sample allowance could not be counted for {email}: {exc}")
        raise HTTPException(status_code=503, detail="The sample is unavailable right now. Please try again shortly.")

    if refusal is not None:
        logger.info(f"Sample question refused: user={email} knowledge={knowledge_id}")
        raise HTTPException(
            status_code=429,
            detail={"error": SAMPLE_QUESTION_LIMIT_ERROR, "message": refusal},
        )


async def sample_allowance_for(session_data: Dict[str, Any]) -> Dict[str, Any]:
    """What the visitor has used and may still ask, for the sample screen."""
    limits = SampleLimits.from_env()
    audience = audience_for(session_data)
    email = (session_data.get("email") or "").strip().lower()
    from utils.db.session import async_session

    async with async_session() as session:
        counts = (
            await session.execute(
                text(
                    "SELECT"
                    " count(*) FILTER (WHERE created_at >= :day) AS user_today,"
                    " count(*) AS user_total"
                    " FROM knowledge_sample_questions WHERE user_email = :email"
                ),
                {"email": email, "day": _utc_day_start()},
            )
        ).one()
    unlimited = audience == "internal"
    return {
        "audience": audience,
        "unlimited": unlimited,
        "questions_today": int(counts.user_today),
        "questions_total": int(counts.user_total),
        "daily_limit": None if unlimited else limits.per_day,
        "total_limit": None if unlimited else limits.total,
    }


async def list_samples() -> List[Dict[str, Any]]:
    """Every live sample: its collection, name, description and document count."""
    from utils.db.session import async_session

    async with async_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT k.knowledge_id, k.sample_key, k.collection_name, k.service_description,"
                    " (SELECT count(*) FROM document d WHERE d.knowledge_id = k.knowledge_id"
                    "  AND d.deleted = false) AS document_count"
                    " FROM knowledge_config k"
                    " WHERE k.sample_key IS NOT NULL AND k.deleted = false"
                    " ORDER BY k.sample_key"
                )
            )
        ).all()
    return [
        {
            "knowledge_id": row.knowledge_id,
            "sample_key": row.sample_key,
            "collection_name": row.collection_name,
            "service_description": row.service_description,
            "document_count": int(row.document_count or 0),
        }
        for row in rows
    ]


async def set_sample_key(knowledge_id: str, sample_key: Optional[str]) -> bool:
    """Mark (or unmark, with None) a collection as the live copy of a sample."""
    from utils.db.session import async_session

    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                text(
                    "UPDATE knowledge_config SET sample_key = :sample_key, updated_at = now()"
                    " WHERE knowledge_id = :knowledge_id AND deleted = false"
                ),
                {"knowledge_id": knowledge_id, "sample_key": sample_key},
            )
    return bool(result.rowcount)


async def sample_adoption(days: int) -> List[Dict[str, Any]]:
    """Questions and distinct visitors per sample per day, newest first."""
    from utils.db.session import async_session

    async with async_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT q.knowledge_id, k.sample_key, q.audience,"
                    " date_trunc('day', q.created_at) AS day,"
                    " count(*) AS questions, count(DISTINCT q.user_email) AS visitors"
                    " FROM knowledge_sample_questions q"
                    " LEFT JOIN knowledge_config k ON k.knowledge_id = q.knowledge_id"
                    " WHERE q.created_at >= now() - make_interval(days => :days)"
                    " GROUP BY 1, 2, 3, 4 ORDER BY day DESC, sample_key"
                ),
                {"days": days},
            )
        ).all()
    return [
        {
            "knowledge_id": row.knowledge_id,
            "sample_key": row.sample_key,
            "audience": row.audience,
            "day": row.day.date().isoformat(),
            "questions": int(row.questions),
            "visitors": int(row.visitors),
        }
        for row in rows
    ]
