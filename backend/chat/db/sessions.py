"""Session CRUD for the Knowledge Service's own chat endpoint.

Mirrors the session-existence-check shape implicit in
fs-agent-chat/chat_utils/db/chat_logs.py's insert path (a chat_log row always
belongs to a session), but this service has no separate widget/agent session
creation call, so get_or_create_session is the single entry point: the first
insert_chat_log call for a new session_id creates the row on the fly.

Degrades rather than raises on failure, matching this repo's service-layer
convention (utils/files/retrieval_utils.py: swallow exceptions, return
empty/None so the chat turn can still proceed with degraded history rather
than a 500).
"""

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import and_, exists, func, or_
from sqlmodel import select

from utils.db.session import async_session
from utils.db.retry_utils import retry_db_connection
from utils.logger import logger
from utils.models.db_tables import KnowledgeChatLogs, KnowledgeChatSessions, KnowledgeConfig


@retry_db_connection(max_retries=3)
async def get_or_create_session(
    session_id: str,
    knowledge_id: str,
    user_id: Optional[str] = None,
    source: Optional[str] = None,
    user_email: Optional[str] = None,
    user_name: Optional[str] = None,
) -> Optional[KnowledgeChatSessions]:
    """Ensure a knowledge_chat_sessions row exists for session_id, return it.

    ``source`` ("widget" | "dashboard" | "api") and ``user_email``/``user_name``
    (migration 044) are facts about the caller that only the request knows, so they
    are recorded here at write time rather than reconstructed later — see the
    KnowledgeChatSessions docstring for why identity cannot be resolved after the
    fact in this service.

    On an existing row these three are treated as *fill-if-empty*, never overwrite:
      - a session created before migration 044 gains its origin and person on the
        next turn somebody takes in it, instead of staying anonymous forever;
      - a value already recorded is never clobbered by a later turn that happens to
        arrive without one (e.g. the same session continued through the public
        widget after starting signed-in), so the first, best-known answer wins.

    Returns None on failure so callers can proceed without hard-failing the
    turn (the row is a bookkeeping convenience, not required for retrieval).
    """
    try:
        async with async_session() as session:
            statement = select(KnowledgeChatSessions).where(
                KnowledgeChatSessions.session_id == session_id
            )
            result = await session.execute(statement)
            existing = result.scalar_one_or_none()
            if existing:
                # Backfill only what is still unknown; commit only if something changed.
                patch = {"source": source, "user_email": user_email, "user_name": user_name}
                changed = False
                for field, value in patch.items():
                    if value and not getattr(existing, field, None):
                        setattr(existing, field, value)
                        changed = True
                if changed:
                    session.add(existing)
                    await session.commit()
                    await session.refresh(existing)
                    logger.info(
                        f"Backfilled origin/identity on knowledge_chat_sessions "
                        f"session_id={session_id}"
                    )
                return existing

            new_session = KnowledgeChatSessions(
                session_id=session_id,
                knowledge_id=knowledge_id,
                user_id=user_id,
                source=source,
                user_email=user_email,
                user_name=user_name,
            )
            session.add(new_session)
            await session.commit()
            await session.refresh(new_session)
            logger.info(
                f"Created knowledge_chat_sessions row for session_id={session_id} "
                f"source={source}"
            )
            return new_session
    except Exception as e:
        logger.error(f"Error in get_or_create_session for session_id={session_id}: {e}")
        return None


@retry_db_connection(max_retries=3)
async def touch_session(session_id: str) -> None:
    """Bump updated_at on an existing session row. Best-effort, no-op on failure."""
    try:
        async with async_session() as session:
            statement = select(KnowledgeChatSessions).where(
                KnowledgeChatSessions.session_id == session_id
            )
            result = await session.execute(statement)
            existing = result.scalar_one_or_none()
            if not existing:
                return
            existing.updated_at = datetime.now()
            session.add(existing)
            await session.commit()
    except Exception as e:
        logger.error(f"Error touching session_id={session_id}: {e}")


# Sort keys accepted by list_sessions, mapped to their SQL expression below.
# Published as a closed set so an unknown value falls back to the default rather
# than reaching the query builder.
_SORT_KEYS = ("last_activity", "questions", "title", "user")


