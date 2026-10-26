"""Request/response models for document and knowledge management endpoints."""

from pydantic import BaseModel, model_validator
from typing import Optional, List
from datetime import datetime

from utils.models.enums import ScrapeUsing, ScrapingMethod, TranscribeUsing


class GetKnowledgeParamsAuth(BaseModel):
    user_id: Optional[str] = None
    content_type: Optional[str] = ["file", "url", "plain_text"]
    knowledge_id: str

    @model_validator(mode='before')
    def strip_user_id_from_payload(cls, values):
        values.pop("user_id", None)
        return values


class DeleteKnowledgeDataRequestAuth(BaseModel):
    user_id: Optional[str] = None
    knowledge_id: str
    object_id: Optional[List[str]] = None

    @model_validator(mode='before')
    def strip_user_id_from_payload(cls, values):
        values.pop("user_id", None)
        return values


class DeleteChatbotDataRequestAuth(BaseModel):
    user_id: Optional[str] = None
    chatbot_id: str
    knowledge_id: Optional[List[str]] = None

    @model_validator(mode='before')
    def strip_user_id_from_payload(cls, values):
        values.pop("user_id", None)
        return values


class DocumentItem(BaseModel):
    id: str
    object_id: Optional[str] = None
    file_name: Optional[str] = None
    url: Optional[str] = None
    source_presigned_url: Optional[str] = None
    created_time: datetime
    content_type: Optional[str] = None
    extracted_s3_url: Optional[str] = None
    status: Optional[str] = None
    sub_urls: Optional[List['DocumentItem']] = None


class GetKnowledgeResponse(BaseModel):
    document: List[DocumentItem]
    message: Optional[str] = None


class FileStatusResponse(BaseModel):
    knowledge_id: str
    object_id: str
    status: str


class DeletionStatusResponse(BaseModel):
    knowledge_id: str
    object_id: str
    status: str


class FileRequest(BaseModel):
    fileName: str
    contentType: str


class GetDocumentSummariesRequest(BaseModel):
    object_ids: List[str]


class ChatbotData(BaseModel):
    user_id: Optional[str] = None
    chatbot_id: Optional[str] = None
    text: Optional[str] = None
    weblink: Optional[str] = None
    use_llama_parse: Optional[bool] = False
    scrape_using: Optional[ScrapeUsing] = ScrapeUsing.option1
    scraping_method: Optional[ScrapingMethod] = ScrapingMethod.option1
    transcript_provider: Optional[TranscribeUsing] = TranscribeUsing.option1
    contextualise_chunks: Optional[bool] = False
    chunk_size: Optional[int] = None

    @model_validator(mode='before')
    def strip_user_id_from_payload(cls, values):
        values.pop("user_id", None)
        return values
