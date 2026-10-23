"""
Knowledge utilities package root.
This file provides backwards-compatible re-exports for common symbols after subpackage refactor.
"""

# Re-export commonly used functions/types for convenience and minimal breakage
from .common_utils import (
    sanitize_filename,
    generate_object_id,
    generate_task_id,
    get_valid_filename,
)

from .files.file_utils import (
    generate_s3_presigned_urls,
    upload_file_to_s3_storage,
    enhance_knowledge_with_presigned_urls,
    is_supported_file_type,
)

from .validation.validation_utils import (
    validate_max_depth,
    validate_file_content_type,
    validate_weblink_url,
    validate_required_data_provided,
    normalize_empty_strings,
    validate_chunk_size,
    validate_knowledge_item_exists,
)

from .validation.input_validation_utils import (
    validate_chatbot_input,
    handle_empty_values,
)

from .tasks.processing_handlers import (
    process_uploaded_file_handler,
    process_file_url_handler,
    process_text_content_handler,
    process_weblink_handler,
)

from .auth.auth_utils import extract_user_from_email

from .db.db_utils import (
    insert_extracted_text,
    retrieve_chatbot_knowledge,
    retrieve_knowledge_processing_status,
    get_chatbot_search_config,
    # get_agentic_rag_config,
    retrieve_knowledge_deletion_status,
    set_initial_deletion_status,
    delete_chatbot_knowledge,
    set_knowledge_deletion_complete,
    delete_documents_index,
    set_knowledge_deletion_error,
)

__all__ = [
    # Common
    "sanitize_filename",
    "generate_object_id",
    "generate_task_id",
    "get_valid_filename",
    # Files
    "generate_s3_presigned_urls",
    "upload_file_to_s3_storage",
    "enhance_knowledge_with_presigned_urls",
    "is_supported_file_type",
    # Validation
    "validate_max_depth",
    "validate_file_content_type",
    "validate_weblink_url",
    "validate_required_data_provided",
    "normalize_empty_strings",
    "validate_chunk_size",
    "validate_knowledge_item_exists",
    # Input validation
    "validate_chatbot_input",
    "handle_empty_values", 
    # Handlers
    "process_uploaded_file_handler",
    "process_file_url_handler",
    "process_text_content_handler",
    "process_weblink_handler",
    # Auth
    "extract_user_from_email",
    # DB
    "insert_extracted_text",
    "retrieve_chatbot_knowledge",
    "retrieve_knowledge_processing_status",
    "get_chatbot_search_config",
    # "get_agentic_rag_config",
    "retrieve_knowledge_deletion_status",
    "set_initial_deletion_status",
    "delete_chatbot_knowledge",
    "set_knowledge_deletion_complete",
    "delete_documents_index",
    "set_knowledge_deletion_error",
]
