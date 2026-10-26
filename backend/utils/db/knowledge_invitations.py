"""DB ops for knowledge-base sharing (``knowledge_invitations`` table,
migration 037).

Ported from fs-agent-chat's ``chat_utils/db/agent_invitations.py`` (see
docs/plan/access-and-sharing/archive/design-share-invitations.md) — re-keyed by ``knowledge_id``.
This module is what ``utils/auth/dependencies.py::require_permission``'s
non-owner branch now calls locally, replacing the HTTP call this service
used to make to fs-agent-chat's ``GET /v1/auth/check-access``.

Simplifications vs. the source (both are consequences of this service's
simpler ownership model — see ``KnowledgeInvitation``'s docstring):
  - No owner_email resolution step. The caller already knows knowledge_id;
    this service's ``KnowledgeConfig.owner_id`` is always the owner's email
    directly, so `invited_by_email` is just "whoever is calling", not a
    looked-up value.
  - No ``changed_fields`` audit trail on role updates — this service's
    universal request-level ``knowledge_audit_log`` already covers "who did
    what, when" for every mutating endpoint.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import and_, select

from utils.db.retry_utils import retry_db_connection
from utils.db.session import async_session
from utils.logger import logger
from utils.models.db_tables import KnowledgeConfig, KnowledgeInvitation
from utils.tracing import track_time


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _to_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


@track_time("DB: Create Knowledge Invitations")
@retry_db_connection(max_retries=3)
async def create_invitations(
    knowledge_id: str,
    invited_by_email: str,
    invitations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Create one or more invitations for ``knowledge_id``.

    Each entry in ``invitations`` is ``{"email": ..., "role": ...}``. A
    duplicate ``(knowledge_id, invited_user_email)`` (already invited) fails
    that one entry via the unique constraint rather than the whole batch —
    mirrors the source's per-row partial-success contract.

    Returns ``failed_emails`` (bare addresses, kept for backwards
    compatibility) *and* ``failures`` (``{email, reason}``) — the UI's Share
    flow reads the reason to surface why a
    given address wasn't granted access; a bare address list alone can't
    tell "already invited" apart from any other failure.
    """
    invited_users: List[Dict[str, Any]] = []
    failed_emails: List[str] = []
    failures: List[Dict[str, str]] = []
    acting_user = _normalize_email(invited_by_email)

    async with async_session() as session:
        for invite in invitations:
            raw_email = invite.get("email")
            email = _normalize_email(raw_email) if raw_email else raw_email
            role = invite.get("role") or "viewer"
            if not email:
                failed_emails.append(raw_email or "")
                failures.append({"email": raw_email or "", "reason": "missing email"})
                continue
            row = KnowledgeInvitation(
                knowledge_id=knowledge_id,
                invited_user_email=email,
                invited_by_email=acting_user,
                role=role,
            )
            session.add(row)
            try:
                await session.commit()
                invited_users.append({"email": email, "role": role})
                logger.info(
                    f"Invitation created knowledge_id={knowledge_id} email={email} role={role} by={acting_user}"
                )
            except IntegrityError as e:
                await session.rollback()
                failed_emails.append(email)
                failures.append({"email": email, "reason": "already invited"})
                logger.warning(f"Invitation insert failed (duplicate?) knowledge_id={knowledge_id} email={email}: {e}")
            except Exception as e:
                await session.rollback()
                failed_emails.append(email)
                failures.append({"email": email, "reason": "unexpected error"})
                logger.error(f"Invitation insert error knowledge_id={knowledge_id} email={email}: {e}")

    return {"invited_users": invited_users, "failed_emails": failed_emails, "failures": failures}


@track_time("DB: List Knowledge Invitations")
@retry_db_connection(max_retries=3)
async def list_invitations(knowledge_id: str) -> List[Dict[str, Any]]:
    async with async_session() as session:
        statement = select(KnowledgeInvitation).where(
            KnowledgeInvitation.knowledge_id == knowledge_id
        ).order_by(KnowledgeInvitation.created_at.desc())
        result = await session.execute(statement)
        rows = result.scalars().all()

    logger.info(f"Retrieved {len(rows)} invitations for knowledge_id={knowledge_id}")
    return [
        {
            "email": row.invited_user_email,
            "role": row.role,
            "invited_at": _to_iso(row.created_at),
        }
        for row in rows
    ]


