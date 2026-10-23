"""
Knowledge retrieval and insertion operations.
"""

from typing import Any, Dict, Optional, List
from datetime import datetime
from sqlmodel import select, and_
from sqlalchemy import or_
from sqlalchemy import func, update as sqlalchemy_update

from utils.logger import logger
from utils.tracing import track_time
from utils.pydantic_models_knowledge import (
    # Users,  # users table not owned by this service
    Document,
    # ExtractedMetadata,
    # ChatbotDetails,
    KnowledgeConfig,
    Batch,
)
from utils.models.enrichment import FieldConfiguration
from utils.db.session import async_session
from utils.db.retry_utils import retry_db_connection


# count_weblinks_for_user removed 2026-08-20 with its sole caller
# get_user_weblink_count (see below) and validate_weblink_count. See
# docs/plan/usage-and-limits/archive/design-limit-enforcement.md §8a for why the trio was dead.


# @retry_db_connection(max_retries=3)
# async def retrieve_agentic_rag_details(user_id: str, chatbot_id: str):
#     try:
#         async with async_session() as session:
#             statement = select(ChatbotDetails.agentic_rag).where(
#                 and_(
#                     ChatbotDetails.user_id == user_id,
#                     ChatbotDetails.chatbot_id == chatbot_id,
#                     ChatbotDetails.deleted == False
#                 )
#             )
#             result = await session.execute(statement)
#             agentic_rag = result.scalar_one_or_none()

#         if agentic_rag is not None:
#             logger.info(f"Retrieved agentic rag details for user ID: {user_id}, Chatbot ID: {chatbot_id}")
#             logger.info(f"Agentic rag details: {agentic_rag}")
#             return agentic_rag
#         else:
#             logger.info(f"No agentic rag details found for user ID: {user_id}, Chatbot ID: {chatbot_id}")
#             return False
#     except Exception as e:
#         logger.error(f"Database error occurred during retrieve_agentic_rag_details: {str(e)}")
#         raise


# @retry_db_connection(max_retries=3)
# async def get_hybrid_search_value(user_id: str, chatbot_id: str):
#     try:
#         async with async_session() as session:
#             statement = select(ChatbotDetails.hybrid_search, ChatbotDetails.embedding_model).where(
#                 and_(
#                     ChatbotDetails.user_id == user_id,
#                     ChatbotDetails.chatbot_id == chatbot_id,
#                     ChatbotDetails.deleted == False
#                 )
#             )
#             result = await session.execute(statement)
#             row = result.first()

#         if row:
#             logger.info(f"Retrieved hybrid search value for user ID: {user_id}, Chatbot ID: {chatbot_id}")
#             return row[0], row[1]
#         else:
#             logger.info(f"No hybrid search value found for user ID: {user_id}, Chatbot ID: {chatbot_id}")
#             return None, None
#     except Exception as e:
#         logger.error(f"Database error occurred during get_hybrid_search_value: {str(e)}")
#         raise


def _processing_mode_value(mode) -> Optional[str]:
    """The stored string for a processing mode, whichever form the caller holds.

    Routers carry the ``DocumentProcessingMode`` enum, handlers annotate the same thing
    as ``str``, and the column is ``VARCHAR``. Note that ``str(enum_member)`` would give
    ``"DocumentProcessingMode.VISUAL"``, not ``"visual"`` — hence ``.value`` rather than a
    cast. Normalising here, at the one boundary that writes the column, keeps every call
    site free to pass whichever form it already has.
    """
    return getattr(mode, "value", mode)


@retry_db_connection(max_retries=3)
@track_time("DB: Insert Document")
async def insert_extracted_text(
    user_id: Optional[str], knowledge_id: str,
    object_id: str,
    file_name: Optional[str] = None,
    use_llama_parse: bool = False, url: Optional[str] = None,
    deleted: bool = False, contextualise_chunks: Optional[bool] = False,
    chunk_size: Optional[int] = None,
    content_type: Optional[str] = None,
    source_knowledge_id: Optional[str] = None,
    source_url: Optional[str] = None,
    batch_id: Optional[str] = None,
    processing_status: Optional[str] = 'processing',
    error_detail: Optional[str] = None,
    extra_metadata: Optional[dict] = None,
    content: Optional[str] = None,
    document_processing_mode: Optional[str] = None):
    try:
        async with async_session() as session:
            document = Document(
                user_id=user_id,
                knowledge_id=knowledge_id,
                object_id=object_id,
                file_name=file_name,
                content=content,
                llama_parse=use_llama_parse,
                url=str(url) if url else None,
                deleted=deleted,
                contextualise_chunks=contextualise_chunks,
                chunk_size=chunk_size,
                processing_status=processing_status,
                content_type=content_type,
                source_knowledge_id=source_knowledge_id,
                source_url=source_url,
                batch_id=batch_id,
                error_detail=error_detail,
                extra_metadata=extra_metadata,
                # Written alongside llama_parse, not instead of it: the boolean stays for
                # readers that predate modes, the mode carries what the boolean cannot say.
                document_processing_mode=_processing_mode_value(document_processing_mode),
            )
            session.add(document)
            await session.commit()
            await session.refresh(document)
            logger.info(f"Inserted extracted text for user ID: {user_id}, Knowledge ID: {knowledge_id}, Object ID: {object_id}")
            return document.id, object_id
    except Exception as e:
        logger.error(f"Database error occurred during insert_extracted_text: {str(e)}")
        raise


