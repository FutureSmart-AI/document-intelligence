"""Persistence helper for the universal access-audit log.

Writes one row to `knowledge_audit_log` per audited request. Called only from the
`audit_access` dependency, which already wraps it in try/except — this layer
adds its own guard so an audit failure can never break the request.
"""

from typing import Optional

from utils.db.session import async_session
from utils.models.db_tables import KnowledgeAuditLog
from utils.logger import logger


async def log_access_event(
    user_id: Optional[str],
    owner_id: Optional[str],
    knowledge_id: Optional[str],
    action: str,
    method: Optional[str] = None,
    path: Optional[str] = None,
) -> bool:
    """Persist one standardized audit row. Returns True on success, False otherwise."""
    try:
        async with async_session() as session:
            entry = KnowledgeAuditLog(
                user_id=user_id,
                owner_id=owner_id,
                knowledge_id=knowledge_id,
                action=action,
                method=method,
                path=path,
            )
            session.add(entry)
            await session.commit()
        return True
    except Exception as e:
        logger.error(f"log_access_event: failed to persist audit row: {e}")
        return False
