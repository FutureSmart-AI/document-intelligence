from sqlmodel import SQLModel, Field
from typing import Optional, Any, List, Dict
from datetime import datetime
import uuid
from sqlalchemy import JSON, Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableList

# Shared SQLModel table definitions used across multiple database utility files

# class Users(SQLModel, table=True):
#     __tablename__ = "users"
#     __table_args__ = {'extend_existing': True}
#     user_id: str = Field(primary_key=True)
#     email_id: str = Field(index=True)
#     user_name: Optional[str] = None
#     user_pic: Optional[str] = None
#     first_logged_in: Optional[datetime] = None
#     last_accessed: Optional[datetime] = None
#     service_count: Optional[int] = Field(default=0)
#     tier: Optional[str] = Field(default="User")
#     total_requests: Optional[int] = Field(default=0)

class ChatbotDetails(SQLModel, table=True):
    __tablename__ = "chatbot_details"
    __table_args__ = {'extend_existing': True}
    user_id: str = Field()
    chatbot_id: str = Field(primary_key=True)
    chatbot_name: str
    system_prompt: str
    llm_provider: str = Field(default='openai')
    model: str = Field(default='gpt-5-mini')
    temperature: float = Field(default=0.7)
    collect_user_info: bool = Field(default=True)
    show_sources: bool = Field(default=True)
    follow_up: bool = Field(default=False)
    restrict_to_knowledge_base: bool = Field(default=True)
    hybrid_search: bool = Field(default=False)
    embedding_model: str = Field(default='paraphrase-multilingual-mpnet-base-v2')
    agentic_rag: bool = Field(default=False)
    chunking_strategy: str = Field(default="LLM_CHUNK_WITH_METADATA")
    created_at: Optional[datetime] = Field(default_factory=datetime.now)
    deleted: bool = Field(default=False)
    deleted_at: Optional[datetime] = None

# class ChatSessions(SQLModel, table=True):
#     __tablename__ = "chat_sessions"
#     __table_args__ = {'extend_existing': True}
#     session_id: str = Field(primary_key=True)
#     chatbot_id: str = Field(index=True)
#     user_id: str = Field(index=True)
#     session_title: Optional[str] = None
#     display_message: Optional[str] = None
#     from_chat_widget: bool = Field(default=False)
#     created_time: Optional[datetime] = Field(default_factory=datetime.now)
#     deleted: bool = Field(default=False)

# class ChatLogs(SQLModel, table=True):
#     __tablename__ = "chat_logs"
#     __table_args__ = {'extend_existing': True}
#     id: Optional[int] = Field(default=None, primary_key=True)
#     session_id: str = Field(index=True)
#     user_id: str = Field(index=True)
#     user_query: Optional[str] = None
#     context: Optional[str] = None
#     gpt_response: Optional[str] = None
#     gpt_response_time: Optional[str] = None
#     tokens_consumed: Optional[str] = None
#     chatbot_id: str = Field(index=True)
#     chat_id: str = Field(index=True)
#     refined_query: Optional[str] = None
#     llm_provider: Optional[str] = None
#     model: Optional[str] = None
#     no_of_chunks: Optional[int] = Field(default=4)
#     debug_info: Optional[Any] = Field(default=None, sa_column=Column(JSON))
#     end_of_conversation: bool = Field(default=False)
#     stream: bool = Field(default=False)
#     use_user_info: bool = Field(default=False)
#     from_chat_widget: bool = Field(default=False)
#     created_at: Optional[datetime] = Field(default_factory=datetime.now)
#     feedback: Optional[str] = None

