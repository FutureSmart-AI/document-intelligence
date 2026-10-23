"""Widget branding settings — heading/greeting/color/icon/avatar shown by the
embeddable chat widget, one row per knowledge_id.

Ported from fs-agent-dashboard's apis/widget_support.py (there keyed by
chatbot_id, gated by the master API key only). Rekeyed to knowledge_id and
gated by this service's own session/RBAC (require_permission), since this
repo already has owner/editor/viewer roles the dashboard endpoint didn't use.
See docs/plan/access-and-sharing/archive/design-widget-fullscreen.md (Piece 1).
"""

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from utils.auth.dependencies import require_permission
from utils.logger import logger
from utils.db.widget_settings_ops import (
    add_widget_settings,
    delete_widget_settings,
    get_widget_settings,
    update_widget_settings,
)
from utils.widget_config_utils import (
    normalize_url_strings,
    resolve_widget_image,
    validate_hex_color,
    validate_image_file,
)

router = APIRouter()

DEFAULT_WIDGET_SETTINGS = {
    "heading": "Welcome",
    "sub_heading": "Got a Question? Just Ask!",
    "greet_message": "Hello! What's up?",
    "color": "#3a5fe3",
    "show_sources": False,
    "widget_icon": "https://futuresmart-rag-bot.s3.us-east-1.amazonaws.com/default_bot/widget_icon.svg",
    "widget_avatar": "https://futuresmart-rag-bot.s3.us-east-1.amazonaws.com/default_bot/widget_avatar.svg",
}

DEFAULT_WIDGET_ICONS = [
    {"url": "https://futuresmart-rag-bot.s3.us-east-1.amazonaws.com/default_bot/widget_icon.svg"},
    {"url": "https://futuresmart-rag-bot.s3.us-east-1.amazonaws.com/default_icon_1/widget_icon.svg"},
    {"url": "https://futuresmart-rag-bot.s3.us-east-1.amazonaws.com/default_icon_2/widget_icon.svg"},
    {"url": "https://futuresmart-rag-bot.s3.us-east-1.amazonaws.com/default_icon_3/widget_icon.svg"},
    {"url": "https://futuresmart-rag-bot.s3.us-east-1.amazonaws.com/default_icon_4/widget_icon.svg"},
    {"url": "https://futuresmart-rag-bot.s3.us-east-1.amazonaws.com/default_icon_5/widget_icon.svg"},
    {"url": "https://futuresmart-rag-bot.s3.us-east-1.amazonaws.com/default_icon_6/widget_icon.svg"},
]

DEFAULT_WIDGET_AVATARS = [
    {"url": "https://futuresmart-rag-bot.s3.us-east-1.amazonaws.com/default_bot/widget_avatar.svg"},
    {"url": "https://futuresmart-rag-bot.s3.us-east-1.amazonaws.com/default_avatar_1/widget_avatar.svg"},
    {"url": "https://futuresmart-rag-bot.s3.us-east-1.amazonaws.com/default_avatar_2/widget_avatar.svg"},
    {"url": "https://futuresmart-rag-bot.s3.us-east-1.amazonaws.com/default_avatar_3/widget_avatar.svg"},
    {"url": "https://futuresmart-rag-bot.s3.us-east-1.amazonaws.com/default_avatar_4/widget_avatar.svg"},
]


@router.post("/v1/{knowledge_id}/widget-config")
async def add_update_widget_settings(
    knowledge_id: str,
    heading: Optional[str] = Form("Welcome"),
    sub_heading: Optional[str] = Form("Got a Question? Just Ask!"),
    greet_message: Optional[str] = Form("Hello! What's up?"),
    color: Optional[str] = Form("#3a5fe3"),
    show_sources: Optional[bool] = Form(False),
    widget_icon: Optional[UploadFile] = File(None),
    widget_avatar: Optional[UploadFile] = File(None),
    widget_icon_url: Optional[str] = Form(None),
    widget_avatar_url: Optional[str] = Form(None),
    _: Optional[str] = Depends(require_permission("write")),
):
    if color is not None:
        color = await validate_hex_color(color)
    await validate_image_file(widget_icon)
    await validate_image_file(widget_avatar)

    try:
        existing = await get_widget_settings(knowledge_id)

        widget_icon_url, widget_avatar_url = await normalize_url_strings(widget_icon_url, widget_avatar_url)
        widget_icon_content = await resolve_widget_image(widget_icon, widget_icon_url, knowledge_id, "icon")
        widget_avatar_content = await resolve_widget_image(widget_avatar, widget_avatar_url, knowledge_id, "avatar")

        if existing:
            logger.info(f"Widget settings already exist for knowledge_id: {knowledge_id}; updating")
            await update_widget_settings(
                knowledge_id=knowledge_id,
                heading=heading if heading != "Welcome" else None,
                sub_heading=sub_heading if sub_heading != "Got a Question? Just Ask!" else None,
                greet_message=greet_message if greet_message != "Hello! What's up?" else None,
                color=color if color != "#3a5fe3" else None,
                show_sources=show_sources,
                widget_icon=widget_icon_content,
                widget_avatar=widget_avatar_content,
            )
            return {"message": "Updated widget settings successfully"}

        logger.info(f"No widget settings found for knowledge_id: {knowledge_id}; creating")
        await add_widget_settings(
            knowledge_id,
            heading,
            sub_heading,
            greet_message,
            color,
            show_sources if show_sources is not None else False,
            widget_icon_content,
            widget_avatar_content,
        )
        return {"message": "Added widget settings successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding/updating widget settings for knowledge_id {knowledge_id}: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.get("/v1/{knowledge_id}/widget-config")
async def get_widget_config(
    knowledge_id: str,
    _: Optional[str] = Depends(require_permission("read")),
):
    try:
        settings = await get_widget_settings(knowledge_id)
        if settings:
            return {"widget_settings": settings}
        logger.info(f"No widget settings found for knowledge_id: {knowledge_id}; returning defaults")
        return {"widget_settings": DEFAULT_WIDGET_SETTINGS}
    except Exception as e:
        logger.error(f"Error getting widget settings for knowledge_id {knowledge_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting widget settings: {str(e)}")


@router.delete("/v1/{knowledge_id}/widget-config")
async def delete_widget_config(
    knowledge_id: str,
    _: Optional[str] = Depends(require_permission("write")),
):
    try:
        deleted = await delete_widget_settings(knowledge_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"No widget settings found for knowledge_id: {knowledge_id}")
        return {"message": "Successfully deleted widget settings"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting widget settings for knowledge_id {knowledge_id}: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.get("/v1/widget-config/default-icons")
async def get_default_widget_icons():
    return {"widget_icons": DEFAULT_WIDGET_ICONS}


@router.get("/v1/widget-config/default-avatars")
async def get_default_widget_avatars():
    return {"widget_avatars": DEFAULT_WIDGET_AVATARS}