@retry_db_connection(max_retries=3)
@track_time("DB: Get Documents")
async def retrieve_chatbot_knowledge(user_id: Optional[str], knowledge_id: str, content_type: Optional[str] = None) -> Optional[List[dict]]:
    try:
        async with async_session() as session:
            statement = select(Document).where(
                and_(
                    # Document.user_id == user_id,
                    Document.knowledge_id == knowledge_id,
                    Document.deleted == False,
                    Document.processing_status != "invalid",
                )
            )
            # if user_id is not None:
            #     statement = statement.where(Document.user_id == user_id)

            if content_type is not None:
                statement = statement.where(Document.content_type == content_type)

            result = await session.execute(statement)
            rows = result.scalars().all()

        if rows:
            logger.info(f"Retrieved chatbot knowledge for user ID: {user_id}, Knowledge ID: {knowledge_id}, Content Type: {content_type}")

            # Create a dictionary to store main URLs and their sub-URLs
            main_urls = {}
            sub_urls = []

            # First pass: Process all rows and separate main URLs and sub-URLs
            for row in rows:
                item = {
                    "id": str(row.id),
                    "object_id": row.object_id,
                    "file_name": row.file_name if row.file_name else None,
                    "status": row.processing_status if row.processing_status else None,
                    "url": row.url if row.url else None,
                    "source_url": row.source_url if row.source_url else None,
                    "created_time": row.created_at.isoformat() if isinstance(row.created_at, datetime) else str(row.created_at),
                    "updated_time": row.updated_at.isoformat() if isinstance(row.updated_at, datetime) else str(row.updated_at),
                    "content_type": row.content_type
                }

                if row.source_object_id is None:
                    # This is a main URL
                    main_urls[row.object_id] = item
                else:
                    # This is a sub-URL
                    sub_urls.append({
                        **item,
                        "source_object_id": row.source_object_id,
                        "source_url": row.source_url
                    })

            # Second pass: Group sub-URLs under their main URLs
            result = []
            for main_object_id, main_item in main_urls.items():
                # Find all sub-URLs for this main URL
                related_sub_urls = [
                    sub for sub in sub_urls 
                    if sub["source_object_id"] == main_object_id
                ]

                if related_sub_urls:
                    main_item["sub_urls"] = related_sub_urls

                result.append(main_item)

            # Add any orphaned sub-URLs (those whose main URL might have been deleted)
            orphaned_sub_urls = [
                sub for sub in sub_urls 
                if sub["source_object_id"] not in main_urls
            ]
            result.extend(orphaned_sub_urls)

            return result
        else:
            logger.info(f"No chatbot knowledge found for user ID: {user_id}, Knowledge ID: {knowledge_id}, Content Type: {content_type}")
            return []
    except Exception as e:
        logger.error(f"Database error occurred during retrieve_chatbot_knowledge: {str(e)}")
        raise


@retry_db_connection(max_retries=3)
@track_time("DB: Get Processing Status")
async def retrieve_knowledge_processing_status(knowledge_id: str, object_id: str, user_id: Optional[str]=None):
    """Retrieves (status, error_detail) for a knowledge item.

    error_detail was always on the Document row (Document.error_detail) but
    this function used to select processing_status alone, so a failed
    document's status endpoint had no way to say why — the UI could only
    show the word "failed". Selecting both columns here costs nothing extra
    (single indexed row lookup) and lets the one caller
    (routers/document_management.py's get_document_status) surface the real
    reason instead of a bare status string.

    Returns (None, None) if no matching row exists, matching the old
    bare-None-on-miss contract closely enough that
    validate_knowledge_item_exists (checks status is None) still works
    unchanged against the first tuple element.
    """
    try:
        async with async_session() as session:
            statement = select(Document.processing_status, Document.error_detail).where(
                and_(
                    Document.knowledge_id == knowledge_id,
                    Document.object_id == object_id,
                    Document.deleted == False,
                    # Document.source_knowledge_id == None
                )
            )
            if user_id:
                statement = statement.where(
                    or_(Document.user_id == user_id, Document.user_id.is_(None))
                )
            result = await session.execute(statement)
            row = result.one_or_none()
            status, error_detail = row if row else (None, None)

        logger.info(f"Found processing status: {status}" if status else "No processing status found")
        return status, error_detail
    except Exception as e:
        logger.error(f"Error retrieving processing status: {e}")
        raise


