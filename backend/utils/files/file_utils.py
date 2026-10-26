"""
Facade exports for file utilities, split into parsing and S3 helpers.
This module re-exports symbols to preserve backwards compatibility.
"""

from utils.common_utils import sanitize_filename
from .parsing_utils import (
    SUPPORTED_FILE_TYPES,
    is_supported_file_type,
    get_pdf_page_count_from_url,
    get_pdf_page_count_from_bytes,
    get_docx_page_count_from_bytes,
    get_file_text,
)
from .s3_utils import (
    get_s3_client,
    upload_extracted_text_to_s3,
    upload_file_to_s3,
    generate_presigned_url,
    generate_presigned_url_v2,
    generate_s3_presigned_urls,
    upload_file_to_s3_storage,
    enhance_knowledge_with_presigned_urls,
    build_s3_key,
)

__all__ = [
    # Common
    "sanitize_filename",
    # Parsing
    "SUPPORTED_FILE_TYPES",
    "is_supported_file_type",
    "get_pdf_page_count_from_url",
    "get_pdf_page_count_from_bytes",
    "get_docx_page_count_from_bytes",
    "get_file_text",
    # S3
    "get_s3_client",
    "upload_extracted_text_to_s3",
    "upload_file_to_s3",
    "generate_presigned_url",
    "generate_presigned_url_v2",
    "generate_s3_presigned_urls",
    "upload_file_to_s3_storage",
    "enhance_knowledge_with_presigned_urls",
    "build_s3_key",
]


