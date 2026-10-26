"""
Validation utility functions for chatbot knowledge management.
Contains input validation, business logic validation, and usage limit checks.
"""

from typing import Optional
from fastapi import HTTPException
from utils.logger import logger
from utils.pydantic_models_knowledge import ScrapeUsing
from utils.files.file_utils import is_supported_file_type

from fastapi import HTTPException



class MissingDataException(HTTPException):
    def __init__(self, detail: str = "No valid data provided. Please provide either a file, text, or weblink."):
        super().__init__(status_code=400, detail=detail)

        
def validate_max_depth(max_depth: Optional[int]) -> None:
    """
    Validate max_depth parameter for web scraping.
    
    Args:
        max_depth: Maximum depth for web scraping
        
    Raises:
        HTTPException: If max_depth is invalid
    """
    if max_depth is not None:
        if max_depth < 0 or max_depth > 2:
            raise HTTPException(
                status_code=400,
                detail="max_depth must be between 0 and 2 (inclusive)"
            )


def validate_file_content_type(content_type: str) -> None:
    """
    Validate file content type against supported types.
    
    Args:
        content_type: MIME type of the file
        
    Raises:
        HTTPException: If file type is not supported
    """
    if not is_supported_file_type(content_type):
        raise HTTPException(status_code=400, detail="Unsupported file type")


def validate_weblink_url(weblink: str) -> None:
    """
    Validate weblink URL for restricted content.
    
    Args:
        weblink: URL to validate
        
    Raises:
        HTTPException: If URL contains restricted content
    """
    if weblink.startswith("https://rag-api.futuresmart.ai/docs"):
        raise HTTPException(
            status_code=400,
            detail="Weblink contains a restricted URL."
        )


# Removed 2026-08-20:
#
# ``validate_weblink_count(current_count, max_limit=20)`` and
# ``validate_firecrawl_usage_limits(user_id, scrape_using, max_depth)`` used to sit
# here and enforced nothing — the first had its ``raise HTTPException`` commented out
# with the note "to maintain legacy behavior" and just logged when the fake ceiling
# was hit; the second computed ``estimated_pages`` and threw it away, with an
# ``except ImportError`` that was a fossil of a removed call into the old standalone
# usage-limits service. Both were keyed by ``user_id``, which matches nothing else in
# the quota system, and the first paid for a real ``COUNT`` query per weblink to feed
# a check that could not fail.
#
# The real ceiling now lives on ``OwnerPlatformLimits.max_knowledge_items`` and is
# enforced in ``routers/document_management.py`` /
# ``routers/batch_document_management.py`` via ``utils/usage/enforcement.py`` before
# the ingest handler runs. See ``docs/plan/usage-and-limits/archive/design-limit-enforcement.md`` §8a.


def validate_required_data_provided(
    file,
    text: Optional[str],
    weblink: Optional[str],
    file_url: Optional[str]
) -> None:
    """
    Validate that at least one type of data is provided.
    
    Args:
        file: Uploaded file
        text: Text content
        weblink: Web link
        file_url: File URL
        
    Raises:
        HTTPException: If no valid data is provided
    """
    if not file and not text and not weblink and not file_url:
        logger.error("No valid data provided (file, text, weblink or file_url).")
        raise MissingDataException()


def normalize_empty_strings(*args) -> tuple:
    """
    Convert empty strings to None values.
    
    Args:
        *args: Arguments to normalize
        
    Returns:
        Tuple of normalized values
    """
    return tuple(None if isinstance(arg, str) and arg == "" else arg for arg in args)


def validate_chunk_size(chunk_size) -> Optional[int]:
    """
    Validate and normalize chunk_size parameter.
    
    Args:
        chunk_size: Chunk size value
        
    Returns:
        Normalized chunk size or None
    """
    if chunk_size == "":
        return None
    return chunk_size


def validate_knowledge_item_exists(status: Optional[str], knowledge_id: str, chatbot_id: str) -> None:
    """
    Validate that a knowledge item exists based on its status.
    
    Args:
        status: Current status of the knowledge item
        knowledge_id: Knowledge identifier
        chatbot_id: Chatbot identifier
        
    Raises:
        HTTPException: If knowledge item doesn't exist
    """
    if status is None:
        raise HTTPException(
            status_code=404,
            detail=f"knowledge_id {knowledge_id} not found for chatbot_id {chatbot_id}"
        )
