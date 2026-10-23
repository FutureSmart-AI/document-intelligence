from utils.db.session import async_session
from utils.db.retry_utils import retry_db_connection
from utils.logger import logger
from utils.pydantic_models_knowledge import KnowledgeConfig, EnrichmentDefaultConfig  # ChatbotDetails, Users removed — tables not owned by this service
from sqlmodel import select, and_, desc
from typing import Optional, Dict, Any
from datetime import datetime
from uuid import UUID
from fastapi import HTTPException

from utils.config.enrichment_config import (
    DEFAULT_FIELD_CONFIGURATIONS,
    DOMAIN_FIELD_TEMPLATES,
    DEFAULT_ENRICHMENT_PROMPT,
    PROTECTED_SYSTEM_FIELDS,
)
from utils.config.resume_field_config import RESUME_FIELD_CONFIGURATIONS
from utils.pydantic_models_enrichment import FieldConfiguration
from utils.tracing import track_time

@track_time("DB: Get User ID from Email")
@retry_db_connection(max_retries=3)
async def get_user_id_from_email(email):
    """
    Retrieve user ID from the database based on the email.
    Returns the user ID if found, otherwise returns None.
    """
    try:
        async with async_session() as session:
            # statement = select(Users.user_id).where(Users.email_id == email)  # users table not owned by this service
            # result = await session.execute(statement)
            # user_id = result.scalar_one_or_none()
            user_id = None
        
        if user_id:
            logger.info(f"Retrieved user ID for email: {email}")
            return user_id
        else:
            logger.info(f"No user ID found for email: {email}")
            return None
    except Exception as e:
        logger.error(f"Database error occurred during get_user_id_from_email: {str(e)}")
        raise


# @track_time("DB: Save Chatbot Configuration")
# @retry_db_connection(max_retries=3)
# async def save_chatbot_configuration(
#     chatbot_id: str,
#     embedding_model: Optional[str] = None,
#     hybrid_search_enabled: Optional[bool] = None,
#     document_level_metadata: Optional[bool] = None,
#     chunk_level_metadata: Optional[bool] = None
# ) -> bool:
#     """
#     Save or update configuration for a chatbot in ChatbotDetails table.

#     Args:
#         chatbot_id (str): The ID of the chatbot
#         embedding_model (Optional[str]): The embedding model to use
#         hybrid_search_enabled (Optional[bool]): Whether to enable hybrid search
#         document_level_metadata (Optional[bool]): Enable document-level metadata extraction
#         chunk_level_metadata (Optional[bool]): Enable chunk-level metadata extraction

#     Returns:
#         bool: True if the update was successful, False if the chatbot was not found
#     """
#     try:
#         # Validate chunk_level_metadata requires document_level_metadata
#         if chunk_level_metadata and not document_level_metadata:
#             raise HTTPException(
#                 status_code=400,
#                 detail="chunk_level_metadata requires document_level_metadata to be enabled"
#             )
        
#         async with async_session() as session:
#             # Find the chatbot by ID
#             chatbot = await session.get(ChatbotDetails, chatbot_id)

#             if not chatbot:
#                 # Chatbot should be already created at this point
#                 logger.warning(f"Chatbot {chatbot_id} not found")
#                 raise HTTPException(
#                     status_code=404,
#                     detail=f"Chatbot not created"
#                 )

#             # Check if trying to change embedding_model
#             if chatbot.embedding_model is not None and embedding_model is not None and embedding_model != chatbot.embedding_model:
#                 logger.error(f"Attempt to change embedding_model for chatbot {chatbot_id} from '{chatbot.embedding_model}' to '{embedding_model}'")
#                 raise HTTPException(
#                     status_code=400,
#                     detail=f"Cannot change embedding_model once set. Current model: '{chatbot.embedding_model}'"
#                 )
            
#             # Update fields if provided
#             if embedding_model is not None:
#                 chatbot.embedding_model = embedding_model

#             if hybrid_search_enabled is not None:
#                 chatbot.hybrid_search = hybrid_search_enabled
            
#             if document_level_metadata is not None:
#                 chatbot.document_level_metadata = document_level_metadata
            
#             if chunk_level_metadata is not None:
#                 chatbot.chunk_level_metadata = chunk_level_metadata

#             await session.commit()
#             logger.info(f"Successfully updated chatbot configuration for chatbot {chatbot_id}")
#             return True

