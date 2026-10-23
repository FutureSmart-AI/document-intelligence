"""Write-side CRUD for knowledge_chat_config, backing the config endpoints in
routers/chat.py (GET/PUT/DELETE /v1/{knowledge_id}/chat/config).

Deliberately separate from chat/config.py, which is read-and-resolve-only
(the hot path every chat turn calls). This module is the cold-path admin
surface, mirroring how utils/db/knowledge_setting_ops.py sits behind
routers/knowledge_config_new.py.

Error convention differs from chat/db/sessions.py and chat/db/chat_logs.py on
purpose: those swallow exceptions because a failed bookkeeping write must not
break a live chat turn. A failed CONFIG write is the opposite — a PUT that
silently does nothing and still returns 200 is a real bug, so the write paths
here let exceptions propagate for the router to turn into a 500. Only the
read path degrades to None, matching chat/config.py::_get_config_row.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlmodel import select

from utils.db.session import async_session
from utils.db.retry_utils import retry_db_connection
from utils.logger import logger
from utils.models.db_tables import KnowledgeChatConfig, KnowledgeChatNodeConfig

# Fields a caller may set via PUT. knowledge_id (PK) and the timestamps are
# managed here, not accepted from the request body.
WRITABLE_FIELDS = (
    "system_prompt",
    "llm_provider",
    "llm_model",
    "temperature",
    "show_sources",
    "chat_enabled",
    "follow_up",
    "suggested_questions",
    "voice_input_enabled",
    "voice_output_enabled",
    "max_tokens",
    "include_charts",
    "show_raw_response",
    "is_public",
    "allowed_domains",
    "fullscreen_is_public",
    "fullscreen_allowed_domains",
)

# Per-node override columns a caller may set. knowledge_id/node_name (the
# composite PK) and the timestamps are managed here, not accepted from the body.
WRITABLE_NODE_FIELDS = (
    "provider",
    "model",
    "temperature",
    "max_tokens",
    "prompt",
    "enabled",
)


@retry_db_connection(max_retries=3)
async def get_config_row(knowledge_id: str) -> Optional[KnowledgeChatConfig]:
    """Fetch the knowledge_chat_config row, or None if absent / on DB failure.

    Absence of a row is the normal default case (chat falls through to env /
    hardcoded defaults), not an error — see chat/config.py's module docstring.
    """
    if not knowledge_id:
        return None
    try:
        async with async_session() as session:
            statement = select(KnowledgeChatConfig).where(
                KnowledgeChatConfig.knowledge_id == knowledge_id
            )
            result = await session.execute(statement)
            return result.scalar_one_or_none()
    except Exception as e:
        logger.warning(f"[chat_config] Failed to read knowledge_chat_config for {knowledge_id}: {e}")
        return None


@retry_db_connection(max_retries=3)
async def upsert_config_row(knowledge_id: str, updates: Dict[str, Any]) -> KnowledgeChatConfig:
    """Create or partially update the knowledge_chat_config row.

    ``updates`` carries only the fields the caller actually set (None-valued
    keys are dropped by the router before calling). Partial by design: it
    mirrors resolve_chat_config's per-field independence, so setting
    llm_provider alone leaves temperature/system_prompt untouched rather than
    nulling them back to defaults.

    On insert, unset columns take their SQLModel/table defaults (openai /
    gpt-5.4-mini / 0.0 / true / true / true).

    Raises on DB failure — a config write that silently no-ops is a bug, see
    this module's docstring.
    """
    async with async_session() as session:
        statement = select(KnowledgeChatConfig).where(
            KnowledgeChatConfig.knowledge_id == knowledge_id
        )
        result = await session.execute(statement)
        row = result.scalar_one_or_none()

        if row is None:
            row = KnowledgeChatConfig(knowledge_id=knowledge_id, **updates)
            session.add(row)
            logger.info(f"[chat_config] Creating knowledge_chat_config for {knowledge_id} with {sorted(updates)}")
        else:
            for field, value in updates.items():
                setattr(row, field, value)
            row.updated_at = datetime.now()
            session.add(row)
            logger.info(f"[chat_config] Updating knowledge_chat_config for {knowledge_id}: {sorted(updates)}")

        await session.commit()
        await session.refresh(row)
        return row


@retry_db_connection(max_retries=3)
async def list_node_config_rows(knowledge_id: str) -> List[KnowledgeChatNodeConfig]:
    """All per-node override rows for this knowledge_id.

    One query for every node rather than one per node — the GET endpoint
    renders all supported nodes at once. Degrades to [] on failure, matching
    get_config_row: a knowledge base with no node rows is the normal case and
    reads identically to one whose rows could not be loaded.
    """
    if not knowledge_id:
        return []
    try:
        async with async_session() as session:
            statement = select(KnowledgeChatNodeConfig).where(
                KnowledgeChatNodeConfig.knowledge_id == knowledge_id
            )
            result = await session.execute(statement)
            return list(result.scalars().all())
    except Exception as e:
        logger.warning(f"[chat_config] Failed to read node configs for {knowledge_id}: {e}")
        return []


@retry_db_connection(max_retries=3)
async def upsert_node_config_row(
    knowledge_id: str, node_name: str, updates: Dict[str, Any]
) -> KnowledgeChatNodeConfig:
    """Create or partially update one (knowledge_id, node_name) override row.

    Partial in the same sense as upsert_config_row: only the supplied keys are
    written. Note that a null written here is meaningful — it clears the
    override so the field falls back to the knowledge-level config, which is
    distinct from never having set it.

    Raises on DB failure, same reasoning as upsert_config_row.
    """
    async with async_session() as session:
        statement = select(KnowledgeChatNodeConfig).where(
            KnowledgeChatNodeConfig.knowledge_id == knowledge_id,
            KnowledgeChatNodeConfig.node_name == node_name,
        )
        result = await session.execute(statement)
        row = result.scalar_one_or_none()

        if row is None:
            row = KnowledgeChatNodeConfig(
                knowledge_id=knowledge_id, node_name=node_name, **updates
            )
            session.add(row)
            logger.info(
                f"[chat_config] Creating node config {knowledge_id}/{node_name} "
                f"with {sorted(updates)}"
            )
        else:
            for field, value in updates.items():
                setattr(row, field, value)
            row.updated_at = datetime.now()
            session.add(row)
            logger.info(
                f"[chat_config] Updating node config {knowledge_id}/{node_name}: "
                f"{sorted(updates)}"
            )

        await session.commit()
        await session.refresh(row)
        return row


@retry_db_connection(max_retries=3)
async def delete_node_config_row(knowledge_id: str, node_name: str) -> bool:
    """Delete one node's override row, so it inherits the knowledge-level
    config again. Returns True if a row was deleted, False if there was none.
    """
    async with async_session() as session:
        statement = select(KnowledgeChatNodeConfig).where(
            KnowledgeChatNodeConfig.knowledge_id == knowledge_id,
            KnowledgeChatNodeConfig.node_name == node_name,
        )
        result = await session.execute(statement)
        row = result.scalar_one_or_none()
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
        logger.info(f"[chat_config] Deleted node config {knowledge_id}/{node_name}")
        return True


@retry_db_connection(max_retries=3)
async def delete_config_row(knowledge_id: str) -> bool:
    """Delete the row, resetting this knowledge_id to env/hardcoded defaults.

    Returns True if a row was deleted, False if there was none to delete (the
    router reports that as a 404 so a caller can tell "reset" from "nothing
    was configured"). Raises on DB failure, same reasoning as upsert.
    """
    async with async_session() as session:
        statement = select(KnowledgeChatConfig).where(
            KnowledgeChatConfig.knowledge_id == knowledge_id
        )
        result = await session.execute(statement)
        row = result.scalar_one_or_none()
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
        logger.info(f"[chat_config] Deleted knowledge_chat_config for {knowledge_id}")
        return True