@retry_db_connection(max_retries=3)
@track_time("DB: Get Search Config")
async def get_chatbot_search_config(knowledge_id: str) -> tuple:
    """
    Get hybrid search configuration and embedding model for a knowledge service from KnowledgeConfig table.
    """
    try:
        async with async_session() as session:
            statement = select(KnowledgeConfig.hybrid_search_enabled, KnowledgeConfig.embedding_model).where(
                KnowledgeConfig.knowledge_id == knowledge_id
            )
            result = await session.execute(statement)
            row = result.first()

        if row:
            hybrid_search = row[0]
            embedding_model = row[1]
            logger.info(f"Retrieved search config from KnowledgeConfig for knowledge {knowledge_id}: hybrid_search={hybrid_search}, embedding_model={embedding_model}")
            return hybrid_search, embedding_model
        else:
            raise ValueError(f"No config found for knowledge {knowledge_id}")
    except Exception as e:
        logger.error(f"Error retrieving search config: {e}")
        raise


# @retry_db_connection(max_retries=3)
# async def get_agentic_rag_config(user_id: str, chatbot_id: str):
#     """
#     Get agentic RAG configuration for a chatbot.
#     """
#     try:
#         agentic_rag = await retrieve_agentic_rag_details(user_id, chatbot_id)
#         logger.info(f"Retrieved agentic RAG config for chatbot {chatbot_id}")
#         return agentic_rag
#     except Exception as e:
#         logger.error(f"Error retrieving agentic RAG config: {e}")
#         raise


# get_user_weblink_count removed 2026-08-20 along with validate_weblink_count —
# the caller pair enforced nothing (validate_weblink_count's `raise` was commented
# out), so this ran a real per-user COUNT query on every weblink upload to feed a
# check that could not fail. Real ceiling now: OwnerPlatformLimits.max_knowledge_items.
# See docs/plan/usage-and-limits/archive/design-limit-enforcement.md §8a.


@retry_db_connection(max_retries=3)
@track_time("DB: Get User Id From Email")
async def get_user_id_from_email(email):
    """Retrieve user ID from the database based on the email."""
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


@retry_db_connection(max_retries=3)
@track_time("DB: Create Knowledge Service")
async def create_knowledge_service(knowledge_id: str, owner_id: str, collection_name: Optional[str] = None) -> str:
    """
    Create a new knowledge service in the database.

    Args:
        knowledge_id: Unique knowledge service identifier
        owner_id: Owner/user identifier
        collection_name: Optional short display name (G1). Was a dead
            `service_name` parameter here, accepted but never written to the
            row — this finishes that.

    Returns:
        The created knowledge_id

    Raises:
        Exception: If database operation fails
    """
    try:
        async with async_session() as session:
            knowledge_service = KnowledgeConfig(
                knowledge_id=knowledge_id,
                owner_id=owner_id,
                collection_name=collection_name,
                # Other config fields remain NULL/None initially
            )
            session.add(knowledge_service)
            await session.commit()
            await session.refresh(knowledge_service)
            
            logger.info(f"Created knowledge service {knowledge_id} for owner {owner_id}")
            return knowledge_service.knowledge_id
            
    except Exception as e:
        logger.error(f"Database error occurred during create_knowledge_service: {str(e)}")
        raise


