"""Models for knowledge configuration settings endpoints."""

from pydantic import BaseModel, Field, model_validator
from typing import Optional

from utils.config.enrichment_config import DEFAULT_ENRICHMENT_PROMPT
from utils.models.enums import EmbeddingModel, FieldTemplate


class ChunkingStrategyConfig(BaseModel):
    """Chunking strategy configuration for knowledge settings."""
    document_level_metadata: Optional[bool] = Field(default=True, description="Enable document-level metadata extraction")
    chunk_level_metadata: Optional[bool] = Field(default=False, description="Enable chunk-level metadata extraction")

    @model_validator(mode='after')
    def validate_chunk_requires_document(self):
        """chunk_level_metadata requires document_level_metadata to be enabled."""
        if self.chunk_level_metadata and not self.document_level_metadata:
            raise ValueError(
                "chunk_level_metadata requires document_level_metadata to be enabled. "
                "Cannot enable chunk-level metadata without document-level metadata."
            )
        return self


class KnowledgeConfigRequest(BaseModel):
    """Request model for /set-knowledge-config endpoint."""
    knowledge_id: str = Field(..., description="UUID of the knowledge service")
    embedding_model: Optional[EmbeddingModel] = Field(default=None, description="Embedding model to use")
    hybrid_search_enabled: Optional[bool] = Field(default=True, description="Enable hybrid search")
    chunking_strategy: Optional[ChunkingStrategyConfig] = Field(default=None, description="Chunking strategy configuration")
    field_template: Optional[FieldTemplate] = Field(default=None, description="Field template to use: 'default' for system fields only, 'resume' for system + resume fields")

    class Config:
        json_schema_extra = {
            "example": {
                "knowledge_id": "550e8400-e29b-41d4-a716-446655440000",
                "embedding_model": "paraphrase-multilingual-mpnet-base-v2",
                "hybrid_search_enabled": True,
                "chunking_strategy": {
                    "document_level_metadata": True,
                    "chunk_level_metadata": False
                },
                "field_template": "default"
            }
        }


class KnowledgeConfigResponse(BaseModel):
    """Response model for /set-knowledge-config POST endpoint."""
    knowledge_id: str
    message: str


class GetKnowledgeConfigRequest(BaseModel):
    """Request model for GET /get-knowledge-config endpoint."""
    knowledge_id: str = Field(..., description="UUID of the knowledge service")

    class Config:
        json_schema_extra = {
            "example": {
                "knowledge_id": "123e4567-e89b-12d3-a456-426614174000"
            }
        }


class GetKnowledgeConfigResponse(BaseModel):
    """Response model for GET /get-knowledge-config endpoint."""
    knowledge_id: str
    embedding_model: Optional[EmbeddingModel] = None
    hybrid_search_enabled: Optional[bool] = None
    chunking_strategy: Optional[ChunkingStrategyConfig] = None
    collection_name: Optional[str] = Field(default=None, description="Short display name (G1) — distinct from service_description, the longer free-text description. Null if never set.")
    service_description: Optional[str] = None
    # Resolved enrichment fields — always populated via fallback chain:
    # knowledge_config (per-knowledge) → enrichment_default_config (global) → env var → hardcoded
    enrichment_provider: Optional[str] = None
    enrichment_model: Optional[str] = None
    enrichment_temperature: Optional[float] = None
    enrichment_prompt: Optional[str] = None
    # Indicates where enrichment_model/provider/temperature came from
    enrichment_source: Optional[str] = None  # "knowledge" | "global" | "default"
    # False = document metadata indexed from our extra_metadata WITHOUT an LLM enrichment call
    enrichment_enabled: Optional[bool] = True
    use_deterministic_chunking: Optional[bool] = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "knowledge_id": "123e4567-e89b-12d3-a456-426614174000",
                "embedding_model": "paraphrase-multilingual-mpnet-base-v2",
                "hybrid_search_enabled": True,
                "chunking_strategy": {
                    "document_level_metadata": True,
                    "chunk_level_metadata": False
                },
                "service_description": "Knowledge base for customer support documents",
                "enrichment_provider": "openai",
                "enrichment_model": "gpt-4.1-mini",
                "enrichment_temperature": 0.0,
                "enrichment_prompt": DEFAULT_ENRICHMENT_PROMPT,
                "created_at": "2025-09-30T17:55:00Z",
                "updated_at": "2025-09-30T17:55:00Z"
            }
        }


class DeleteKnowledgeConfigRequest(BaseModel):
    """Request model for DELETE /delete-knowledge-config endpoint."""
    knowledge_id: str = Field(..., description="UUID of the knowledge service")

    class Config:
        json_schema_extra = {
            "example": {
                "knowledge_id": "123e4567-e89b-12d3-a456-426614174000"
            }
        }


class DeleteKnowledgeConfigResponse(BaseModel):
    """Response model for DELETE /delete-knowledge-config endpoint."""
    knowledge_id: str
    message: str

    class Config:
        json_schema_extra = {
            "example": {
                "knowledge_id": "123e4567-e89b-12d3-a456-426614174000",
                "message": "Knowledge configuration successfully deleted"
            }
        }
