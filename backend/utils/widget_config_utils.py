"""Validation + upload helpers for widget branding settings. Ported from
fs-agent-dashboard's utils/widget_utils.py, but reusing this repo's own async
S3 uploader (utils/files/s3_utils.py::upload_file_to_s3) instead of a second
aioboto3 client — see docs/plan/access-and-sharing/archive/design-widget-fullscreen.md (Piece 1).
"""

import re
from io import BytesIO
from typing import Optional, Tuple

from fastapi import HTTPException, UploadFile

from utils.files.s3_utils import upload_file_to_s3

ALLOWED_IMAGE_MIME_TYPES = [
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "image/svg+xml", "image/bmp", "image/tiff",
    "image/avif", "image/heic", "image/heif",
]


async def validate_hex_color(value: str) -> str:
    """Validate a hex color code, e.g. #3a5fe3 or #fff."""
    if not re.match(r"^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})$", value):
        raise HTTPException(status_code=400, detail="Invalid hex color code. Must be in #RRGGBB or #RGB format.")
    return value


async def validate_image_file(file: Optional[UploadFile]) -> None:
    """Reject a non-image upload before it reaches S3."""
    if file is None:
        return
    if file.content_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type for {file.filename}. Only image files are allowed.",
        )


async def normalize_url_strings(icon_url: Optional[str], avatar_url: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Treat an empty/whitespace-only string the same as "not provided"."""
    return (
        None if not icon_url or icon_url.strip() == "" else icon_url,
        None if not avatar_url or avatar_url.strip() == "" else avatar_url,
    )


async def resolve_widget_image(
    file: Optional[UploadFile],
    url: Optional[str],
    knowledge_id: str,
    kind: str,
) -> Optional[str]:
    """Resolve the stored value for widget_icon/widget_avatar: an uploaded file
    wins over a plain URL, matching fs-agent-dashboard's precedence."""
    if file is not None:
        content = await file.read()
        extension = (file.filename or "").rsplit(".", 1)[-1] or "png"
        file_name = f"widget_{kind}.{extension}"
        return await upload_file_to_s3(knowledge_id, file_name, file.content_type, BytesIO(content))
    return url
