"""
Backwards-compatible re-export shim.
All definitions have moved to utils/models/. Import from there directly.
"""

from utils.models.enums import ScrapeUsing, ScrapingMethod, TranscribeUsing
from utils.models.db_tables import (
    # Users,
    Document,
    Batch,
    KnowledgeConfig,
    EnrichmentDefaultConfig,
    UserDefinedFieldManifest,
    ConnectorSyncConfig,
    ConnectorSyncRun,
    KnowledgeWidgetSettings,
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

__all__ = [
    "ScrapeUsing", "ScrapingMethod", "TranscribeUsing",
    # "Users",
    "Document", "Batch", "KnowledgeConfig",
    "EnrichmentDefaultConfig", "UserDefinedFieldManifest",
    "ConnectorSyncConfig", "ConnectorSyncRun", "KnowledgeWidgetSettings",  # "ChatbotDetails", "ExtractedMetadata",
    "GetKnowledgeParamsAuth", "DeleteKnowledgeDataRequestAuth",
    "DeleteChatbotDataRequestAuth", "DocumentItem", "GetKnowledgeResponse",
    "FileStatusResponse", "DeletionStatusResponse", "FileRequest",
    "GetDocumentSummariesRequest", "ChatbotData",
    "GenerateKnowledgeRequest", "GenerateKnowledgeResponse",
    "MetadataField", "MetadataResponse",
    "BatchUploadFileRequest", "BatchUploadUrlsRequest",
    "BatchUploadFileResponse", "BatchUploadUrlsResponse",
    "BatchDocumentProcessingRequest", "BatchDocumentStatus",
    "BatchDocumentsResponse", "BatchStatusResponse",
]
