import traceback
import psycopg2
from psycopg2 import pool
from typing import Optional, List, Dict, Any
import time
from pydantic import BaseModel, Field
from datetime import datetime
import json
import psycopg2.extras
from sqlmodel import SQLModel, Field as SQLField, create_engine, Session, select, and_, text, desc
from sqlalchemy import JSON
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager
from collections import defaultdict

from services.celery_logger import logger
from services import celery_setup_env
from celery_utils.db.db_pool import db_pool_manager
from celery_utils.shared.tracing import track_time

from services.pydantic_models_db import (
    Document, ChatbotDetails, DocumentsIndex,
    FieldConfiguration as FieldConfigurationDB, ExtractedMetadata, KnowledgeConfig,
    EnrichmentDefaultConfig,
)

# ---------------------------------------------------------------------------
# Engine & Session Setup
# ---------------------------------------------------------------------------

engine = None
async_engine = None

def get_engine():
    global engine
    if engine is None:
        database_url = celery_setup_env.get_database_url()
        engine = create_engine(database_url, echo=False)
    return engine

def get_async_engine():
    global async_engine
    if async_engine is None:
        sync_database_url = celery_setup_env.get_database_url()
        async_database_url = sync_database_url.replace('postgresql://', 'postgresql+asyncpg://')
        async_engine = create_async_engine(
            async_database_url, echo=False, future=True,
            pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=3600,
        )
    return async_engine

def get_session():
    return Session(get_engine())

AsyncSessionFactory = None
SyncSessionFactory = None

def get_async_session_factory():
    global AsyncSessionFactory
    if AsyncSessionFactory is None:
        AsyncSessionFactory = sessionmaker(get_async_engine(), class_=AsyncSession, expire_on_commit=False)
    return AsyncSessionFactory

def get_sync_session_factory():
    global SyncSessionFactory
    if SyncSessionFactory is None:
        SyncSessionFactory = sessionmaker(bind=get_engine(), class_=Session, expire_on_commit=False)
    return SyncSessionFactory

async def get_async_session():
    async_session_factory = get_async_session_factory()
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

@contextmanager
def get_sync_session():
    sync_session_factory = get_sync_session_factory()
    session = sync_session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

# ---------------------------------------------------------------------------
# Raw SQL Execution (psycopg2 pool)
# ---------------------------------------------------------------------------

_db_pool = None

def set_db_pool(pool):
    global _db_pool
    _db_pool = pool

def get_db_pool():
    global _db_pool
    return _db_pool

def execute_sql_query(query, *args, fetchone=False, fetchall=False, fetchval=False):
    MAX_RETRIES = 3
    RETRY_DELAY = 2
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            pool = db_pool_manager.get_pool()
            conn = pool.getconn()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute(query, args)
                    if fetchone:
                        result = cur.fetchone()
                    elif fetchall:
                        result = cur.fetchall()
                    elif fetchval:
                        row = cur.fetchone()
                        result = row[0] if row else None
                    else:
                        result = f"{cur.rowcount} rows affected"
                    conn.commit()
                    return result
            finally:
                pool.putconn(conn)
        except psycopg2.pool.PoolError:
            logger.warning(f"Connection pool exhausted. Retry {attempt}/{MAX_RETRIES}")
        except psycopg2.OperationalError as e:
            logger.error(f"Database connection error: {e}")
            db_pool_manager.close()
        except psycopg2.DatabaseError as e:
            logger.warning(f"Database operation error: {e}. Retry {attempt}/{MAX_RETRIES}")
        except Exception as e:
            logger.error(f"Unexpected database error: {str(e)}\n{traceback.format_exc()}")
            break
        time.sleep(RETRY_DELAY)

    logger.error("Failed to execute database query after retries.")
    return None

# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------

def _get_document(session, knowledge_id: str, object_id: str) -> Optional[Document]:
    """Fetch a single non-deleted document by knowledge_id + object_id."""
    statement = select(Document).where(
        Document.knowledge_id == knowledge_id,
        Document.object_id == object_id,
        Document.deleted == False
    )
    return session.execute(statement).scalar_one_or_none()

