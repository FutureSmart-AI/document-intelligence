"""Request/response models for batch document upload and processing endpoints."""

from pydantic import BaseModel, Field, model_validator
from typing import Optional, List

from utils.models.enums import TranscribeUsing


class BatchUploadFileRequest(BaseModel):
    """Request model for a single file in batch upload."""
    file_name: str = Field(..., description="Name of the file")
    content_type: str = Field(..., description="MIME type of the file")


class BatchUploadUrlsRequest(BaseModel):
    """Request model for batch upload URLs generation."""
    files: List[BatchUploadFileRequest] = Field(..., description="List of files to generate upload URLs for (1-50 items)")
    user_id: Optional[str] = Field(None, description="User identifier (optional)")

    @model_validator(mode='before')
    def strip_user_id_from_payload(cls, values):
        values.pop("user_id", None)
        return values


class BatchUploadFileResponse(BaseModel):
    """Response model for a single file upload URL."""
    object_id: str = Field(..., description="Unique object identifier for the file")
    file_name: str = Field(..., description="Name of the file")
    presigned_url: str = Field(..., description="Presigned URL for uploading the file")
    download_presigned_url: str = Field(..., description="Presigned URL for downloading the file")


class BatchUploadUrlsResponse(BaseModel):
    """Response model for batch upload URLs generation."""
    uploads: List[BatchUploadFileResponse] = Field(..., description="List of upload URLs for each file")


class BatchDocumentProcessingRequest(BaseModel):
    """Request model for batch document processing."""
    user_id: Optional[str] = Field(None, description="User identifier (optional)")
    enable_chunking: Optional[bool] = Field(True, description="Enable chunking (default: true)")
    use_llama_parse: Optional[bool] = Field(False, description="Use LlamaParse for processing (default: false)")
    chunk_size: Optional[int] = Field(2000, description="Chunk size for processing (default: 2000)")
    contextualise_chunks: Optional[bool] = Field(False, description="Contextualise chunks (default: false)")
    transcript_provider: Optional[TranscribeUsing] = Field(TranscribeUsing.option1, description="Transcript provider (optional)")

    @model_validator(mode='before')
    def strip_user_id_from_payload(cls, values):
        values.pop("user_id", None)
        return values


class BatchDocumentStatus(BaseModel):
    """Status model for a single document in batch processing."""
    object_id: str = Field(..., description="Object identifier for the document")
    file_name: str = Field(..., description="File name of the document")
    status: str = Field(..., description="Processing status of the document")
    processing_duration_ms: Optional[float] = Field(default=None, description="Processing duration in milliseconds (completed_at - created_at)")
    error_detail: Optional[str] = Field(default=None, description="Error detail if status is 'failed'")


class BatchDocumentsResponse(BaseModel):
    """Response model for batch document processing."""
    batch_id: str = Field(..., description="Unique batch identifier")
    status: str = Field(..., description="Batch status: 'processing' if any document is being processed, 'failed' if all failed")
    message: str = Field(..., description="Status message")
    documents: List[BatchDocumentStatus] = Field(..., description="List of documents with their status")


class BatchStatusResponse(BaseModel):
    """Response model for batch status tracking."""
    batch_id: str = Field(..., description="Unique batch identifier")
    total: int = Field(..., description="Total number of documents in the batch")
    completed: int = Field(..., description="Number of completed documents")
    processing: int = Field(..., description="Number of documents currently processing")
    failed: int = Field(..., description="Number of failed documents")
    documents: List[BatchDocumentStatus] = Field(..., description="List of documents with their status")
    elapsed_since_created_ms: Optional[float] = Field(default=None, description="Milliseconds elapsed from batch creation to now")
