"""
URL utility functions for chatbot knowledge management.
Contains functions for URL parsing and filename extraction.
"""

import mimetypes
from urllib.parse import urlparse, unquote
from fastapi import HTTPException


def register_custom_mimetypes():
    """Register custom MIME types for various file formats."""
    mimetypes.add_type("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx")
    mimetypes.add_type("audio/mpeg", ".mp3")
    mimetypes.add_type("audio/mpeg", ".mpeg")
    mimetypes.add_type("audio/aac", ".aac")
    mimetypes.add_type("audio/ogg", ".ogg")
    mimetypes.add_type('application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.docx')
    mimetypes.add_type('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx')
    mimetypes.add_type('application/vnd.openxmlformats-officedocument.presentationml.presentation', '.pptx')
    mimetypes.add_type("text/markdown", ".md")


async def extract_filename_and_content_type(file_url: str):
    """
    Extract filename and content type from S3 or presigned URL.
    
    Args:
        file_url: URL to extract filename and content type from
        
    Returns:
        Tuple of (file_name, content_type, knowledge_id)
        
    Raises:
        HTTPException: If file type is not supported
    """
    SUPPORTED_FILE_TYPES = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "text/plain": ".txt",
        "audio/mpeg": [".mp3", ".mpeg", ".wav", ".aac", ".ogg"],
        "audio/wav": [".wav"],
        "audio/x-wav": [".wav"],
        "text/html": [".html", ".htm"],
        "text/markdown": [".md"]
    }
    
    parsed = urlparse(file_url)
    path = parsed.path  # e.g., /bucket-name/folder/filename.ext
    file_name_with_knowledge_id = unquote(path.split("/")[-1])
    parts = file_name_with_knowledge_id.split("_")
    knowledge_id = parts[0]
    file_name = "_".join(parts[1:])

    # Ensure mimetypes covers .docx, .xlsx etc.
    register_custom_mimetypes()

    # Infer MIME type
    content_type, _ = mimetypes.guess_type(file_name)
    content_type = content_type or "application/octet-stream"
    
    if content_type not in SUPPORTED_FILE_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    return file_name, content_type, knowledge_id