def db_rows_affected_log(rows_affected: Optional[int], success_message: str, error_message: str):
    try:
        if rows_affected is None:
            logger.error(f"Operation returned None. {error_message}")
            return
        count = int(rows_affected.split()[0]) if isinstance(rows_affected, str) else rows_affected
        if count == 0:
            logger.error(f"Rows affected: {count}. {error_message}")
        else:
            logger.info(f"Rows affected: {count}. {success_message}")
    except (ValueError, Exception) as e:
        logger.error(f"Error in db_rows_affected_log: {e}")

# ---------------------------------------------------------------------------
# Document Status Operations
# ---------------------------------------------------------------------------

@track_time("DB: Retrieve Processing Status")
def retrieve_knowledge_processing_status(knowledge_id: str, object_id: str):
    try:
        with get_session() as session:
            document = _get_document(session, knowledge_id, object_id)
            if document:
                logger.info(f"Processing status for object_id={object_id}: {document.processing_status}")
                return document.processing_status
            logger.info(f"No document found for object_id={object_id}")
            return None
    except Exception as e:
        logger.error(f"Error retrieving processing status: {e}")
        return None

def save_processing_telemetry(knowledge_id: str, object_id: str, telemetry: Optional[Dict[str, Any]]) -> bool:
    """Write one document's processing telemetry (celery_utils/shared/
    document_telemetry.py) to ``document.processing_telemetry`` (migration 056).

    Its own statement, after the status write has committed, and never raises:
    a schema without the column (migration not yet run) loses the telemetry,
    never the document's status. Raw SQL rather than a mapped field on this
    repo's Document model for the same reason -- a mapped column the database
    lacks would break every Document read in the worker.
    """
    if not telemetry:
        return False
    try:
        with get_session() as session:
            session.execute(
                text(
                    "UPDATE document SET processing_telemetry = CAST(:telemetry AS JSONB) "
                    "WHERE knowledge_id = :knowledge_id AND object_id = :object_id AND deleted = false"
                ),
                {"telemetry": json.dumps(telemetry, default=str),
                 "knowledge_id": knowledge_id, "object_id": object_id},
            )
            session.commit()
        logger.info(
            f"Saved processing telemetry for object_id={object_id}: total_ms={telemetry.get('total_ms')}, "
            f"operations={[(o.get('operation'), o.get('model')) for o in telemetry.get('operations') or []]}"
        )
        return True
    except Exception as e:
        logger.warning(f"Processing telemetry not saved for object_id={object_id}: {e}")
        return False


@track_time("DB: Update Processing Status")
def update_processing_status(knowledge_id: str, object_id: str, status: str, error_detail: str = None):
    try:
        with get_session() as session:
            document = _get_document(session, knowledge_id, object_id)
            if document:
                document.processing_status = status
                if status == "failed" and error_detail:
                    document.error_detail = error_detail[:2000]
                session.commit()
                logger.info(f"Updated status to '{status}' for object_id={object_id}")
                if status == "failed":
                    # A failed document keeps what it spent before failing.
                    from celery_utils.shared import document_telemetry
                    save_processing_telemetry(
                        knowledge_id, object_id, document_telemetry.finish(object_id, status="failed")
                    )
                return 1
            logger.warning(f"Document not found for status update. knowledge_id={knowledge_id}, object_id={object_id}")
            return 0
    except Exception as e:
        logger.error(f"Error updating processing status: {e}")
        return None

