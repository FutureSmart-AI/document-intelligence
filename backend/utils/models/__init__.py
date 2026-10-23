"""
utils.models — all pydantic/SQLModel definitions for the knowledge service.

Import from the submodules directly for clarity, or from here for convenience.
"""

from utils.models.enums import (
    ScrapeUsing,
    ScrapingMethod,
    TranscribeUsing,
    EmbeddingModel,
    FieldTemplate,
    FieldType,
    DeletionStatus,
)
from utils.models.db_tables import (
    # Users,
    Document,
    Batch,
    KnowledgeConfig,
    EnrichmentDefaultConfig,
    KnowledgeAuditLog,
    # ChatbotDetails,
    # ExtractedMetadata,
)
from utils.models.document import (
    GetKnowledgeParamsAuth,
    DeleteKnowledgeDataRequestAuth,
    DeleteChatbotDataRequestAuth,
    DocumentItem,
    GetKnowledgeResponse,
    FileStatusResponse,
    DeletionStatusResponse,
    FileRequest,
    GetDocumentSummariesRequest,
    ChatbotData,
)
from utils.models.knowledge import (
    GenerateKnowledgeRequest,
    GenerateKnowledgeResponse,
    MetadataField,
    MetadataResponse,
)
from utils.models.batch import (
    BatchUploadFileRequest,
    BatchUploadUrlsRequest,
    BatchUploadFileResponse,
    BatchUploadUrlsResponse,
    BatchDocumentProcessingRequest,
    BatchDocumentStatus,
    BatchDocumentsResponse,
    BatchStatusResponse,
)
from utils.models.enrichment import (
    FieldConfiguration,
    EnrichmentSyncSummary,
    EnrichmentConfigurationSyncResponse,
    EnrichmentConfigurationErrorResponse,
    FieldValidationError,
    EnrichmentConfigurationValidationErrorResponse,
    FieldConfigurationResponseV2,
    FieldConfigurationRequestV2,
    EnrichmentConfigurationRequestV2,
    EnrichmentConfigurationResponseV2,
)
from utils.models.knowledge_settings import (
    ChunkingStrategyConfig,
    KnowledgeConfigRequest,
    KnowledgeConfigResponse,
    GetKnowledgeConfigRequest,
    GetKnowledgeConfigResponse,
    DeleteKnowledgeConfigRequest,
    DeleteKnowledgeConfigResponse,
)
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
    DataViewRequest,
    DataViewResponse,
    DataViewRequestV2,
    DataViewResponseV2,
)

__all__ = [
    # enums
    "ScrapeUsing", "ScrapingMethod", "TranscribeUsing",
    "EmbeddingModel", "FieldTemplate", "FieldType", "DeletionStatus",
    # db tables
    # "Users",
    "Document", "Batch", "KnowledgeConfig",
    "EnrichmentDefaultConfig", "KnowledgeAuditLog",  # "ChatbotDetails", "ExtractedMetadata",
    # document
    "GetKnowledgeParamsAuth", "DeleteKnowledgeDataRequestAuth",
    "DeleteChatbotDataRequestAuth", "DocumentItem", "GetKnowledgeResponse",
    "FileStatusResponse", "DeletionStatusResponse", "FileRequest",
    "GetDocumentSummariesRequest", "ChatbotData",
    # knowledge
    "GenerateKnowledgeRequest", "GenerateKnowledgeResponse",
    "MetadataField", "MetadataResponse",
    # batch
    "BatchUploadFileRequest", "BatchUploadUrlsRequest",
    "BatchUploadFileResponse", "BatchUploadUrlsResponse",
    "BatchDocumentProcessingRequest", "BatchDocumentStatus",
    "BatchDocumentsResponse", "BatchStatusResponse",
    # enrichment
    "FieldConfiguration", "EnrichmentSyncSummary",
    "EnrichmentConfigurationSyncResponse", "EnrichmentConfigurationErrorResponse",
    "FieldValidationError", "EnrichmentConfigurationValidationErrorResponse",
    "FieldConfigurationResponseV2", "FieldConfigurationRequestV2",
    "EnrichmentConfigurationRequestV2", "EnrichmentConfigurationResponseV2",
    # knowledge settings
    "ChunkingStrategyConfig", "KnowledgeConfigRequest", "KnowledgeConfigResponse",
    "GetKnowledgeConfigRequest", "GetKnowledgeConfigResponse",
    "DeleteKnowledgeConfigRequest", "DeleteKnowledgeConfigResponse",
    # retrieval
    "ChunkSearchRequest", "ChunkSearchResponse", "DocumentListResponse",
    "DocumentResponse", "DocumentMetadataResponse", "ChunkSingleResponse",
    "ChunkBulkFetchRequest", "ChunkBulkFetchResponse",
    "DocumentSummariesRequest", "SummaryItem", "DocumentSummariesResponse",
    "ChunkMetadata", "DocumentChunkResponse", "DocumentChunksResponse",
    "AllDocumentsMetadataRequest", "AllDocumentsMetadataResponse",
    "ColumnSchema", "DataViewRequest", "DataViewResponse",
    "DataViewRequestV2", "DataViewResponseV2",
]
