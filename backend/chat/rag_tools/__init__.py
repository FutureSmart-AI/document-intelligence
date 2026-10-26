"""
RAG Tools Package — Metadata-aware, Document-level, and Chunk-level tools.
These tools are used ONLY by the RAG subagent for intelligent retrieval.

v2 consolidated surface exposes 3 primary tools:
  retrieve_chunks, retrieve_documents, retrieve_metadata_catalog
plus resolve_filter_value and 3 deep-read tools kept from v1.

Trimmed port of fs-agent-chat/agents/rag_agent/rag_tools/ — the donor's
legacy 11-tool surface (RAG_TOOLS_LEGACY, metadata_tools.py, document_tools.py)
is not included here.
"""
from chat.rag_tools.helpers import normalize_metadata_filter

# Schemas (legacy schemas kept because the deep-read tools import them;
# v2 schemas for the 3 consolidated tools)
from chat.rag_tools.schemas import (
    GetDocumentChunksInput,
    GetChunksBatchInput,
    GetChunkMetadataInput,
    MetadataValue,
    # v2 schemas
    RetrieveDocumentsInput,
    RetrieveMetadataCatalogInput,
    RetrieveChunksInput,
)

# Deep-read tool callables
from chat.rag_tools.chunk_tools import (
    get_document_chunks_tool,
    get_chunks_batch_tool,
    get_chunk_metadata_tool,
)

# v2 consolidated tool callables
from chat.rag_tools.consolidated_tools import (
    retrieve_documents_tool,
    retrieve_metadata_catalog_tool,
    retrieve_chunks_tool,
)
from chat.rag_tools.resolution_tools import (
    resolve_filter_value_tool,
)

# Collections
from chat.rag_tools.collections import (
    RAG_TOOLS,
    BASIC_RAG_TOOLS,
)

__all__ = [
    # Helpers
    "normalize_metadata_filter",
    # Schemas
    "GetDocumentChunksInput",
    "GetChunksBatchInput",
    "GetChunkMetadataInput",
    "MetadataValue",
    # v2 schemas
    "RetrieveDocumentsInput",
    "RetrieveMetadataCatalogInput",
    "RetrieveChunksInput",
    # Deep-read tools
    "get_document_chunks_tool",
    "get_chunks_batch_tool",
    "get_chunk_metadata_tool",
    # v2 tools
    "retrieve_documents_tool",
    "retrieve_metadata_catalog_tool",
    "retrieve_chunks_tool",
    "resolve_filter_value_tool",
    # Collections
    "RAG_TOOLS",
    "BASIC_RAG_TOOLS",
]