@track_time("DB: Mark Task Completed")
def mark_task_completed(knowledge_id: str, object_id: str, processing_start_time: float,
                        effective_mode: Optional[str] = None):
    """Mark task as completed and store processing duration (ms) in a single DB call.

    ``effective_mode`` is the mode that actually ran — post any auto-shift — not
    necessarily the mode the caller originally requested. Optional and defaults to
    None so existing call sites that don't have this value yet (scripts/, other
    pipelines) keep working unchanged; None is never written over an existing value,
    it's simply not touched, so a caller that omits it cannot blank out a value set
    by a previous run.
    """
    try:
        duration_ms = int((time.time() - processing_start_time) * 1000)
        with get_session() as session:
            document = _get_document(session, knowledge_id, object_id)
            if document:
                document.processing_status = "completed"
                document.task_duration_ms = duration_ms
                document.completed_at = datetime.utcnow()
                if effective_mode is not None:
                    document.document_processing_mode = effective_mode
                session.commit()
                logger.info(f"Task completed for object_id={object_id}, duration: {duration_ms}ms")
                from celery_utils.shared import document_telemetry
                save_processing_telemetry(
                    knowledge_id, object_id,
                    document_telemetry.finish(object_id, status="completed", total_ms=duration_ms),
                )
                return duration_ms
            logger.warning(f"Document not found for task completion. knowledge_id={knowledge_id}, object_id={object_id}")
            return 0
    except Exception as e:
        logger.error(f"Error marking task completed for object_id={object_id}: {e}")
        return None

# ---------------------------------------------------------------------------
# Document CRUD
# ---------------------------------------------------------------------------

def create_document_record(
    user_id: Optional[str], knowledge_id: str, object_id: str,
    file_name: Optional[str] = None, use_llama_parse: bool = False,
    url: Optional[str] = None, deleted: bool = False,
    contextualise_chunks: Optional[bool] = False, chunk_size: Optional[int] = None,
    content_type: Optional[str] = None, source_object_id: Optional[str] = None,
    source_url: Optional[str] = None):
    try:
        with get_session() as session:
            new_document = Document(
                user_id=user_id, knowledge_id=knowledge_id, object_id=object_id,
                file_name=file_name, llama_parse=use_llama_parse,
                url=str(url) if url else None, deleted=deleted,
                contextualise_chunks=contextualise_chunks, chunk_size=chunk_size,
                processing_status='processing', content_type=content_type,
                source_object_id=source_object_id, source_url=source_url
            )
            session.add(new_document)
            session.commit()
            session.refresh(new_document)
            logger.info(f"Created document record for object_id={object_id}, knowledge_id={knowledge_id}")
            return new_document.id, object_id
    except Exception as e:
        logger.error(f"Error creating document record: {str(e)}")
        raise

# ---------------------------------------------------------------------------
# Chatbot / Knowledge Config Lookups
# ---------------------------------------------------------------------------

def retrieve_agentic_rag_details(user_id: str, chatbot_id: str):
    try:
        with get_session() as session:
            statement = select(ChatbotDetails.agentic_rag).where(
                and_(
                    ChatbotDetails.user_id == user_id,
                    ChatbotDetails.chatbot_id == chatbot_id,
                    ChatbotDetails.deleted == False
                )
            )
            agentic_rag = session.execute(statement).scalar_one_or_none()
            if agentic_rag is not None:
                logger.info(f"Retrieved agentic_rag for chatbot_id={chatbot_id}: {agentic_rag}")
            return agentic_rag
    except Exception as e:
        logger.error(f"Error retrieving agentic_rag details: {str(e)}")
        raise

def update_chatbot_chunking_strategy(knowledge_id: str, chunking_strategy: str) -> bool:
    try:
        with get_session() as session:
            chatbot = session.get(ChatbotDetails, knowledge_id)
            if chatbot:
                chatbot.chunking_strategy = chunking_strategy
                session.commit()
                logger.info(f"Updated chunking strategy for knowledge_id={knowledge_id}")
                return True
            logger.warning(f"Knowledge base {knowledge_id} not found")
            return False
    except Exception as e:
        logger.error(f"Error updating chunking strategy for knowledge_id={knowledge_id}: {str(e)}")
        raise

