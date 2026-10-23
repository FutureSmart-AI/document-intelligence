import re
import os
import uuid
from utils.logger import logger
from fastapi import HTTPException

def sanitize_filename(filename: str) -> str:
    """
    Sanitizes the filename by removing or replacing problematic characters
    that may interfere with S3 keys or presigned URLs.
    """
    # Split name and extension
    base, ext = os.path.splitext(filename)
    
    # Remove unsafe characters (preserve basic readable characters)
    base = re.sub(r'[^a-zA-Z0-9._-]', '_', base)

    # Optionally collapse multiple underscores
    base = re.sub(r'__+', '_', base).strip('_')

    return f"{base}{ext}"

def validate_text_content(text: str, min_length: int = 10) -> None:
    """
    Validate text content length.
    
    Args:
        text: Text content to validate
        min_length: Minimum required length
        
    Raises:
        HTTPException: If text is too short
    """
    if len(text.strip()) < min_length:
        logger.error("Text length too small.")
        raise HTTPException(
            status_code=400,
            detail=f"Text is too short. It must contain at least {min_length} characters."
        )
        
async def generate_object_id() -> str:
    """
    Generate a unique object ID using UUID4.
    
    Returns:
        Unique object identifier string
    """
    logger.info("Generating Object ID")
    object_id = str(uuid.uuid4())
    return object_id


async def generate_task_id() -> str:
    """
    Generate a unique task ID for Celery tasks using UUID4.
    
    Returns:
        Unique task identifier string
    """
    logger.info("Generating Task ID")
    task_id = str(uuid.uuid4())
    logger.info(f"Generated Task ID: {task_id}")
    return task_id

async def get_valid_filename(text: str) -> str:
    """
    Extract the first 8 valid characters from the text for filename generation.
    
    Args:
        text: Input text to extract characters from
        
    Returns:
        Generated filename with .txt extension
    """
    valid_chars = []
    
    for char in text:
        if char not in ['\n', '\r']:  # Skip newlines and carriage returns
            valid_chars.append(char)
        if len(valid_chars) == 8:  # Stop once we have 8 characters
            break
    
    return ''.join(valid_chars) + '.txt' if valid_chars else 'default.txt'

def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)


# async def extract_user_from_email(session_data):
#     """
#     Extract user ID from session data with multiple fallback strategies.
    
#     This function attempts to resolve a user ID using the following priority order:
#     1. Look up user ID from database using the email from session data
#     2. Use the direct 'user_id' field from session data
#     3. Fall back to 'sub' claim from 'all_claims' in session data
#     """

#     from utils.postre_pool_db_utils import get_user_id_from_email
#     user_id = await get_user_id_from_email(session_data.get("email"))
#     if user_id:
#         return user_id
#     else:
#         if not session_data:
#             return None
#         user_id = session_data.get("user_id")
#         if user_id:
#             return user_id
#         claims = session_data.get("all_claims", {}) or {}
#         return claims.get("sub")
