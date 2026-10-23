"""
Backwards-compatible re-export shim.
All definitions have moved to utils/models/. Import from there directly.
"""

from utils.models.retrieval import (
    ChunkSearchRequest,
    ChunkSearchResponse,
    DocumentListResponse,
    DocumentResponse,
    DocumentMetadataResponse,
    ChunkSingleResponse,
    ChunkBulkFetchRequest,
    ChunkBulkFetchResponse,
    DocumentSummariesRequest,
    SummaryItem,
    DocumentSummariesResponse,
    ChunkMetadata,
    DocumentChunkResponse,
    DocumentChunksResponse,
    AllDocumentsMetadataRequest,
    AllDocumentsMetadataResponse,
    ColumnSchema,
    ColumnSchemaV2,
    DataViewRequest,
    DataViewResponse,
    DataViewRequestV2,
    DataViewResponseV2,
)

__all__ = [
    "ChunkSearchRequest", "ChunkSearchResponse",
    "DocumentListResponse", "DocumentResponse", "DocumentMetadataResponse",
    "ChunkSingleResponse", "ChunkBulkFetchRequest", "ChunkBulkFetchResponse",
    "DocumentSummariesRequest", "SummaryItem", "DocumentSummariesResponse",
    "ChunkMetadata", "DocumentChunkResponse", "DocumentChunksResponse",
    "AllDocumentsMetadataRequest", "AllDocumentsMetadataResponse",
    "ColumnSchema", "ColumnSchemaV2", "DataViewRequest", "DataViewResponse",
    "DataViewRequestV2", "DataViewResponseV2",
]