@retry_db_connection(max_retries=3)
@track_time("DB: Get Knowledge Services By Owner")
async def get_knowledge_services_by_owner(owner_id: str) -> List[str]:
    """
    Retrieve all knowledge service IDs for a specific owner.
    
    Matching is case- and whitespace-insensitive on both sides. Every writer
    normalizes before storing (`routers/knowledge_management.py::create_knowledge`
    does `.strip().lower()`), but rows written before that normalization existed
    kept whatever casing the caller sent — so an exact `==` here silently hid the
    owner's *older* collections while listing the newer ones. Every other owner
    comparison in the service already normalizes: `/v1/validate` and
    `require_permission`'s owner fast-path compare `.strip().lower()` in Python,
    and `list_shared_knowledge` uses `func.lower()` on its column — this was the
    one exact-match lookup left. Confirmed live 2026-08-29, see
    docs/plan/access-and-sharing/api-tests.md.

    `func.lower()` on the column does not use the plain `owner_id` btree index,
    which is fine at this table's size (787 non-deleted rows in production); add
    a functional index on `lower(owner_id)` if it ever isn't.

    Args:
        owner_id: Owner/user identifier (email); case and surrounding whitespace
            are ignored

    Returns:
        List of knowledge_ids belonging to the owner

    Raises:
        Exception: If database operation fails
    """
    try:
        normalized_owner_id = owner_id.strip().lower() if owner_id else owner_id
        async with async_session() as session:
            statement = select(KnowledgeConfig.knowledge_id).where(
                and_(
                    func.lower(func.trim(KnowledgeConfig.owner_id)) == normalized_owner_id,
                    KnowledgeConfig.deleted == False
                )
            )
            result = await session.execute(statement)
            knowledge_ids = result.scalars().all()
            
            logger.info(f"Retrieved {len(knowledge_ids)} knowledge services for owner {owner_id}")
            return list(knowledge_ids)
            
    except Exception as e:
        logger.error(f"Database error occurred during get_knowledge_services_by_owner: {str(e)}")
        raise


@retry_db_connection(max_retries=3)
@track_time("DB: Get Knowledge Service")
async def get_knowledge_service(knowledge_id: str) -> Optional[dict]:
    """
    Retrieve knowledge service details by knowledge_id.
    
    Args:
        knowledge_id: Knowledge service identifier
        
    Returns:
        Dictionary with service details or None if not found
        
    Raises:
        Exception: If database operation fails
    """
    try:
        async with async_session() as session:
            statement = select(KnowledgeConfig).where(
                and_(
                    KnowledgeConfig.knowledge_id == knowledge_id,
                    KnowledgeConfig.deleted == False
                )
            )
            result = await session.execute(statement)
            service = result.scalar_one_or_none()
            
            if service:
                logger.info(f"Retrieved knowledge service {knowledge_id}")
                return {
                    "knowledge_id": service.knowledge_id,
                    "owner_id": service.owner_id,
                    # "service_name": service.service_name,
                    "created_at": service.created_at.isoformat() if service.created_at else None,
                    "updated_at": service.updated_at.isoformat() if service.updated_at else None
                }
            else:
                logger.info(f"No knowledge service found for ID: {knowledge_id}")
                return None
                
    except Exception as e:
        logger.error(f"Database error occurred during get_knowledge_service: {str(e)}")
        raise


@retry_db_connection(max_retries=3)
async def mark_document_processing(
    knowledge_id: str,
    object_id: str,
    user_id: Optional[str] = None,
    extra_metadata: Optional[dict] = None,
    content: Optional[str] = None,
    document_processing_mode: Optional[str] = None,
) -> bool:
    """Mark an existing document's processing_status to 'processing'.

    On reindex the row is reused rather than re-inserted, so any freshness metadata
    (e.g. connector `last_modified`) must be refreshed here — otherwise the dedup key
    stays frozen at the original ingest value and the doc reads as "changed" forever.
    Passed metadata is merged over the existing blob; a new dict is assigned so the
    JSONB column change is detected.

    document_processing_mode is refreshed for the same reason: re-uploading a file as
    `scanned` genuinely changes how it is read, so leaving the row at the mode of the
    original ingest would make the listing describe a run that no longer exists. None
    means "the caller didn't say", which leaves the stored mode alone.
    """
    try:
        async with async_session() as session:
            statement = select(Document).where(
                and_(
                    Document.knowledge_id == knowledge_id,
                    Document.object_id == object_id,
                    Document.deleted == False,
                )
            )
            if user_id is not None:
                statement = statement.where(
                    or_(Document.user_id == user_id, Document.user_id.is_(None))
                )
            result = await session.execute(statement)
            doc = result.scalar_one_or_none()
            if not doc:
                logger.warning(
                    f"mark_document_processing matched no row — freshness metadata NOT "
                    f"refreshed. knowledge_id={knowledge_id}, object_id={object_id}, "
                    f"user_id={user_id!r}. Document will keep reading as 'changed'."
                )
                return False
            doc.processing_status = "processing"
            doc.updated_at = datetime.now()
            # reset deletion status if any
            doc.deletion_status = None
            if extra_metadata:
                doc.extra_metadata = {**(doc.extra_metadata or {}), **extra_metadata}
            if content is not None:
                doc.content = content
            if document_processing_mode is not None:
                doc.document_processing_mode = _processing_mode_value(document_processing_mode)
            await session.commit()
            return True
    except Exception as e:
        logger.error(f"Error marking document processing: {e}")
        raise


