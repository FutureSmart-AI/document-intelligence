"""CRUD helpers for knowledge_widget_settings — see
docs/plan/access-and-sharing/archive/design-widget-fullscreen.md (Piece 1).
"""

from datetime import datetime
from typing import Any, Dict, Optional

from utils.db.session import async_session
from utils.db.retry_utils import retry_db_connection
from utils.logger import logger
from utils.pydantic_models_knowledge import KnowledgeWidgetSettings
from utils.tracing import track_time


@track_time("DB: Get Widget Settings")
@retry_db_connection(max_retries=3)
async def get_widget_settings(knowledge_id: str) -> Optional[Dict[str, Any]]:
    """Fetch widget branding settings for a knowledge base. Returns None if no
    (non-deleted) row exists — that is the normal default case, not an error."""
    async with async_session() as session:
        row = await session.get(KnowledgeWidgetSettings, knowledge_id)
        if not row or row.deleted:
            return None
        return {
            "heading": row.heading,
            "sub_heading": row.sub_heading,
            "greet_message": row.greet_message,
            "color": row.color,
            "show_sources": row.show_sources,
            "widget_icon": row.widget_icon,
            "widget_avatar": row.widget_avatar,
        }


@track_time("DB: Add Widget Settings")
@retry_db_connection(max_retries=3)
async def add_widget_settings(
    knowledge_id: str,
    heading: Optional[str],
    sub_heading: Optional[str],
    greet_message: Optional[str],
    color: Optional[str],
    show_sources: bool,
    widget_icon: Optional[str],
    widget_avatar: Optional[str],
) -> None:
    async with async_session() as session:
        row = KnowledgeWidgetSettings(
            knowledge_id=knowledge_id,
            heading=heading,
            sub_heading=sub_heading,
            greet_message=greet_message,
            color=color,
            show_sources=show_sources,
            widget_icon=widget_icon,
            widget_avatar=widget_avatar,
        )
        session.add(row)
        await session.commit()
        logger.info(f"Added widget settings for knowledge_id: {knowledge_id}")


@track_time("DB: Update Widget Settings")
@retry_db_connection(max_retries=3)
async def update_widget_settings(
    knowledge_id: str,
    heading: Optional[str] = None,
    sub_heading: Optional[str] = None,
    greet_message: Optional[str] = None,
    color: Optional[str] = None,
    show_sources: Optional[bool] = None,
    widget_icon: Optional[str] = None,
    widget_avatar: Optional[str] = None,
) -> None:
    """Partial update — only non-None fields are written; the rest keep their
    stored value. Mirrors knowledge_chat_config's update convention."""
    async with async_session() as session:
        row = await session.get(KnowledgeWidgetSettings, knowledge_id)
        if not row:
            raise ValueError(f"No widget settings found for knowledge_id: {knowledge_id}")

        if heading is not None:
            row.heading = heading
        if sub_heading is not None:
            row.sub_heading = sub_heading
        if greet_message is not None:
            row.greet_message = greet_message
        if color is not None:
            row.color = color
        if show_sources is not None:
            row.show_sources = show_sources
        if widget_icon is not None:
            row.widget_icon = widget_icon
        if widget_avatar is not None:
            row.widget_avatar = widget_avatar

        row.updated_at = datetime.now()
        await session.commit()
        logger.info(f"Updated widget settings for knowledge_id: {knowledge_id}")


@track_time("DB: Delete Widget Settings")
@retry_db_connection(max_retries=3)
async def delete_widget_settings(knowledge_id: str) -> bool:
    """Soft-delete — mirrors every other table's `deleted` flag convention
    instead of the dashboard source's hard sudo-delete."""
    async with async_session() as session:
        row = await session.get(KnowledgeWidgetSettings, knowledge_id)
        if not row or row.deleted:
            return False
        row.deleted = True
        row.updated_at = datetime.now()
        await session.commit()
        logger.info(f"Deleted widget settings for knowledge_id: {knowledge_id}")
        return True
