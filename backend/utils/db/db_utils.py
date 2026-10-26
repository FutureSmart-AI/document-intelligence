"""
Database utility facade re-exporting split modules for backward compatibility.

This module maintains the original import path while the actual implementations
are split across dedicated modules.
"""

from typing import Optional, List

# Re-export session for external use if needed
from utils.db.session import engine, async_session  # noqa: F401

# Import knowledge-related operations
from utils.db.knowledge_ops import (
    # count_weblinks_for_user / get_user_weblink_count — removed 2026-08-20; see
    # docs/plan/usage-and-limits/archive/design-limit-enforcement.md §8a
    # retrieve_agentic_rag_details,
    # get_hybrid_search_value,
    insert_extracted_text,
    retrieve_chatbot_knowledge,
    retrieve_chatbot_knowledge_v2,
    retrieve_knowledge_processing_status,
    get_chatbot_search_config,
    # get_agentic_rag_config,
    get_user_id_from_email,
    create_knowledge_service,
    get_knowledge_services_by_owner,
    get_knowledge_service,
    update_document_presigned_url,
)

# Import deletion-related operations
from utils.db.deletion_ops import (
    retrieve_knowledge_deletion_status,
    set_initial_deletion_status,
    delete_chatbot_knowledge,
    set_knowledge_deletion_complete,
    delete_documents_index,
    set_knowledge_deletion_error,
)


# Backward compatibility aliases matching old names
async def insert_knowledge_record(*args, **kwargs):
    return await insert_extracted_text(*args, **kwargs)


async def get_chatbot_knowledge_data(*args, **kwargs):
    return await retrieve_chatbot_knowledge(*args, **kwargs)


async def get_knowledge_processing_status(*args, **kwargs):
    return await retrieve_knowledge_processing_status(*args, **kwargs)


async def get_knowledge_deletion_status(*args, **kwargs):
    return await retrieve_knowledge_deletion_status(*args, **kwargs)