#     except Exception as e:
#         logger.error(f"Error updating chatbot configuration for chatbot {chatbot_id}: {str(e)}")
#         raise

@track_time("DB: Save Knowledge Configuration")
@retry_db_connection(max_retries=3)
async def save_knowledge_config(
    knowledge_id: str,
    embedding_model: Optional[str] = None,
    hybrid_search_enabled: Optional[bool] = None,
    document_level_metadata: Optional[bool] = None,
    chunk_level_metadata: Optional[bool] = None,
    service_description: Optional[str] = None,
    collection_name: Optional[str] = None,
    enrichment_provider: Optional[str] = None,
    enrichment_model: Optional[str] = None,
    enrichment_temperature: Optional[float] = None,
    enrichment_prompt: Optional[str] = None,
    use_deterministic_chunking: Optional[bool] = None,
    enrichment_enabled: Optional[bool] = None,
    extraction_instructions: Optional[str] = None,
) -> bool:
    """
    Save or update knowledge configuration for a knowledge service in knowledge_config table.
    Creates a new record if it doesn't exist, updates existing record otherwise.

    Args:
        knowledge_id (str): The ID of the knowledge service
        embedding_model (Optional[str]): The embedding model to use
        hybrid_search_enabled (Optional[bool]): Whether to enable hybrid search
        document_level_metadata (Optional[bool]): Enable document-level metadata extraction
        chunk_level_metadata (Optional[bool]): Enable chunk-level metadata extraction
        collection_name (Optional[str]): Short display name (G1) — distinct from
            service_description, which is the longer free-text description

    Returns:
        bool: True if the operation was successful
    """
    try:
        # Validate chunk_level_metadata requires document_level_metadata
        if chunk_level_metadata and not document_level_metadata:
            raise HTTPException(
                status_code=400,
                detail="chunk_level_metadata requires document_level_metadata to be enabled"
            )
        
        async with async_session() as session:
            
            # Try to find existing config
            config = await session.get(KnowledgeConfig, knowledge_id)

            if not config or config.deleted:
                raise HTTPException(
                    status_code=404,
                    detail=f"Knowledge service not found for knowledge_id: {knowledge_id}. Please create the knowledge service first using /knowledge/generate"
                )
            
            # Update existing config
            # Check if trying to change embedding_model
            if embedding_model is not None and config.embedding_model is not None and embedding_model != config.embedding_model:
                logger.error(f"Attempt to change embedding_model for knowledge {knowledge_id} from '{config.embedding_model}' to '{embedding_model}'")
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot change embedding_model once set. Current model: '{config.embedding_model}'"
                )
            
            if embedding_model is not None:
                config.embedding_model = embedding_model
            if hybrid_search_enabled is not None:
                config.hybrid_search_enabled = hybrid_search_enabled
            if document_level_metadata is not None:
                config.document_level_metadata = document_level_metadata
            if chunk_level_metadata is not None:
                config.chunk_level_metadata = chunk_level_metadata
            if service_description is not None:
                config.service_description = service_description
            if collection_name is not None:
                config.collection_name = collection_name
            if enrichment_provider is not None:
                config.enrichment_provider = enrichment_provider
            if enrichment_model is not None:
                config.enrichment_model = enrichment_model
            if enrichment_temperature is not None:
                config.enrichment_temperature = enrichment_temperature
            if enrichment_prompt is not None:
                config.enrichment_prompt = enrichment_prompt
            elif config.enrichment_prompt is None:
                config.enrichment_prompt = DEFAULT_ENRICHMENT_PROMPT
            if use_deterministic_chunking is not None:
                config.use_deterministic_chunking = use_deterministic_chunking
            if enrichment_enabled is not None:
                config.enrichment_enabled = enrichment_enabled
            if extraction_instructions is not None:
                config.extraction_instructions = extraction_instructions

            config.updated_at = datetime.now()
            logger.info(f"Updated existing knowledge config for knowledge {knowledge_id}")

            await session.commit()
            logger.info(f"Successfully saved knowledge configuration for knowledge {knowledge_id}")
            return True

    except Exception as e:
        logger.error(f"Error saving knowledge configuration for knowledge {knowledge_id}: {str(e)}")
        raise