@track_time("DB: Delete Knowledge Invitation")
@retry_db_connection(max_retries=3)
async def delete_invitation(knowledge_id: str, invited_email: str) -> bool:
    """Revoke an invitation. Hard delete — no row survives to carry
    ``updated_by``, so the revocation is recorded via the log line above
    (plus, independently, whatever the request already wrote to
    ``knowledge_audit_log``)."""
    email = _normalize_email(invited_email)
    async with async_session() as session:
        statement = select(KnowledgeInvitation).where(
            and_(
                KnowledgeInvitation.knowledge_id == knowledge_id,
                func.lower(KnowledgeInvitation.invited_user_email) == email,
            )
        )
        result = await session.execute(statement)
        row = result.scalar_one_or_none()
        if not row:
            return False
        revoked_role = row.role
        await session.delete(row)
        await session.commit()

    logger.info(
        f"Invitation revoked knowledge_id={knowledge_id} email={email} revoked_role={revoked_role}"
    )
    return True


@track_time("DB: Update Knowledge Invitation Role")
@retry_db_connection(max_retries=3)
async def update_invitation_role(
    knowledge_id: str,
    invited_email: str,
    role: str,
    actor: Optional[str] = None,
) -> bool:
    email = _normalize_email(invited_email)
    async with async_session() as session:
        statement = select(KnowledgeInvitation).where(
            and_(
                KnowledgeInvitation.knowledge_id == knowledge_id,
                func.lower(KnowledgeInvitation.invited_user_email) == email,
            )
        )
        result = await session.execute(statement)
        row = result.scalar_one_or_none()
        if not row:
            return False
        old_role = row.role
        row.role = role
        row.updated_by = _normalize_email(actor) if actor else None
        row.updated_at = datetime.now(timezone.utc)
        session.add(row)
        await session.commit()

    logger.info(
        f"Invitation role updated knowledge_id={knowledge_id} email={email} "
        f"role={old_role}->{role} by={actor}"
    )
    return True


@track_time("DB: List Shared Knowledge")
@retry_db_connection(max_retries=3)
async def list_shared_knowledge(user_email: str) -> List[Dict[str, Any]]:
    """Reverse lookup: knowledge bases ``user_email`` has been invited to
    (not owns) — the missing half of this table's discovery surface.

    Mirrors fs-agent-chat's ``agent_registry/db/agent_crud.py::
    list_shared_agents`` (backing ``GET /agents/shared``), which the original
    port (docs/plan/access-and-sharing/) never carried over. Without it,
    an invited editor/viewer passes every ``require_permission`` check on a
    shared knowledge base but has no way to find it — ``GET /v1``
    (``routers/knowledge_management.py::list_knowledge``) only ever lists
    collections the caller owns. See decisions.md, 2026-08-25.
    """
    if not user_email:
        return []
    email = _normalize_email(user_email)
    async with async_session() as session:
        statement = (
            select(KnowledgeInvitation, KnowledgeConfig)
            .join(
                KnowledgeConfig,
                KnowledgeConfig.knowledge_id == KnowledgeInvitation.knowledge_id,
            )
            .where(
                and_(
                    func.lower(KnowledgeInvitation.invited_user_email) == email,
                    KnowledgeConfig.deleted == False,
                )
            )
            .order_by(KnowledgeInvitation.created_at.desc())
        )
        result = await session.execute(statement)
        rows = result.all()

    logger.info(f"Retrieved {len(rows)} shared knowledge bases for user_email={email}")
    return [
        {
            "knowledge_id": invitation.knowledge_id,
            "collection_name": config.collection_name,
            "role": invitation.role,
            "invited_by_email": invitation.invited_by_email,
            "invited_at": _to_iso(invitation.created_at),
        }
        for invitation, config in rows
    ]


@track_time("DB: Get Knowledge Access Role")
@retry_db_connection(max_retries=3)
async def get_invited_role(knowledge_id: str, user_email: str) -> Optional[str]:
    """Resolve a non-owner caller's role for ``knowledge_id``, or ``None`` if
    they have never been invited.

    Owner resolution stays out of this module entirely — the caller
    (``require_permission``) already has a cheaper, local owner fast-path
    (comparing the session email against ``KnowledgeConfig.owner_id``) and
    only reaches this function once that has already come back negative.
    Case-insensitive on the invited email for the same reason as the
    source: invitations created before this normalization existed, or typed
    by a caller in different casing, must still resolve.
    """
    if not user_email:
        return None
    email = _normalize_email(user_email)
    async with async_session() as session:
        statement = select(KnowledgeInvitation.role).where(
            and_(
                KnowledgeInvitation.knowledge_id == knowledge_id,
                func.lower(KnowledgeInvitation.invited_user_email) == email,
            )
        )
        result = await session.execute(statement)
        role = result.scalar_one_or_none()
    return str(role).lower().strip() if role else None
