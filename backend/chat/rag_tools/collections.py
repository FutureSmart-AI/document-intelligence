"""
Tool collections for RAG agent.

v2 consolidated surface (3 primary + 3 deep-read = 6 tools) + resolve_filter_value
(7 tools total):
  retrieve_chunks            — primary factual retrieval
  retrieve_documents         — unified document listing / enrichment
  retrieve_metadata_catalog  — unified metadata exploration
  resolve_filter_value       — canonical value lookup before building a metadata_filter
  + get-document-chunks, get-chunks-batch, get-chunk-metadata (deep-read, kept)

The donor's legacy 11-tool surface (RAG_TOOLS_LEGACY) and its exclusive
dependencies (metadata_tools.py, document_tools.py) are NOT ported — confirmed
legacy-only / rollback-path only, per the plan.
"""
from chat.rag_tools.chunk_tools import (
    get_document_chunks_tool,
    get_chunks_batch_tool,
    get_chunk_metadata_tool,
)
from chat.rag_tools.consolidated_tools import (
    retrieve_documents_tool,
    retrieve_metadata_catalog_tool,
    retrieve_chunks_tool,
)
from chat.rag_tools.resolution_tools import (
    resolve_filter_value_tool,
)


# ---------------------------------------------------------------------------
# v2 consolidated tool set  (default — used by create_rag_agent_graph)
# ---------------------------------------------------------------------------
RAG_TOOLS = [
    retrieve_chunks_tool,
    retrieve_documents_tool,
    retrieve_metadata_catalog_tool,
    resolve_filter_value_tool,
    # Deep-read tools kept for advanced / phase-2 use
    get_document_chunks_tool,
    get_chunks_batch_tool,
    get_chunk_metadata_tool,
]

BASIC_RAG_TOOLS = [
    retrieve_chunks_tool,
    retrieve_documents_tool,
]