@track_time("DB: Get Document By Source Id")
@retry_db_connection(max_retries=3)
async def get_document_by_source_id(knowledge_id: str, source_id: str) -> Optional[dict]:
    """Resolve the live document for a connector source_id (extra_metadata->>'source_id').

    Authoritative dedup key written synchronously at submit time — unlike the Qdrant
    chunk lookup, this row exists the moment a document is accepted, so it closes the
    race where the same source_id is posted twice before the first run's chunks land.

    Returns {object_id, last_modified, processing_status} for the most recent non-deleted
    match, or None if the source_id has never been ingested.
    """
    try:
        async with async_session() as session:
            statement = (
                select(Document)
                .where(
                    and_(
                        Document.knowledge_id == knowledge_id,
                        Document.deleted == False,
                        Document.extra_metadata["source_id"].astext == str(source_id),
                    )
                )
                .order_by(Document.id.desc())
                .limit(1)
            )
            result = await session.execute(statement)
            doc = result.scalar_one_or_none()
            if not doc:
                return None
            meta = doc.extra_metadata or {}
            raw_lm = meta.get("last_modified")
            try:
                last_modified = int(raw_lm) if raw_lm is not None else None
            except (TypeError, ValueError):
                last_modified = None
            return {
                "object_id": doc.object_id,
                "last_modified": last_modified,
                "processing_status": doc.processing_status,
            }
    except Exception as e:
        # Non-fatal: a failed lookup must never block ingestion — caller falls back to
        # treating the document as new (the prior behavior).
        logger.warning(f"Error resolving document by source_id='{source_id}': {e}")
        return None


@retry_db_connection(max_retries=3)
async def mark_document_invalid(knowledge_id: str, object_id: str, user_id: Optional[str] = None, error_detail: Optional[str] = None) -> bool:
    """Mark an existing document's processing_status to 'invalid'."""
    try:
        async with async_session() as session:
            statement = select(Document).where(
                and_(
                    Document.knowledge_id == knowledge_id,
                    Document.object_id == object_id,
                    Document.deleted == False,
                )
            )
            if user_id is not None:
                statement = statement.where(
                    or_(Document.user_id == user_id, Document.user_id.is_(None))
                )
            result = await session.execute(statement)
            doc = result.scalar_one_or_none()
            if not doc:
                logger.warning(
                    f"mark_document_invalid matched no row. knowledge_id={knowledge_id}, "
                    f"object_id={object_id}, user_id={user_id!r}"
                )
                return False
            doc.processing_status = "invalid"
            doc.error_detail = error_detail
            await session.commit()
            return True
    except Exception as e:
        logger.error(f"Error marking document invalid: {e}")
        raise



@retry_db_connection(max_retries=3)
async def get_document_source_info(knowledge_id: str, object_id: str) -> Optional[dict]:
    """Retrieve source info (file_name, source_url, content_type) for a document."""
    try:
        async with async_session() as session:
            statement = select(
                Document.object_id,
                Document.file_name,
                Document.source_url,
                Document.content_type,
            ).where(
                and_(
                    Document.knowledge_id == knowledge_id,
                    Document.object_id == object_id,
                    Document.deleted == False,
                )
            )
            result = await session.execute(statement)
            row = result.first()
        if row:
            return {
                "object_id": row.object_id,
                "file_name": row.file_name,
                "source_url": row.source_url,
                "content_type": row.content_type,
            }
        return None
    except Exception as e:
        logger.error(f"Error retrieving document source info: {e}")
        raise


@retry_db_connection(max_retries=3)
@track_time("DB: Get Documents By Batch")
async def get_documents_by_batch_id(knowledge_id: str, batch_id: str, user_id: Optional[str] = None) -> List[Document]:
    """Retrieve documents by batch_id and knowledge_id, optionally filtered by user_id."""
    try:
        async with async_session() as session:
            statement = select(Document).where(
                and_(
                    Document.knowledge_id == knowledge_id,
                    Document.batch_id == batch_id,
                    Document.deleted == False,
                )
            )
            if user_id is not None:
                statement = statement.where(
                    or_(Document.user_id == user_id, Document.user_id.is_(None))
                )
            result = await session.execute(statement)
            documents = list(result.scalars().all())
            logger.info(f"Retrieved {len(documents)} documents for batch_id: {batch_id}, knowledge_id: {knowledge_id}")
            return documents
    except Exception as e:
        logger.error(f"Error retrieving documents by batch_id: {e}")
        raise