def get_chatbot_chunking_strategy_sync(knowledge_id: str) -> str | None:
    try:
        with get_session() as session:
            chatbot = session.get(ChatbotDetails, knowledge_id)
            if chatbot and chatbot.chunking_strategy:
                return chatbot.chunking_strategy
            return None
    except Exception as e:
        logger.error(f"Error retrieving chunking strategy for knowledge_id={knowledge_id}: {str(e)}")
        raise

def get_knowledge_config_sync(knowledge_id: str) -> Optional[KnowledgeConfig]:
    try:
        with get_session() as session:
            knowledge_config = session.get(KnowledgeConfig, knowledge_id)
            if knowledge_config:
                return knowledge_config
            logger.warning(f"No knowledge config for knowledge_id={knowledge_id}, using defaults")
            return KnowledgeConfig(
                knowledge_id=knowledge_id,
                document_level_metadata=True,
                chunk_level_metadata=False,
                hybrid_search_enabled=True
            )
    except Exception as e:
        logger.error(f"Error retrieving knowledge config for knowledge_id={knowledge_id}: {str(e)}")
        raise

# ---------------------------------------------------------------------------
# Metadata Models & Operations
# ---------------------------------------------------------------------------

class FieldConfiguration(BaseModel):
    id: int
    knowledge_id: str
    field_name: str
    description: Optional[str]
    field_type: str
    is_active: bool
    is_custom: bool
    is_required: bool
    sample_values: Optional[Dict[str, Any]]
    restrict_to_choices: bool
    max_items: Optional[int]
    level: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

class MetadataField(BaseModel):
    field_name: str = Field(..., description="Name of the metadata field")
    field_type: str = Field(..., description="Type of the metadata field")
    description: Optional[str] = Field(None, description="Field description")
    is_custom: bool = Field(..., description="Whether this is a custom field")
    is_required: bool = Field(..., description="Whether this field is required")
    is_active: bool = Field(..., description="Whether this field is active")
    sample_values: Optional[Any] = Field(None, description="Sample values for this field")
    restrict_to_choices: bool = Field(..., description="Whether values are restricted to choices")
    max_items: Optional[int] = Field(None, description="Maximum number of items allowed")
    values: List[Any] = Field(default_factory=list, description="Unique values extracted for this field")

class MetadataResponse(BaseModel):
    fields: List[MetadataField] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, List[Any]]:
        return {field.field_name: field.values for field in self.fields}

def _extract_unique_values(raw_values: list, field_type: str) -> list:
    """Flatten and deduplicate extracted metadata values."""
    if not raw_values:
        return []

    if field_type == 'List[str]':
        flattened = []
        for value in raw_values:
            if isinstance(value, list):
                flattened.extend(value)
            elif isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    flattened.extend(parsed) if isinstance(parsed, list) else flattened.append(parsed)
                except (json.JSONDecodeError, TypeError):
                    flattened.append(value)
            else:
                flattened.append(value)
        return list({str(v) for v in flattened if v and str(v).strip()})

    return list({
        str(v) if not isinstance(v, (dict, list)) else str(v)
        for v in raw_values if v and str(v).strip()
    })