class Document(SQLModel, table=True):
    __tablename__ = "document"
    __table_args__ = {'extend_existing': True}
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    knowledge_id: str = Field(index=True)
    object_id: str = Field(index=True)
    file_name: Optional[str] = None
    content: Optional[str] = None
    llama_parse: bool = Field(default=False)
    url: Optional[str] = None
    deleted: bool = Field(default=False)
    created_at: Optional[datetime] = Field(default_factory=datetime.now)
    contextualise_chunks: Optional[bool] = Field(default=False)
    chunk_size: Optional[int] = None
    processing_status: Optional[str] = Field(default='processing')
    content_type: Optional[str] = None
    source_knowledge_id: Optional[str] = None # Deprecated
    source_object_id: Optional[str] = None
    source_url: Optional[str] = None
    deletion_status: Optional[str] = Field(default=None)
    task_duration_ms: Optional[int] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    error_detail: Optional[str] = Field(default=None)
    # The mode that actually ran — post any auto-shift, whether from the PDF
    # detection heuristics in celery_utils/processing/file_utils.py or the caller's
    # own explicit request. Column already exists on the shared `document` table
    # (fs-agent-knowledge migration 042_add_document_processing_mode.py); this repo's
    # ORM model was simply missing the field definition. No migration needed here.
    document_processing_mode: Optional[str] = Field(default=None, max_length=32)

# class WidgetsSettings(SQLModel, table=True):
#     __tablename__ = "widgets_settings"
#     __table_args__ = {'extend_existing': True}
#     id: Optional[int] = Field(default=None, primary_key=True)
#     chatbot_id: str = Field(index=True)
#     heading: Optional[str] = None
#     sub_heading: Optional[str] = None
#     greet_message: Optional[str] = None
#     color: Optional[str] = None
#     show_sources: Optional[bool] = None
#     widget_icon: Optional[str] = None
#     widget_avatar: Optional[str] = None
#     created_at: Optional[datetime] = Field(default_factory=datetime.now)
#     updated_at: Optional[datetime] = None
#     deleted: bool = Field(default=False)

class DocumentsIndex(SQLModel, table=True):
    __tablename__ = "documents_index"
    __table_args__ = {'extend_existing': True}
    object_id: uuid.UUID = Field(primary_key=True)
    user_id: str = Field(index=True)
    knowledge_id: str = Field(index=True)
    title: Optional[str] = None
    summary: Optional[str] = None
    document_type: Optional[str] = None
    document_type_granular: Optional[str] = None
    topic: Optional[str] = None
    keywords: Optional[str] = None
    entities: Optional[str] = None
    organization: Optional[str] = None
    temporal_context: Optional[str] = None
    document_outline: Optional[str] = None
    prepended_doc_summary: Optional[str] = None
    seo_title: Optional[str] = None
    seo_description: Optional[str] = None
    url: Optional[str] = None
    source_url: Optional[str] = None
    source_knowledge_id: Optional[str] = None
    file_name: Optional[str] = None

class FieldConfiguration(SQLModel, table=True):
    __tablename__ = "field_configurations"
    __table_args__ = {'extend_existing': True}
    id: Optional[int] = Field(default=None, primary_key=True)
    knowledge_id: str = Field(index=True)
    field_name: str
    description: Optional[str] = None
    field_type: str
    is_active: bool = Field(default=True)
    is_custom: bool = Field(default=False)
    is_required: bool = Field(default=False)
    # is_system_locked: bool = Field(default=False)
    sample_values: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    restrict_to_choices: bool = Field(default=False) 
    max_items: Optional[int] = None
    schema: Optional[Dict[str, str]] = Field(default=None, sa_column=Column(JSON))
    # v2 fields
    level: Optional[str] = Field(default="document")
    deprecated_at: Optional[datetime] = None
    deleted: bool = Field(default=False, index=True)
    deleted_at: Optional[datetime] = None
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

class ExtractedMetadata(SQLModel, table=True):
    __tablename__ = "extracted_metadata"
    __table_args__ = {'extend_existing': True}
    id: Optional[int] = Field(default=None, primary_key=True)
    object_id: Optional[str] = Field(default=None, index=True)
    field_configuration_id: int = Field(foreign_key="field_configurations.id")
    knowledge_id: str = Field(index=True)
    extracted_content: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    extraction_timestamp: Optional[datetime] = Field(default_factory=datetime.utcnow)