@retry_db_connection(max_retries=3)
async def get_documents_by_batch_id(knowledge_id: str, batch_id: str, user_id: Optional[str] = None) -> List[Document]:
    """Retrieve documents by batch_id and knowledge_id, optionally filtered by user_id."""
    try:
        async with async_session() as session:
            statement = select(Document).where(
                and_(
                    Document.knowledge_id == knowledge_id,
                    Document.batch_id == batch_id,
                    Document.deleted == False,
                )
            )
            if user_id is not None:
                statement = statement.where(
                    or_(Document.user_id == user_id, Document.user_id.is_(None))
                )
            result = await session.execute(statement)
            return list(result.scalars().all())
    except Exception as e:
        logger.error(f"Error retrieving documents by batch_id: {e}")
        raise


@retry_db_connection(max_retries=3)
@track_time("DB: Create Or Update Batch")
async def create_or_update_batch(
    batch_id: str,
    knowledge_id: str,
    user_id: Optional[str] = None,
    status: str = "processing",
    total_documents: int = 0
) -> Batch:
    """
    Create or update batch metadata in the batches table.
    
    Args:
        batch_id: Unique batch identifier
        knowledge_id: Knowledge service identifier
        user_id: User identifier (optional)
        status: Batch processing status (default: 'processing')
        total_documents: Total number of documents in the batch (default: 0)
        
    Returns:
        The created or updated Batch object
        
    Raises:
        Exception: If database operation fails
    """
    try:
        async with async_session() as session:
            # Check if batch already exists
            statement = select(Batch).where(Batch.batch_id == batch_id)
            result = await session.execute(statement)
            existing_batch = result.scalars().first()
            
            if existing_batch:
                # Update existing batch
                existing_batch.status = status
                existing_batch.total_documents = total_documents
                if user_id:
                    existing_batch.user_id = user_id
                await session.commit()
                await session.refresh(existing_batch)
                logger.info(f"Updated batch {batch_id} with status: {status}, total_documents: {total_documents}")
                return existing_batch
            else:
                # Create new batch
                batch = Batch(
                    batch_id=batch_id,
                    knowledge_id=knowledge_id,
                    user_id=user_id,
                    status=status,
                    total_documents=total_documents
                )
                session.add(batch)
                await session.commit()
                await session.refresh(batch)
                logger.info(f"Created batch {batch_id} for knowledge_id: {knowledge_id}, total_documents: {total_documents}")
                return batch
    except Exception as e:
        logger.error(f"Database error occurred during create_or_update_batch: {str(e)}")
        raise


@retry_db_connection(max_retries=3)
@track_time("DB: Get Batch By Id")
async def get_batch_by_id(batch_id: str):
    """Retrieve a Batch record by batch_id."""
    try:
        async with async_session() as session:
            statement = select(Batch).where(Batch.batch_id == batch_id)
            result = await session.execute(statement)
            return result.scalar_one_or_none()
    except Exception as e:
        logger.error(f"Error retrieving batch {batch_id}: {e}")
        raise


@retry_db_connection(max_retries=3)
@track_time("DB: Update Presigned Url")
async def update_document_presigned_url(
    knowledge_id: str,
    object_id: str,
    presigned_url: str,
    expires_at: datetime,
) -> bool:
    """Update the cached presigned URL for a document.

    Args:
        knowledge_id: Knowledge service identifier
        object_id: Document object identifier
        presigned_url: The new presigned URL to cache
        expires_at: When the presigned URL expires

    Returns:
        True if update succeeded, False if document not found
    """
    try:
        async with async_session() as session:
            statement = select(Document).where(
                and_(
                    Document.knowledge_id == knowledge_id,
                    Document.object_id == object_id,
                    Document.deleted == False,
                )
            )
            result = await session.execute(statement)
            doc = result.scalar_one_or_none()
            if not doc:
                logger.warning(f"Document not found for presigned URL update: {knowledge_id}/{object_id}")
                return False
            doc.source_presigned_url = presigned_url
            doc.source_presigned_url_expires_at = expires_at
            await session.commit()
            logger.info(f"Updated presigned URL for document {object_id}, expires at {expires_at}")
            return True
    except Exception as e:
        logger.error(f"Error updating document presigned URL: {e}")
        raise


