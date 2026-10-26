"""Chat-log CRUD + cross-turn evidence memory for the Knowledge Service's own
chat endpoint (see claude-logs/plan/knowledge-owned-chat.md).

Ported from fs-agent-chat/chat_utils/db/chat_logs.py, reshaped for
knowledge_chat_logs (single-service table — no chatbot_id/agent_id/chat_id,
"query"/"response" instead of "user_query"/"gpt_response") and this repo's
async_session + retry_db_connection conventions. Every function degrades
(swallow exceptions, return empty/None) rather than raising, matching
utils/files/retrieval_utils.py's service-layer convention — a DB hiccup
degrades the chat turn's memory, it must not 500 the whole turn.
"""

import json
import uuid
from typing import Any, Dict, List, Optional

from sqlmodel import desc, select

from utils.db.session import async_session
from utils.db.retry_utils import retry_db_connection
from utils.logger import logger
from utils.models.db_tables import KnowledgeChatLogs, KnowledgeChatSessions
from chat.db.sessions import get_or_create_session, touch_session


async def get_recent_evidence(session_id: str, valid_turns: int = 2) -> list:
    """Return knowledge_evidence groups from the last ``valid_turns`` turns that
    actually retrieved documents.

    Walks the session's KnowledgeChatLogs rows newest->oldest, parses
    ``service_data["knowledge_evidence"]`` (one level shallower than the
    Orchestrator's ``service_data["knowledge"]["knowledge_evidence"]`` — this
    table is knowledge-only, not multi-service), and collects groups from the
    most recent ``valid_turns`` rows whose evidence is non-empty (>=1 group
    with >=1 document). No-retrieval rows are skipped, not counted — so a
    follow-up that retrieved nothing doesn't consume a slot, and turn 3 can
    reach past an empty turn 2 to turn 1's evidence.

    Newest-first across turns. Returns ``[]`` for missing/legacy/malformed
    rows or any error. Defensive per row: one unparseable row never breaks
    the others.
    """
    if not session_id or valid_turns <= 0:
        return []
    try:
        async with async_session() as session:
            statement = (
                select(KnowledgeChatLogs)
                .where(KnowledgeChatLogs.session_id == session_id)
                .order_by(desc(KnowledgeChatLogs.created_at))
            )
            result = await session.execute(statement)
            rows = result.scalars().all()
    except Exception as e:
        logger.error(f"Error fetching recent evidence for session {session_id}: {e}")
        return []

    collected: list = []
    turns_used = 0
    for row in rows:
        if turns_used >= valid_turns:
            break
        raw = getattr(row, "service_data", None)
        if not raw:
            continue
        try:
            service_data = json.loads(raw) if isinstance(raw, str) else raw
            evidence = (service_data or {}).get("knowledge_evidence")
        except (json.JSONDecodeError, ValueError, AttributeError, TypeError):
            continue

        if not isinstance(evidence, list):
            continue
        valid_groups = [
            g for g in evidence
            if isinstance(g, dict)
            and isinstance(g.get("documents"), list)
            and g["documents"]
        ]
        if not valid_groups:
            continue  # no-retrieval turn — skip without counting

        collected.extend(valid_groups)
        turns_used += 1

    logger.info(
        f"get_recent_evidence: session={session_id} collected {len(collected)} group(s) "
        f"from {turns_used} valid turn(s) (requested {valid_turns})"
    )
    return collected