def get_all_metadata(knowledge_id: str, user_id: Optional[str]) -> MetadataResponse:
    """Retrieve all metadata based on active field configurations and their extracted values."""
    try:
        with get_session() as session:
            field_configs = session.execute(
                select(FieldConfigurationDB).where(
                    FieldConfigurationDB.knowledge_id == knowledge_id,
                    FieldConfigurationDB.is_active == True,
                    FieldConfigurationDB.level == "document",
                    ~FieldConfigurationDB.field_type.in_(['chunks']),
                    ~FieldConfigurationDB.field_name.in_(['title', 'summary'])
                )
            ).scalars().all()

            if not field_configs:
                logger.warning(f"No active field configurations for knowledge_id={knowledge_id}")
                return MetadataResponse()

            config_ids = [c.id for c in field_configs]

            extracted_metadata = session.execute(
                select(ExtractedMetadata).where(
                    ExtractedMetadata.knowledge_id == knowledge_id,
                    ExtractedMetadata.field_configuration_id.in_(config_ids),
                    ExtractedMetadata.extracted_content.isnot(None)
                )
            ).scalars().all()

        # Group extracted content by config id
        metadata_by_config = defaultdict(list)
        for m in extracted_metadata:
            metadata_by_config[m.field_configuration_id].append(m.extracted_content)

        metadata_fields = []
        for config in field_configs:
            unique_values = _extract_unique_values(metadata_by_config.get(config.id, []), config.field_type)
            metadata_fields.append(MetadataField(
                field_name=config.field_name, field_type=config.field_type,
                description=config.description, is_custom=config.is_custom,
                is_required=config.is_required, is_active=config.is_active,
                sample_values=config.sample_values, restrict_to_choices=config.restrict_to_choices,
                max_items=config.max_items, values=unique_values
            ))
            logger.info(f"Retrieved {len(unique_values)} unique values for field '{config.field_name}'")

        return MetadataResponse(fields=metadata_fields)

    except Exception as e:
        logger.error(f"Error retrieving metadata: {e}")
        raise

def get_summaries_for_document_ids(knowledge_id: str, document_ids: List[str]) -> Dict[str, str]:
    """Retrieve summaries for document IDs using a subquery on field_configuration."""
    try:
        if not document_ids or not knowledge_id:
            return {}

        with get_session() as session:
            summary_config_subquery = select(FieldConfigurationDB.id).where(
                FieldConfigurationDB.knowledge_id == knowledge_id,
                FieldConfigurationDB.field_name == 'summary',
                FieldConfigurationDB.is_active == True,
                FieldConfigurationDB.level == "document"
            ).limit(1).scalar_subquery()

            rows = session.execute(
                select(
                    ExtractedMetadata.document_id,
                    ExtractedMetadata.extracted_content.label('summary')
                ).where(
                    ExtractedMetadata.field_configuration_id == summary_config_subquery,
                    ExtractedMetadata.document_id.in_(document_ids),
                    ExtractedMetadata.extracted_content.isnot(None)
                )
            ).fetchall()

        summaries = {}
        for row in rows:
            summary = row.summary
            if isinstance(summary, dict):
                summary = summary.get('summary', str(summary))
            summaries[str(row.document_id)] = str(summary)

        logger.info(f"Retrieved {len(summaries)} summaries for {len(document_ids)} documents")
        return summaries

    except Exception as e:
        logger.error(f"Error retrieving summaries: {str(e)}")
        return {}


@track_time("DB: Get Enrichment Default Config")
def get_enrichment_default_config_sync() -> Optional[Dict[str, Any]]:
    """
    Fetch the latest global enrichment default config (highest id = most recent insert) synchronously.
    Returns None if the table is empty (migration not run).

    Used as the middle fallback in get_enrichment_config():
        knowledge_config (per-knowledge) → enrichment_default_config (latest) → env var → hardcoded
    """
    try:
        with get_sync_session() as session:
            config = session.execute(
                select(EnrichmentDefaultConfig).order_by(desc(EnrichmentDefaultConfig.id)).limit(1)
            ).scalars().first()
            if not config:
                logger.warning("enrichment_default_config has no rows — falling back to env var")
                return None
            logger.info(
                f"Loaded enrichment_default_config id={config.id}: model={config.enrichment_model}, "
                f"provider={config.enrichment_provider}, temperature={config.enrichment_temperature}"
            )
            return {
                "enrichment_model": config.enrichment_model,
                "enrichment_provider": config.enrichment_provider,
                "enrichment_temperature": config.enrichment_temperature,
                "enrichment_fallback_model": config.enrichment_fallback_model,
                "enrichment_fallback_provider": config.enrichment_fallback_provider,
            }
    except Exception as e:
        logger.error(f"Error retrieving enrichment_default_config (sync): {e}")
        return None