@retry_db_connection(max_retries=3)
async def list_sessions(
    knowledge_id: str,
    page: int = 1,
    page_size: int = 20,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    q: Optional[str] = None,
    user_id: Optional[str] = None,
    source: Optional[str] = None,
    exclude_user_id: Optional[str] = None,
    sort: Optional[str] = None,
    order: str = "desc",
) -> Dict[str, Any]:
    """Page through a knowledge base's sessions, most recently active first.

    ``session_title`` is a real column but nothing in this service writes it
    yet (see the class docstring's TODO — no caller sets it), so every
    session's display title falls back to its first turn's query, truncated.
    That fallback is computed here per row rather than stored, so it always
    reflects the actual first turn even if session_title is later wired up
    and a UI mixes rows from before/after that change.

    ``q`` searches session_title AND every turn's query/response text via a
    correlated EXISTS — a session matches if any turn in it mentions the
    term, since that is what "search my conversations" means to a user, not
    "search session titles" (which are usually null).

    ``source`` filters by where the conversation came from. Three of its values are
    the ``source`` column verbatim — ``widget``, ``dashboard``, ``api`` — and the
    fourth, ``shared``, is *derived*: a dashboard conversation held by somebody who
    is not the collection's current owner (``knowledge_config.owner_id``, always an
    email in this service). Ownership can change after a conversation happened, so
    that comparison is made here at read time rather than frozen into the column.
    A row written before migration 044 has no ``source`` and therefore matches no
    ``source`` filter at all; it still appears in an unfiltered listing.

    ``exclude_user_id`` drops one person's own threads — matched against
    ``user_id`` *or* ``user_email`` (case-insensitively), because callers legitimately
    hold either form of the identifier: a session's ``user_id`` is an account uuid,
    while a collection's owner is identified by email everywhere else in this
    service.

    ``sort`` is one of ``last_activity`` (default), ``questions``, ``title``,
    ``user``; ``order`` is ``asc`` or ``desc``. Sorting and filtering both happen in
    SQL so a page is a page of the *filtered* result and ``total_count`` is that
    result's real size — the caller never has to re-count or re-order what it was
    handed.

    Returns ``{"sessions": [...], "total_count", "current_page", "total_pages"}``.
    Degrades to an empty page on any DB failure, matching this module's
    existing swallow-and-continue convention.
    """
    page = max(page, 1)
    page_size = max(1, min(page_size, 100))

    try:
        async with async_session() as session:
            # Per-session aggregates as correlated subqueries rather than a query
            # per row: one round trip for the whole page, and both become sortable
            # SQL expressions instead of post-hoc Python.
            exchange_count_sq = (
                select(func.count())
                .select_from(KnowledgeChatLogs)
                .where(KnowledgeChatLogs.session_id == KnowledgeChatSessions.session_id)
                .correlate(KnowledgeChatSessions)
                .scalar_subquery()
            )
            first_query_sq = (
                select(KnowledgeChatLogs.query)
                .where(KnowledgeChatLogs.session_id == KnowledgeChatSessions.session_id)
                .correlate(KnowledgeChatSessions)
                .order_by(KnowledgeChatLogs.created_at.asc())
                .limit(1)
                .scalar_subquery()
            )

            conditions = [
                KnowledgeChatSessions.knowledge_id == knowledge_id,
                KnowledgeChatSessions.deleted == False,  # noqa: E712 - SQLAlchemy needs `== False`, not `is False`
            ]
            if user_id:
                conditions.append(KnowledgeChatSessions.user_id == user_id)
            if from_dt:
                conditions.append(KnowledgeChatSessions.created_at >= from_dt)
            if to_dt:
                conditions.append(KnowledgeChatSessions.created_at <= to_dt)
            if q:
                like = f"%{q}%"
                log_match = exists().where(
                    KnowledgeChatLogs.session_id == KnowledgeChatSessions.session_id,
                    or_(
                        KnowledgeChatLogs.query.ilike(like),
                        KnowledgeChatLogs.response.ilike(like),
                    ),
                )
                conditions.append(or_(KnowledgeChatSessions.session_title.ilike(like), log_match))

            if source:
                source = source.strip().lower()
                if source == "shared":
                    owner_email = await _resolve_owner_email(session, knowledge_id)
                    conditions.append(KnowledgeChatSessions.source == "dashboard")
                    conditions.append(KnowledgeChatSessions.user_email.isnot(None))
                    if owner_email:
                        conditions.append(
                            func.lower(KnowledgeChatSessions.user_email) != owner_email
                        )
                else:
                    conditions.append(KnowledgeChatSessions.source == source)

            if exclude_user_id:
                needle = exclude_user_id.strip().lower()
                # IS DISTINCT FROM semantics by hand: a NULL email/user_id must NOT
                # be excluded, and a bare `!=` against NULL is NULL (i.e. drops the
                # row) in SQL.
                conditions.append(
                    and_(
                        or_(
                            KnowledgeChatSessions.user_id.is_(None),
                            func.lower(KnowledgeChatSessions.user_id) != needle,
                        ),
                        or_(
                            KnowledgeChatSessions.user_email.is_(None),
                            func.lower(KnowledgeChatSessions.user_email) != needle,
                        ),
                    )
                )

            where_clause = and_(*conditions)

            total_count = (
                await session.execute(
                    select(func.count()).select_from(KnowledgeChatSessions).where(where_clause)
                )
            ).scalar_one()

            # Default: most recently active first — a session with turns sorts by
            # its last turn, not by when it was opened.
            last_activity = func.coalesce(
                KnowledgeChatSessions.updated_at, KnowledgeChatSessions.created_at
            )
            sort_key = (sort or "last_activity").strip().lower()
            if sort_key not in _SORT_KEYS:
                sort_key = "last_activity"
            order_key = {
                "last_activity": last_activity,
                "questions": exchange_count_sq,
                "title": func.coalesce(KnowledgeChatSessions.session_title, first_query_sq),
                "user": func.coalesce(
                    KnowledgeChatSessions.user_name,
                    KnowledgeChatSessions.user_email,
                    KnowledgeChatSessions.user_id,
                ),
            }[sort_key]
            direction = order_key.asc() if str(order).strip().lower() == "asc" else order_key.desc()

            rows = (
                await session.execute(
                    select(
                        KnowledgeChatSessions,
                        exchange_count_sq.label("exchange_count"),
                        first_query_sq.label("first_query"),
                    )
                    .where(where_clause)
                    # Tie-break on the primary key so two rows with equal sort keys
                    # keep a stable relative order across pages (without it, page 2
                    # can repeat or skip a row the DB happened to order differently).
                    .order_by(direction, KnowledgeChatSessions.session_id.asc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()

            sessions_out = []
            for row, exchange_count, first_query in rows:
                title = row.session_title or (first_query[:120] if first_query else None)
                sessions_out.append({
                    "session_id": row.session_id,
                    "title": title,
                    "exchange_count": exchange_count or 0,
                    "user_id": row.user_id,
                    "user_email": row.user_email,
                    "user_name": row.user_name,
                    "source": row.source,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                })

        total_pages = max(1, (total_count + page_size - 1) // page_size) if total_count else 1
        return {
            "sessions": sessions_out,
            "total_count": total_count,
            "current_page": page,
            "total_pages": total_pages,
        }
    except Exception as e:
        logger.error(f"Error listing sessions for knowledge_id={knowledge_id}: {e}")
        return {"sessions": [], "total_count": 0, "current_page": page, "total_pages": 1}


async def _resolve_owner_email(session, knowledge_id: str) -> Optional[str]:
    """Lowercased ``knowledge_config.owner_id`` for this collection, or None.

    Read on the caller's open session so the ``source=shared`` split costs one extra
    SELECT per request, not a second connection. ``owner_id`` is always an email in
    this service (utils/auth/auth_utils.py::extract_owner_id_from_session), which is
    why it can be compared to a session's ``user_email`` directly.
    """
    try:
        owner_id = (
            await session.execute(
                select(KnowledgeConfig.owner_id).where(
                    KnowledgeConfig.knowledge_id == knowledge_id
                )
            )
        ).scalar_one_or_none()
        return owner_id.strip().lower() if isinstance(owner_id, str) else None
    except Exception as e:
        logger.warning(f"Could not resolve owner email for knowledge_id={knowledge_id}: {e}")
        return None


@retry_db_connection(max_retries=3)
async def soft_delete_session(knowledge_id: str, session_id: str) -> bool:
    """Soft-delete a session (sets ``deleted=True``). Returns False if the
    session does not exist, belongs to a different knowledge_id, or is
    already deleted — True only on an actual state change."""
    try:
        async with async_session() as session:
            statement = select(KnowledgeChatSessions).where(
                KnowledgeChatSessions.session_id == session_id,
                KnowledgeChatSessions.knowledge_id == knowledge_id,
            )
            result = await session.execute(statement)
            existing = result.scalar_one_or_none()
            if not existing or existing.deleted:
                return False
            existing.deleted = True
            existing.updated_at = datetime.now()
            session.add(existing)
            await session.commit()
            return True
    except Exception as e:
        logger.error(f"Error soft-deleting session_id={session_id}: {e}")
        return False