# class CustomActions(SQLModel, table=True):
#     __tablename__ = "custom_actions"
#     __table_args__ = {'extend_existing': True}
#     custom_action_id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
#     user_id: str = Field(index=True)
#     chatbot_id: str = Field(index=True)
#     name: str
#     description: Optional[str] = None
#     url: str
#     method: str = Field(default="POST")
#     headers: Optional[Any] = Field(default=None, sa_column=Column(JSON))
#     auth_type: Optional[str] = None
#     auth_details: Optional[Any] = Field(default=None, sa_column=Column(JSON))
#     params_schema: Optional[Any] = Field(default=None, sa_column=Column(JSON))
#     enabled: bool = Field(default=True)
#     created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
#     updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
#     deleted: bool = Field(default=False)
#     deleted_at: Optional[datetime] = None

# class ApiKeys(SQLModel, table=True):
#     __tablename__ = "api_keys"
#     __table_args__ = {'extend_existing': True}
#     id: Optional[int] = Field(default=None, primary_key=True)
#     user_id: str = Field(index=True)
#     chatbot_id: Optional[str] = Field(default=None, index=True)
#     api_key: str = Field(unique=True, index=True)
#     is_active: bool = Field(default=True)
#     created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
#     revoked_at: Optional[datetime] = None

# class IssuedTokens(SQLModel, table=True):
#     __tablename__ = "issued_tokens"
#     __table_args__ = {'extend_existing': True}
#     token_id: Optional[int] = Field(default=None, primary_key=True)
#     token: str = Field(index=True)
#     email_id: str = Field(index=True)
#     session_id: str = Field(index=True)
#     issued_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

class AdminPrompts(SQLModel, table=True):
    __tablename__ = "admin_prompts"
    __table_args__ = {'extend_existing': True}
    prompt_id: Optional[int] = Field(default=None, primary_key=True)
    prompt_content: str
    prompt_type: Optional[str] = None
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    deleted: bool = Field(default=False)


class UserPrompts(SQLModel, table=True):
    __tablename__ = "user_prompts"
    __table_args__ = {'extend_existing': True}
    prompt_id: Optional[int] = Field(default=None, primary_key=True)
    prompt_content: str
    prompt_type: Optional[str] = None
    user_id: str = Field(index=True)
    chatbot_id: str = Field(index=True)
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    deleted: bool = Field(default=False)

# class BotNodeModels(SQLModel, table=True):
#     __tablename__ = "bot_node_models"
#     __table_args__ = {'extend_existing': True}
#     id: Optional[int] = Field(default=None, primary_key=True)
#     bot_id: str = Field(index=True)
#     node_name: str = Field(index=True)
#     model_provider: str
#     model_name: str
#     temperature: float = Field(default=0.7)
#     max_tokens: int = Field(default=1000)
#     is_default: bool = Field(default=True)
#     created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
#     updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

# # Tier Usage Tables
# class TierUsageLimits(SQLModel, table=True):
#     __tablename__ = "tier_usage_limits"
    
#     tier: str = Field(primary_key=True)
#     feature: str = Field(primary_key=True)
#     limit_key: str = Field(primary_key=True)
#     daily_limit: Optional[int] = None
#     total_limit: Optional[int] = None
#     cost_limit_usd: Optional[float] = None
#     updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

# class TierModelAccess(SQLModel, table=True):
#     __tablename__ = "tier_model_access"
    
#     tier: str = Field(primary_key=True)
#     feature: str = Field(primary_key=True)
#     provider: Optional[str] = None
#     allowed_models: Any = Field(sa_column=Column(JSON))
#     default_model: Optional[str] = None
#     enabled: bool = Field(default=True)
#     updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

# class TierFeatureFlags(SQLModel, table=True):
#     __tablename__ = "tier_feature_flags"
    
#     tier: str = Field(primary_key=True)
#     feature: str = Field(primary_key=True)
#     feature_values: Any = Field(sa_column=Column(JSON))
#     default_value: Optional[str] = None
#     updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