@retry_db_connection(max_retries=3)
@track_time("DB: Get Knowledge Configuration")
async def get_knowledge_config(knowledge_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve knowledge configuration for a knowledge service from knowledge_config table.
    Only returns non-deleted configurations.

    Args:
        knowledge_id (str): The ID of the knowledge service

    Returns:
        Optional[Dict[str, Any]]: Configuration dictionary or None if not found or deleted
    """
    try:
        async with async_session() as session:
            
            config = await session.get(KnowledgeConfig, knowledge_id)
            
            if not config:
                logger.info(f"No knowledge config found for knowledge {knowledge_id}")
                return None
            
            if config.deleted:
                logger.info(f"Knowledge config for knowledge {knowledge_id} is deleted")
                return None
            
            return {
                "knowledge_id": config.knowledge_id,
                "owner_id": config.owner_id,
                "embedding_model": config.embedding_model,
                "hybrid_search_enabled": config.hybrid_search_enabled,
                "document_level_metadata": config.document_level_metadata,
                "chunk_level_metadata": config.chunk_level_metadata,
                "collection_name": getattr(config, "collection_name", None),
                "service_description": config.service_description,
                "extraction_instructions": getattr(config, "extraction_instructions", None),
                "enrichment_provider": config.enrichment_provider,
                "enrichment_model": config.enrichment_model,
                "enrichment_temperature": config.enrichment_temperature,
                "enrichment_prompt": config.enrichment_prompt or DEFAULT_ENRICHMENT_PROMPT,
                "use_deterministic_chunking": getattr(config, "use_deterministic_chunking", None),
                "enrichment_enabled": getattr(config, "enrichment_enabled", True) if getattr(config, "enrichment_enabled", True) is not None else True,
                "created_at": config.created_at.isoformat() if config.created_at else None,
                "updated_at": config.updated_at.isoformat() if config.updated_at else None,
            }

    except Exception as e:
        logger.error(f"Error retrieving knowledge configuration for knowledge {knowledge_id}: {str(e)}")
        raise


@track_time("DB: Check If Agent is Chatbot")
@retry_db_connection(max_retries=3)
async def check_if_agent_is_chatbot(_agent_id: str) -> bool:
    """
    Check if agent_id exists as chatbot_id in ChatbotDetails table with non-null embedding_model.
    
    This function handles both UUID and non-UUID chatbot_ids. If the agent_id is a valid UUID format,
    it could be either an agent or a chatbot. We check the chatbot_details table to determine if
    it's a chatbot with embeddings configured.

    Args:
        agent_id (str): The agent ID to check (can be UUID or string format)

    Returns:
        bool: True if agent_id exists as chatbot_id with non-null embedding_model, False otherwise
    """
    # ChatbotDetails removed — table phased out
    # try:
    #     async with async_session() as session:
    #         chatbot = await session.get(ChatbotDetails, agent_id)
    #         if chatbot and chatbot.embedding_model is not None:
    #             logger.info(f"Agent {agent_id} found as chatbot with embedding_model: {chatbot.embedding_model}")
    #             return True
    #         logger.info(f"Agent {agent_id} not found as chatbot with non-null embedding_model")
    #         return False
    # except Exception as e:
    #     logger.error(f"Error checking if agent is chatbot: {str(e)}")
    #     raise
    return False

# @track_time("DB: Get Chatbot Knowledge Configuration")
# @retry_db_connection(max_retries=3)
# async def get_chatbot_knowledge_config(chatbot_id: str) -> Optional[Dict[str, Any]]:
#     """
#     Retrieve knowledge configuration for a chatbot from ChatbotDetails table.

#     Args:
#         chatbot_id (str): The ID of the chatbot

#     Returns:
#         Optional[Dict[str, Any]]: Configuration dictionary or None if not found
#     """
#     try:
#         async with async_session() as session:
#             chatbot = await session.get(ChatbotDetails, chatbot_id)
            
#             if not chatbot:
#                 logger.info(f"No chatbot found with ID {chatbot_id}")
#                 return None
            
#             return {
#                 "chatbot_id": chatbot.chatbot_id,
#                 "embedding_model": chatbot.embedding_model,
#                 "hybrid_search_enabled": chatbot.hybrid_search,
#                 "document_level_metadata": chatbot.document_level_metadata,
#                 "chunk_level_metadata": chatbot.chunk_level_metadata,
#                 "created_at": chatbot.created_at.isoformat() if chatbot.created_at else None,
#                 "updated_at": None  # ChatbotDetails doesn't have updated_at
#             }

#     except Exception as e:
#         logger.error(f"Error retrieving chatbot configuration for chatbot {chatbot_id}: {str(e)}")
#         raise

@track_time("DB: Delete Knowledge Configuration")
@retry_db_connection(max_retries=3)
async def delete_knowledge_config(knowledge_id: str) -> bool:
    """
    Soft delete knowledge configuration for a knowledge service from knowledge_config table.
    Sets deleted=True and deleted_at=current_time instead of removing the record.

    Args:
        knowledge_id (str): The ID of the knowledge service

    Returns:
        bool: True if the deletion was successful, False if the config was not found
    """
    try:
        async with async_session() as session:
            # Find the configuration
            config = await session.get(KnowledgeConfig, knowledge_id)
            
            if not config:
                logger.info(f"No knowledge config found for knowledge {knowledge_id}")
                return False
            
            if config.deleted:
                logger.info(f"Knowledge config for knowledge {knowledge_id} is already deleted")
                return False
            
            # Perform soft delete
            config.deleted = True
            config.deleted_at = datetime.now()
            config.updated_at = datetime.now()
            
            await session.commit()
            logger.info(f"Successfully soft deleted knowledge configuration for knowledge {knowledge_id}")
            return True

    except Exception as e:
        logger.error(f"Error soft deleting knowledge configuration for knowledge {knowledge_id}: {str(e)}")
        raise


# SQLModel-based Database Operations
@track_time("DB: Initialize Default Field Configurations")
@retry_db_connection(max_retries=3)
async def initialize_default_field_configurations(knowledge_id: str):
    """Initialize default field configurations using SQLModel."""
    try:
        async with async_session() as session:
            for field_data in DEFAULT_FIELD_CONFIGURATIONS:
                # Check if field already exists
                statement = select(FieldConfiguration).where(
                    and_(
                        FieldConfiguration.knowledge_id == knowledge_id,
                        FieldConfiguration.field_name == field_data["field_name"]
                    )
                )
                result = await session.execute(statement)
                existing_field = result.scalars().first()
                
                if not existing_field:
                    # Create new field configuration
                    field_config = FieldConfiguration(
                        knowledge_id=knowledge_id,
                        **field_data
                    )
                    session.add(field_config)
            
            await session.commit()
            logger.info(f"Initialized default field configurations for knowledge {knowledge_id}")
    except Exception as e:
        logger.error(f"Error initializing default field configurations: {str(e)}")
        raise

@track_time("DB: Initialize Field Configurations by Template")
@retry_db_connection(max_retries=3)
async def initialize_field_configurations_by_template(
    knowledge_id: str,
    template: Optional[str] = None,
    document_level_metadata: bool = True,
    chunk_level_metadata: bool = False,
    seed_optional_defaults: bool = True,
):
    """
    Initialize field configurations based on template and metadata level flags.
    Idempotent — skips fields that already exist.

    seed_optional_defaults=False skips every document-level default EXCEPT the
    still-protected ones (PROTECTED_SYSTEM_FIELDS — summary, document_type at
    this level). It exists for the create-collection wizard: the optional
    defaults — title, topics, keywords, entities, organizations,
    temporal_context, document_outline — are only created if the user's own
    Step 4 field list includes them, so this call must not force-create them
    all up front. Template
    fields are never gated by this flag — picking a template is itself an
    explicit choice, unlike the universal defaults.
    """
    # Only initialize fields for enabled metadata levels to avoid
    # creating unused chunk/document field configs
    allowed_levels = set()
    if document_level_metadata:
        allowed_levels.add("document")
    if chunk_level_metadata:
        allowed_levels.add("chunk")

    # Template fields are seeded ON TOP of the universal defaults, never instead
    # of them. Accepts the FieldTemplate enum or its plain string value.
    template_key = getattr(template, "value", template)
    template_fields = DOMAIN_FIELD_TEMPLATES.get(template_key, [])
    if template_key and template_key != "default" and not template_fields:
        logger.warning(f"Unknown field template '{template_key}' for knowledge {knowledge_id} — seeding defaults only")

    default_fields = [
        f for f in DEFAULT_FIELD_CONFIGURATIONS
        if f["level"] in allowed_levels
        and (seed_optional_defaults or f["level"] != "document" or f["field_name"] in PROTECTED_SYSTEM_FIELDS)
    ]
    template_fields = [f for f in template_fields if f["level"] in allowed_levels]
    fields_to_initialize = default_fields + template_fields

    if not fields_to_initialize:
        logger.warning(f"No fields to initialize for knowledge {knowledge_id} — both metadata levels disabled")
        return

    field_names = [f["field_name"] for f in fields_to_initialize]

    async with async_session() as session:
        # Single query to find all existing fields, avoiding N+1
        statement = select(FieldConfiguration.field_name).where(
            and_(
                FieldConfiguration.knowledge_id == knowledge_id,
                FieldConfiguration.field_name.in_(field_names)
            )
        )
        result = await session.execute(statement)
        existing_names = set(result.scalars().all())

        for field_data in fields_to_initialize:
            if field_data["field_name"] not in existing_names:
                session.add(FieldConfiguration(knowledge_id=knowledge_id, **field_data))

        await session.commit()
        logger.info(
            f"Initialized field configurations for knowledge {knowledge_id}, "
            f"template={template or 'default'}, levels={allowed_levels}, "
            f"skipped={len(existing_names)}, created={len(field_names) - len(existing_names)}"
        )


@retry_db_connection(max_retries=3)
@track_time("DB: Get Enrichment Default Config")
async def get_enrichment_default_config() -> Optional[Dict[str, Any]]:
    """
    Retrieve the latest global enrichment default config (highest id = most recent insert).
    Returns None if no rows exist yet.
    """
    try:
        async with async_session() as session:
            result = await session.execute(
                select(EnrichmentDefaultConfig).order_by(desc(EnrichmentDefaultConfig.id)).limit(1)
            )
            config = result.scalars().first()
            if not config:
                logger.warning("enrichment_default_config has no rows — migration may not have run")
                return None
            return {
                "enrichment_model": config.enrichment_model,
                "enrichment_provider": config.enrichment_provider,
                "enrichment_temperature": config.enrichment_temperature,
                "enrichment_fallback_model": config.enrichment_fallback_model,
                "enrichment_fallback_provider": config.enrichment_fallback_provider,
                "updated_at": config.updated_at.isoformat() if config.updated_at else None,
            }
    except Exception as e:
        logger.error(f"Error retrieving enrichment_default_config: {e}")
        raise


@retry_db_connection(max_retries=3)
@track_time("DB: Update Enrichment Default Config")
async def update_enrichment_default_config(
    enrichment_model: Optional[str] = None,
    enrichment_provider: Optional[str] = None,
    enrichment_temperature: Optional[float] = None,
    enrichment_fallback_model: Optional[str] = None,
    enrichment_fallback_provider: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Insert a new versioned row into enrichment_default_config.
    Inherits unchanged fields from the current latest row.
    Returns the newly inserted config dict.
    """
    try:
        async with async_session() as session:
            # Fetch current latest to carry forward unchanged fields
            result = await session.execute(
                select(EnrichmentDefaultConfig).order_by(desc(EnrichmentDefaultConfig.id)).limit(1)
            )
            current = result.scalars().first()
            if not current:
                raise ValueError("enrichment_default_config has no rows — run migration first")

            new_row = EnrichmentDefaultConfig(
                enrichment_model=enrichment_model if enrichment_model is not None else current.enrichment_model,
                enrichment_provider=enrichment_provider if enrichment_provider is not None else current.enrichment_provider,
                enrichment_temperature=enrichment_temperature if enrichment_temperature is not None else current.enrichment_temperature,
                enrichment_fallback_model=enrichment_fallback_model if enrichment_fallback_model is not None else current.enrichment_fallback_model,
                enrichment_fallback_provider=enrichment_fallback_provider if enrichment_fallback_provider is not None else current.enrichment_fallback_provider,
                updated_at=datetime.now(),
            )
            session.add(new_row)
            await session.commit()
            await session.refresh(new_row)

            logger.info(
                f"Inserted new enrichment_default_config id={new_row.id}: "
                f"model={new_row.enrichment_model} ({new_row.enrichment_provider}), "
                f"fallback={new_row.enrichment_fallback_model} ({new_row.enrichment_fallback_provider})"
            )
            return {
                "enrichment_model": new_row.enrichment_model,
                "enrichment_provider": new_row.enrichment_provider,
                "enrichment_temperature": new_row.enrichment_temperature,
                "enrichment_fallback_model": new_row.enrichment_fallback_model,
                "enrichment_fallback_provider": new_row.enrichment_fallback_provider,
                "updated_at": new_row.updated_at.isoformat() if new_row.updated_at else None,
            }
    except Exception as e:
        logger.error(f"Error inserting enrichment_default_config: {e}")
        raise