@retry_db_connection(max_retries=3)
@track_time("DB: Batch Update Presigned Urls")
async def batch_update_document_presigned_urls(updates: list[dict]) -> None:
    """Batch update presigned URLs for multiple documents in a single transaction.

    Args:
        updates: List of dicts with keys: knowledge_id, object_id, presigned_url, expires_at
    """
    if not updates:
        return
    try:
        async with async_session() as session:
            for update in updates:
                statement = (
                    sqlalchemy_update(Document)
                    .where(
                        and_(
                            Document.knowledge_id == update['knowledge_id'],
                            Document.object_id == update['object_id'],
                            Document.deleted == False,
                        )
                    )
                    .values(
                        source_presigned_url=update['presigned_url'],
                        source_presigned_url_expires_at=update['expires_at'],
                    )
                )
                await session.execute(statement)
            await session.commit()
            logger.info(f"Batch updated presigned URLs for {len(updates)} documents")
    except Exception as e:
        logger.error(f"Error batch updating document presigned URLs: {e}")
        raise


def _derive_document_source(row) -> Optional[str]:
    """Where a document came from: the connector that supplied it, or its content type.

    Only one value is new here — `clickup`. Everything else keeps the vocabulary
    `content_type` already uses (`file`, `url`, `plain_text`), so a caller reading this
    field sees the labels it already knows plus the one that was missing.

    The gap this closes: a ClickUp page is uploaded through the ordinary file path, so
    its `content_type` is `file` and it is indistinguishable from a hand-uploaded PDF.
    The connector's identity survives only in `extra_metadata["source"]`, which is what
    this reads. `content_type` itself is left alone — it is a filter parameter, and the
    sync's own reconcile sweep depends on its current values.
    """
    metadata = row.extra_metadata or {}
    source = metadata.get("source")
    if source:
        return str(source)
    return row.content_type


@retry_db_connection(max_retries=3)
@track_time("DB: Get Documents V2")
async def retrieve_chatbot_knowledge_v2(
    user_id: Optional[str],
    knowledge_id: str,
    content_type: Optional[str] = None,
    file_name: Optional[str] = None,
    object_id: Optional[str] = None,
) -> Optional[List[dict]]:
    """Retrieve chatbot knowledge with presigned URL caching fields (v2).

    Returns documents with source_presigned_url and expiry for caching.
    file_name and object_id, when given, do a case-insensitive partial match
    so the Upload Status window can search on either field.
    """
    try:
        async with async_session() as session:
            statement = select(Document).where(
                and_(
                    Document.knowledge_id == knowledge_id,
                    Document.deleted == False,
                    Document.processing_status != "invalid",
                )
            )
            if content_type is not None:
                statement = statement.where(Document.content_type == content_type)
            if file_name:
                statement = statement.where(Document.file_name.ilike(f"%{file_name}%"))
            if object_id:
                statement = statement.where(Document.object_id.ilike(f"%{object_id}%"))

            result = await session.execute(statement)
            rows = result.scalars().all()

        if rows:
            logger.info(f"Retrieved chatbot knowledge v2 for knowledge_id: {knowledge_id}")

            main_urls = {}
            sub_urls = []

            for row in rows:
                item = {
                    "id": str(row.id),
                    "object_id": row.object_id,
                    "knowledge_id": knowledge_id,
                    "file_name": row.file_name if row.file_name else None,
                    "status": row.processing_status if row.processing_status else None,
                    # Only non-null when status == "failed" — was on the row
                    # (Document.error_detail) and simply never selected into
                    # this response before, so a failed document's UI card
                    # could only say "failed" with no reason a user could act on.
                    "error_detail": row.error_detail if row.error_detail else None,
                    "url": row.url if row.url else None,
                    "source_url": row.source_url if row.source_url else None,
                    "source_presigned_url": row.source_presigned_url if row.source_presigned_url else None,
                    "source_presigned_url_expires_at": row.source_presigned_url_expires_at,
                    "created_time": row.created_at.isoformat() if isinstance(row.created_at, datetime) else str(row.created_at),
                    "updated_time": row.updated_at.isoformat() if isinstance(row.updated_at, datetime) else str(row.updated_at),
                    "content_type": row.content_type,
                    # Where the document came from: "clickup" for a synced page,
                    # otherwise the content_type value unchanged. Additive — content_type
                    # itself keeps its values, so the filter param and the ClickUp
                    # connector's own reconcile sweep are unaffected.
                    "source": _derive_document_source(row),
                    # The mode this document was requested to be processed in. Null only
                    # for rows written before migration 042 ran, or by an insert path that
                    # forgot to set it.
                    "document_processing_mode": row.document_processing_mode,
                }

                if row.source_object_id is None:
                    main_urls[row.object_id] = item
                else:
                    # Crawled child pages inherit both labels from this same item dict —
                    # a sub-page of a ClickUp-sourced or weblink document is that source too.
                    sub_urls.append({
                        **item,
                        "source_object_id": row.source_object_id,
                    })

            result_list = []
            for main_object_id, main_item in main_urls.items():
                related_sub_urls = [
                    sub for sub in sub_urls
                    if sub["source_object_id"] == main_object_id
                ]
                if related_sub_urls:
                    main_item["sub_urls"] = related_sub_urls
                result_list.append(main_item)

            orphaned_sub_urls = [
                sub for sub in sub_urls
                if sub["source_object_id"] not in main_urls
            ]
            result_list.extend(orphaned_sub_urls)

            return result_list
        else:
            logger.info(f"No chatbot knowledge found for knowledge_id: {knowledge_id}")
            return []
    except Exception as e:
        logger.error(f"Database error during retrieve_chatbot_knowledge_v2: {str(e)}")
        raise


