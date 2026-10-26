"""
Backwards-compatible re-export shim.
All definitions have moved to utils/models/. Import from there directly.
"""

from utils.models.enums import EmbeddingModel, FieldTemplate
from utils.models.knowledge_settings import (
    ChunkingStrategyConfig,
    KnowledgeConfigRequest,
    KnowledgeConfigResponse,
    GetKnowledgeConfigRequest,
    GetKnowledgeConfigResponse,
    DeleteKnowledgeConfigRequest,
    DeleteKnowledgeConfigResponse,
)

__all__ = [
    "EmbeddingModel", "FieldTemplate",
    "ChunkingStrategyConfig",
    "KnowledgeConfigRequest", "KnowledgeConfigResponse",
    "GetKnowledgeConfigRequest", "GetKnowledgeConfigResponse",
    "DeleteKnowledgeConfigRequest", "DeleteKnowledgeConfigResponse",
]