# class UserUsageLimits(SQLModel, table=True):
#     __tablename__ = "user_usage_limits"
    
#     user_id: str = Field(primary_key=True)
#     feature: str = Field(primary_key=True)
#     limit_key: str = Field(primary_key=True)
#     daily_limit: Optional[int] = None
#     total_limit: Optional[int] = None
#     cost_limit_usd: Optional[float] = None
#     custom_override: bool = Field(default=True)
#     updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

# class UserModelAccess(SQLModel, table=True):
#     __tablename__ = "user_model_access"
    
#     user_id: str = Field(primary_key=True)
#     feature: str = Field(primary_key=True)
#     provider: Optional[str] = None
#     allowed_models: Any = Field(sa_column=Column(JSON))
#     default_model: Optional[str] = None
#     custom_override: bool = Field(default=True)
#     updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

# class UserFeatureFlags(SQLModel, table=True):
#     __tablename__ = "user_feature_flags"
    
#     user_id: str = Field(primary_key=True)
#     feature: str = Field(primary_key=True)
#     feature_values: Any = Field(sa_column=Column(JSON))
#     default_value: Optional[str] = None
#     custom_override: bool = Field(default=True)
#     updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

class UserUsage(SQLModel, table=True):
    __tablename__ = "user_usage"
    
    user_id: str = Field(primary_key=True)
    feature: str = Field(primary_key=True)
    limit_key: str = Field(primary_key=True)
    limit_used: int = Field(default=0)
    cost_usd: float = Field(default=0.0)
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)


class KnowledgeConfig(SQLModel, table=True):
    __tablename__ = "knowledge_config"
    __table_args__ = {'extend_existing': True}
    knowledge_id: str = Field(primary_key=True)
    # Service fields (set by /knowledge/generate)
    owner_id: Optional[str] = Field(index=True)
    # service_name: Optional[str] = None
    # Configuration fields (set by /set-knowledge-config)
    embedding_model: Optional[str] = None
    hybrid_search_enabled: Optional[bool] = None
    document_level_metadata: Optional[bool] = None
    chunk_level_metadata: Optional[bool] = None
    # Enrichment LLM configuration
    enrichment_provider: Optional[str] = None  # "openai", "groq", "anthropic"
    enrichment_model: Optional[str] = None     # e.g., "gpt-5-mini", "llama-3.3-70b-versatile"
    enrichment_temperature: Optional[float] = None
    enrichment_prompt: Optional[str] = None    # Custom prompt to append to base enrichment prompt
    use_deterministic_chunking: Optional[bool] = Field(default=False)  # Strategy 2: deterministic pre-split before LLM call
    enrichment_enabled: Optional[bool] = Field(default=True)  # False = store our extra_metadata (filterable) WITHOUT the LLM enrichment call (Strategy 6b)
    # Common fields
    created_at: Optional[datetime] = Field(default_factory=datetime.now)
    updated_at: Optional[datetime] = Field(default_factory=datetime.now)
    deleted: bool = Field(default=False)
    deleted_at: Optional[datetime] = None


class EnrichmentDefaultConfig(SQLModel, table=True):
    __tablename__ = "enrichment_default_config"
    __table_args__ = {'extend_existing': True}

    id: Optional[int] = Field(default=None, primary_key=True)  # SERIAL — assigned by DB on insert
    enrichment_model: str
    enrichment_provider: str
    enrichment_temperature: float = Field(default=0.0)
    enrichment_fallback_model: str
    enrichment_fallback_provider: str
    updated_at: Optional[datetime] = Field(default_factory=datetime.now)


# class InitialSuggestedQuestions(SQLModel, table=True):
#     __tablename__ = "initial_suggested_questions"
#     __table_args__ = {'extend_existing': True}
#     user_id: str = Field(primary_key=True)
#     chatbot_id: str = Field(primary_key=True)
#     questions: List[dict] = Field(
#         sa_column=Column(
#             MutableList.as_mutable(JSONB),
#             default=[]
#         ),
#         default_factory=list
#     )

 