@retry_db_connection(max_retries=3)
async def get_knowledge_summaries(knowledge_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Aggregate per-knowledge_id summary fields for Home cards: document
    count + processing rollup, active field count + custom field count.

    Three grouped-count queries total (not N+1 per knowledge_id) — one each
    over Document, and FieldConfiguration split by is_custom — each grouped
    by knowledge_id and filtered to the given id list, so this scales with
    query count, not with len(knowledge_ids).

    Rollup buckets are "completed" / "processing" / "failed", matching the
    convention already used by GET /v1/{knowledge_id}/batch/{batch_id}/status
    (routers/batch_document_management.py) — NOT the "queued/processing/
    ready/failed" wording from the product mock, which doesn't correspond to
    any processing_status value this service actually writes (real values:
    processing, completed, failed, invalid, deleted, deleting — there is no
    "queued" state; "invalid" folds into "failed" here as it does there).

    Returns a dict keyed by knowledge_id; every id in the input list gets an
    entry (zeros if it has no documents/fields yet), so callers don't need
    a membership check before indexing in.
    """
    summaries: Dict[str, Dict[str, Any]] = {
        kid: {
            "document_count": 0,
            "processing": {"completed": 0, "processing": 0, "failed": 0},
            "field_count": 0,
            "custom_field_count": 0,
            "collection_name": None,
            "service_description": None,
            "enrichment_enabled": None,
        }
        for kid in knowledge_ids
    }
    if not knowledge_ids:
        return summaries

    try:
        async with async_session() as session:
            config_rows = await session.execute(
                select(
                    KnowledgeConfig.knowledge_id,
                    KnowledgeConfig.collection_name,
                    KnowledgeConfig.service_description,
                    KnowledgeConfig.enrichment_enabled,
                ).where(
                    KnowledgeConfig.knowledge_id.in_(knowledge_ids),
                    KnowledgeConfig.deleted == False,
                )
            )
            for knowledge_id, collection_name, service_description, enrichment_enabled in config_rows.all():
                bucket = summaries.get(knowledge_id)
                if bucket is None:
                    continue
                bucket["collection_name"] = collection_name
                bucket["service_description"] = service_description
                bucket["enrichment_enabled"] = enrichment_enabled

            doc_rows = await session.execute(
                select(Document.knowledge_id, Document.processing_status, func.count())
                .where(
                    Document.knowledge_id.in_(knowledge_ids),
                    Document.deleted == False,
                )
                .group_by(Document.knowledge_id, Document.processing_status)
            )
            for knowledge_id, status, count in doc_rows.all():
                bucket = summaries.get(knowledge_id)
                if bucket is None:
                    continue
                bucket["document_count"] += count
                if status == "completed":
                    bucket["processing"]["completed"] += count
                elif status in ("failed", "invalid"):
                    bucket["processing"]["failed"] += count
                else:
                    # processing/pending/unset, and "deleted"/"deleting" rows
                    # that slipped past the deleted=False filter due to a
                    # race — treated as in-flight rather than dropped.
                    bucket["processing"]["processing"] += count

            field_rows = await session.execute(
                select(FieldConfiguration.knowledge_id, FieldConfiguration.is_custom, func.count())
                .where(
                    FieldConfiguration.knowledge_id.in_(knowledge_ids),
                    FieldConfiguration.is_active == True,
                    FieldConfiguration.deleted == False,
                )
                .group_by(FieldConfiguration.knowledge_id, FieldConfiguration.is_custom)
            )
            for knowledge_id, is_custom, count in field_rows.all():
                bucket = summaries.get(knowledge_id)
                if bucket is None:
                    continue
                bucket["field_count"] += count
                if is_custom:
                    bucket["custom_field_count"] += count

        return summaries
    except Exception as e:
        logger.error(f"Error aggregating knowledge summaries for {len(knowledge_ids)} id(s): {e}")
        return summaries