@retry_db_connection(max_retries=3)
async def insert_chat_log(
    session_id: str,
    knowledge_id: str,
    user_id: Optional[str],
    query: str,
    response: str,
    refined_query: Optional[str] = None,
    llm_provider: Optional[str] = None,
    model: Optional[str] = None,
    no_of_chunks: int = 4,
    service_data: Optional[Dict[str, Any]] = None,
    has_attachment: bool = False,
    attachment_url: Optional[str] = None,
    is_answered: Optional[bool] = None,
    source: Optional[str] = None,
    user_email: Optional[str] = None,
    user_name: Optional[str] = None,
    chat_id: Optional[str] = None,
    usage: Optional[Dict[str, Any]] = None,
) -> Optional[KnowledgeChatLogs]:
    """Insert one knowledge_chat_logs row, creating the session first if new.

    Returns the inserted row, or None on failure (swallowed — see module
    docstring). Session existence is ensured via get_or_create_session before
    the log insert, mirroring the donor's session-on-first-message pattern.

    has_attachment/attachment_url are transcript-completeness fields only
    (Vision option 1, see the plan's "Vision input" section) — the turn's
    query is already description-enriched by the caller before it reaches
    this service, so these two fields are never read back into retrieval or
    answer generation.

    is_answered is the RAG answer node's own self-eval (RagAnswerOutput.
    is_answered / the retrieval agent's stop_reason), surfaced by
    chat/rag_node.py as a "yes"/"no" string and converted to bool by the
    caller before it reaches this function.

    source/user_email/user_name (migration 044) describe the *caller*, not the turn,
    so they are passed straight through to the session row rather than stored per
    log line — the surface a conversation came in through and the person who had it
    are properties of the conversation.

    The returned row's chat_id (UUID, migration 025) is what the router
    surfaces as ChatResponse.chat_id — read it via session.refresh(chat_log)
    below, not re-derived.

    chat_id may be supplied by the caller instead of being defaulted by the
    model's uuid4 factory. routers/chat.py does this so the id exists BEFORE
    the graph runs and can be tagged onto the turn's root LangSmith run
    (chat/graph.py), which is what lets the metadata-strategy cost backfill
    key a LangSmith run back to this row. Passing it changes nothing else —
    it is the same value the factory would have produced, just minted earlier.
    """
    try:
        await get_or_create_session(
            session_id,
            knowledge_id,
            user_id,
            source=source,
            user_email=user_email,
            user_name=user_name,
        )

        async with async_session() as session:
            chat_log = KnowledgeChatLogs(
                # Only pass chat_id when given — passing None explicitly would
                # override the uuid4 default_factory with a NULL and violate the
                # column's NOT NULL constraint.
                **({"chat_id": uuid.UUID(str(chat_id))} if chat_id else {}),
                session_id=session_id,
                knowledge_id=knowledge_id,
                user_id=user_id,
                query=query,
                response=response,
                service_data=service_data,
                refined_query=refined_query,
                llm_provider=llm_provider,
                model=model,
                no_of_chunks=no_of_chunks,
                has_attachment=has_attachment,
                attachment_url=attachment_url,
                is_answered=is_answered,
                usage=usage,
            )
            session.add(chat_log)
            await session.commit()
            await session.refresh(chat_log)
            logger.info(f"Inserted knowledge_chat_logs row for session_id={session_id}")

        # Stamp the session's updated_at with this turn. Without it the column stayed
        # NULL forever (touch_session existed but nothing called it) and every reader
        # fell back to created_at, so "last activity" reported when a conversation
        # *started*. Outside the insert's own session block and best-effort by design:
        # a failed bookkeeping bump must not lose a turn that is already committed.
        await touch_session(session_id)
        return chat_log
    except Exception as e:
        logger.error(f"Error inserting chat log for session_id={session_id}: {e}")
        return None


async def get_past_messages(session_id: str, limit: int = 10) -> List[Dict[str, str]]:
    """Return the last ``limit`` turns for session_id as chronological
    {"role": "user"/"assistant", "content": ...} dicts (mirrors
    get_past_conversation's shape).

    Fetches most-recent-first (bounded by limit) then reverses, so recency
    caps the DB read while the returned order stays chronological.
    """
    if not session_id:
        return []
    try:
        async with async_session() as session:
            statement = (
                select(KnowledgeChatLogs)
                .where(KnowledgeChatLogs.session_id == session_id)
                .order_by(desc(KnowledgeChatLogs.created_at))
                .limit(limit)
            )
            result = await session.execute(statement)
            rows = result.scalars().all()

        if not rows:
            logger.info(f"No conversation history found for session_id: {session_id}")
            return []

        rows = list(reversed(rows))  # chronological order
        messages: List[Dict[str, str]] = []
        for row in rows:
            messages.append({"role": "user", "content": row.query})
            messages.append({"role": "assistant", "content": row.response})

        logger.info(f"Retrieved {len(rows)} past turn(s) for session_id={session_id}")
        return messages
    except Exception as e:
        logger.error(f"Error retrieving past messages for session_id={session_id}: {e}")
        return []


async def get_session_transcript(knowledge_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    """Return the full transcript for one session, oldest turn first, as a
    flat ``messages`` list (one row per user/assistant turn-half, matching
    fs-agent-chat's own session-transcript shape) rather than one row per
    query+response exchange — a UI renders chat bubbles per message, not
    per exchange, and this lets both services share one rendering path.

    Each assistant message's evidence is nested as
    ``data.knowledge.{relevant_sources,summaries,detailed_chunks,
    knowledge_evidence}`` — genuinely the same fields the live /chat response
    sends under ``data.knowledge`` (``_persist_turn`` now stores them
    verbatim, at zero extra cost, since they're already sitting on
    ``final_state`` for that turn), not a relabeled stand-in. ``rag_context``
    is the one live field NOT persisted (it's the raw ReAct trace, not
    display data). ``knowledge_evidence`` is kept alongside them for
    completeness, but it is a DIFFERENT, lossier shape — cross-turn-memory
    only (200-char-truncated summaries, no chunk text, global-chunk-scan
    groups carry no summary at all) — so don't use it as a sources-panel
    substitute even though it lives in the same dict. The nesting level
    (``data.knowledge``) matches the live envelope on purpose: a bare
    top-level ``knowledge_evidence`` is the shape that bit the NL2SQL
    migration (stored vs. live envelopes disagreeing on where evidence
    lives).

    show_sources is applied by the caller (routers/chat.py), not here — this
    function always returns the full stored evidence, matching the DB row.

    ``chart_output`` IS included (G16, closed) — it lives in ``service_data``
    alongside the other evidence fields, same read pattern as
    ``relevant_sources``/``summaries``/``detailed_chunks``.

    Deliberately NOT included: ``user_feedback`` — not a column on
    ``knowledge_chat_logs`` (excluded by design — see KnowledgeChatLogs'
    docstring); read back via a separate join/field, not this dict. G13
    (feedback) is tracked separately from this function's G14 sources-panel
    fix in docs/screen-api-flow-document-intelligence.md §10.
    """
    if not knowledge_id or not session_id:
        return None
    try:
        async with async_session() as session:
            session_stmt = select(KnowledgeChatSessions).where(
                KnowledgeChatSessions.session_id == session_id,
                KnowledgeChatSessions.knowledge_id == knowledge_id,
                KnowledgeChatSessions.deleted == False,  # noqa: E712
            )
            session_row = (await session.execute(session_stmt)).scalar_one_or_none()
            if not session_row:
                return None

            logs_stmt = (
                select(KnowledgeChatLogs)
                .where(KnowledgeChatLogs.session_id == session_id)
                .order_by(KnowledgeChatLogs.created_at.asc())
            )
            logs = (await session.execute(logs_stmt)).scalars().all()
    except Exception as e:
        logger.error(f"Error fetching transcript for session_id={session_id}: {e}")
        return None

    messages: List[Dict[str, Any]] = []
    first_query = logs[0].query if logs else None
    for n, log in enumerate(logs, start=1):
        raw = log.service_data
        knowledge_data: Dict[str, Any] = {
            "knowledge_evidence": None,
            "relevant_sources": None,
            "summaries": None,
            "detailed_chunks": None,
            "chart_output": None,
        }
        try:
            service_data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(service_data, dict):
                for key in knowledge_data:
                    knowledge_data[key] = service_data.get(key)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        messages.append({
            "n": n,
            "chat_id": str(log.chat_id),
            "role": "user",
            "message": log.query,
            "created_at": log.created_at,
            "has_attachment": log.has_attachment,
            "attachment_url": log.attachment_url,
        })
        messages.append({
            "n": n,
            "chat_id": str(log.chat_id),
            "role": "assistant",
            "message": log.response,
            "created_at": log.created_at,
            "is_answered": log.is_answered,
            "refined_query": log.refined_query,
            "llm_provider": log.llm_provider,
            "model": log.model,
            "user_feedback": log.feedback,
            "usage": log.usage,
            "data": {"knowledge": knowledge_data},
        })

    title = session_row.session_title or (first_query[:120] if first_query else None)
    return {
        "session_id": session_row.session_id,
        "knowledge_id": knowledge_id,
        "user_id": session_row.user_id,
        # Origin + person (migration 044). Read straight off the session row so a
        # reopened transcript names the same caller the list did, with no second
        # lookup and no chance of the two screens disagreeing.
        "user_email": session_row.user_email,
        "user_name": session_row.user_name,
        "source": session_row.source,
        "title": title,
        "created_at": session_row.created_at,
        "updated_at": session_row.updated_at,
        "exchange_count": len(logs),
        "messages": messages,
    }


@retry_db_connection(max_retries=3)
async def set_message_feedback(
    knowledge_id: str,
    session_id: str,
    chat_id: str,
    feedback: Optional[str],
) -> bool:
    """Set (or clear, with ``feedback=None``) 👍/👎 on one turn (G13).

    Scoped by knowledge_id + session_id + chat_id together — not chat_id
    alone — so a caller can't flip feedback on a turn from a session/
    knowledge base it doesn't have the path segments for, even though
    chat_id itself is already globally unique.

    Returns False (not True + a swallowed exception) when the row doesn't
    match all three, so the router can 404 rather than silently no-op —
    this is deliberately NOT the swallow-and-degrade posture the rest of
    this module uses for read-path/cross-turn-memory helpers: a feedback
    write that silently does nothing is exactly the G10 failure mode
    (a control that 200s and saves nothing).
    """
    try:
        chat_uuid = uuid.UUID(chat_id)
    except (ValueError, AttributeError, TypeError):
        return False

    async with async_session() as session:
        statement = select(KnowledgeChatLogs).where(
            KnowledgeChatLogs.chat_id == chat_uuid,
            KnowledgeChatLogs.session_id == session_id,
            KnowledgeChatLogs.knowledge_id == knowledge_id,
        )
        result = await session.execute(statement)
        log = result.scalar_one_or_none()
        if not log:
            return False
        log.feedback = feedback
        session.add(log)
        await session.commit()
        return True
