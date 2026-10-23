import uuid
import time
import json
from typing import Union, Dict, Any, Optional, TypeVar, Callable
from pydantic import BaseModel
from services.celery_logger import logger
from celery_utils.indexing.client import MultiQdrantDocumentIndexer, MultiQdrantChunkIndexer, EMBEDDING_MODELS
from celery_utils.shared.user_usage_tracking import send_vector_indexing_event, send_embedding_event
from celery_utils.shared.tracing import track_time, add_embedding_cost
from celery_utils.shared.embedding_models import embedding_cost_usd
from celery_utils.shared.credentials_utils import get_api_key
from langsmith import traceable
import services.celery_setup_env as setup_env

# Type variable for indexer types
_TIndexer = TypeVar("_TIndexer")

# Cache dictionaries for indexers keyed by embedding_model
_document_indexer_cache: Dict[str, MultiQdrantDocumentIndexer] = {}
_chunk_indexer_cache: Dict[str, MultiQdrantChunkIndexer] = {}


def _get_embedding_provider_and_model(embedding_model: str) -> tuple:
    """
    Get provider and model name from embedding model identifier.
    
    Args:
        embedding_model: Embedding model identifier
        
    Returns:
        Tuple of (provider, model_name)
    """
    model_config = EMBEDDING_MODELS.get(embedding_model)
    if not model_config:
        return "huggingface", embedding_model
    
    if model_config["type"] == "openai":
        return "openai", model_config["model_name"]
    elif model_config["type"] == "sentence_transformer":
        return "huggingface", model_config["model_name"]
    else:
        return "huggingface", embedding_model


def _get_or_create_indexer(
    cache: Dict[str, _TIndexer],
    embedding_model: str,
    constructor: Callable[[str], _TIndexer],
    initializer: Callable[[_TIndexer], None],
) -> _TIndexer:
    """
    Return cached indexer for model or construct+initialize and cache it.
    
    Args:
        cache: Dictionary to cache indexers by embedding_model
        embedding_model: The embedding model identifier
        constructor: Function to create a new indexer instance
        initializer: Function to initialize the indexer (sync)
    
    Returns:
        The cached or newly created and initialized indexer
    """
    if embedding_model not in cache:
        logger.info(f"Creating new indexer for embedding model: {embedding_model}")
        indexer = constructor(embedding_model)
        initializer(indexer)
        cache[embedding_model] = indexer
        logger.info(f"Cached indexer for embedding model: {embedding_model}")
    else:
        logger.debug(f"Using cached indexer for embedding model: {embedding_model}")
    return cache[embedding_model]


@track_time("Init: Document Indexer")
def initialize_qdrant_document_indexer(embedding_model):
    """
    Initialize and return a cached document indexer for the given embedding model.

    Args:
        embedding_model: The embedding model identifier

    Returns:
        MultiQdrantDocumentIndexer: The initialized document indexer
    """
    return _get_or_create_indexer(
        cache=_document_indexer_cache,
        embedding_model=embedding_model,
        constructor=MultiQdrantDocumentIndexer,
        initializer=lambda idx: idx.initialize_qdrant()
    )


@track_time("Init: Chunk Indexer")
def initialize_qdrant_document_chunk_indexer(embedding_model):
    """
    Initialize and return a cached chunk indexer for the given embedding model.

    Args:
        embedding_model: The embedding model identifier

    Returns:
        MultiQdrantChunkIndexer: The initialized chunk indexer
    """
    return _get_or_create_indexer(
        cache=_chunk_indexer_cache,
        embedding_model=embedding_model,
        constructor=MultiQdrantChunkIndexer,
        initializer=lambda idx: idx.initialize_qdrant()
    )

# Enhanced function that handles both document metadata and chunks
@track_time("Node: Generate Embeddings And Index")
@traceable(name="Generate Embeddings And Index")
def index_in_vectordb(
    object_id: str,
    knowledge_id: str,
    user_id: Optional[str],
    enriched_metadata: Union[BaseModel, Dict[str, Any]],
    embedding_model: str = "paraphrase-multilingual-mpnet-base-v2",
    chunks_only: bool = False,
    owner_id: Optional[str] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Index enriched metadata and chunks in vector database.
    
    Args:
        object_id (str): Unique identifier for the document (object_id from tasks)
        knowledge_id (str): The knowledge ID
        user_id (Optional[str]): The user ID (can be None)
        enriched_metadata (Union[BaseModel, Dict[str, Any]]): The enriched metadata from LLM containing document metadata and chunks
        embedding_model (str): The embedding model to use for indexing
        
    Returns:
        bool: True if successfully indexed
    """
    try:
        logger.info(f"Starting vector indexing for enriched metadata and chunks of document {object_id}")

        # Track total indexing duration (includes initialization, embedding, and Qdrant operations)
        indexing_start_time = time.time()
        # Track embedding duration separately
        total_embedding_duration_ms = 0

        # Get or create cached indexers
        document_indexer = initialize_qdrant_document_indexer(embedding_model)
        chunk_indexer = initialize_qdrant_document_chunk_indexer(embedding_model)

        def is_real_value(v):
            return v not in (None, "not_found", ["not_found"])

        _RESERVED_PAYLOAD_KEYS = frozenset({"object_id", "knowledge_id", "created_at", "chunk_text"})

        def _safe_extra(extra: Optional[dict]) -> dict:
            if not extra:
                return {}
            cleaned = {}
            for k, v in extra.items():
                if k in _RESERVED_PAYLOAD_KEYS:
                    logger.warning(f"extra_metadata key '{k}' is reserved and will be ignored (object_id={object_id})")
                else:
                    cleaned[k] = v
            return cleaned

        safe_extra = _safe_extra(extra_metadata)

        # Handle both BaseModel and dictionary inputs
        if isinstance(enriched_metadata, BaseModel):
            logger.info(f"Enriched metadata is a Pydantic model")
            summary = getattr(enriched_metadata, 'summary', '')
            # mode="json" so `date` fields become ISO-8601 strings — this dict
            # becomes the Qdrant payload, which is JSON and cannot carry a
            # datetime.date. ISO is also what the DATETIME payload index reads.
            metadata_dict = enriched_metadata.model_dump(mode="json")
        else:
            logger.info(f"Enriched metadata is a dictionary")
            summary = enriched_metadata.get('summary', '')
            metadata_dict = enriched_metadata

        logger.info(f"Using summary for vector indexing: {str(summary)[:200]}...")

        # Get owner-specific API key if owner_id is provided and model is OpenAI
        owner_api_key = None
        embedding_key_type = None
        provider, model_name = _get_embedding_provider_and_model(embedding_model)
        if provider == "openai":
            if owner_id:
                owner_api_key, embedding_key_type = get_api_key(owner_id, "openai", setup_env.OPENAI_API_KEY)
        else:
            embedding_key_type = "platform-key"

        # Initialize tracking variables
        document_vector_count = 0
        chunk_vector_count = 0
        total_payload_size_bytes = 0
        total_embedding_tokens = 0
        total_embedding_char_count = 0
        
        if not chunks_only:
            # Prepare document payload by combining system fields with metadata
            document_payload = {
                "object_id": object_id,
                "knowledge_id": knowledge_id,
                "created_at": time.time(),
                **{k: v if is_real_value(v) else [] if isinstance(v, list) else "" for k, v in metadata_dict.items() if k != 'chunks' and v is not None},
                **safe_extra,
            }

            logger.info(f"Document payload: {document_payload}")

            # Calculate payload size for document
            total_payload_size_bytes += len(json.dumps(document_payload))

            # Track embedding time for document
            doc_embedding_start = time.time()
            # Index document in vector database and get embedding metrics
            doc_tokens, doc_char_count = document_indexer.index_document_in_qdrantdb(
                object_id=object_id,
                document_payload=document_payload,
                summary=str(summary),
                knowledge_id=knowledge_id,
                owner_api_key=owner_api_key
            )
            doc_embedding_duration_ms = int((time.time() - doc_embedding_start) * 1000)
            total_embedding_duration_ms += doc_embedding_duration_ms

            total_embedding_tokens += doc_tokens
            total_embedding_char_count += doc_char_count

            document_vector_count = 1
            logger.info(f"Successfully indexed enriched document metadata for document {object_id} in vector database (embedding time: {doc_embedding_duration_ms}ms)")
        
        # Index chunks if they exist
        chunks = metadata_dict.get('chunks', [])
        if chunks:
            logger.info(f"Indexing {len(chunks)} chunks for document {object_id}",extra={"chunks_count": len(chunks),"object_id": object_id,})
            
            chunk_records = []
            # prepended_doc_summary = metadata_dict.get('prepended_doc_summary', '')
            
            for idx, chunk in enumerate(chunks):
                # Deterministic point id so re-processing the same doc (SQS redelivery /
                # worker-lost requeue) overwrites its chunks instead of inserting new
                # points under fresh random ids, which left orphaned duplicate chunks.
                chunk_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{knowledge_id}:{object_id}:{idx}"))
                
                # Get chunk text based on chunk type (handle both dict and object)
                if isinstance(chunk, dict):
                    chunk_text = chunk.get('chunk_text', '')

                if not chunk_text:
                    logger.warning(f"Chunk {idx} for document {object_id}", "missing chunk_text field")
                    continue
                
                # Prepare chunk text for embedding (with prepended summary if available)
                # chunk_text_for_embedding = f"{prepended_doc_summary}{chunk_text}" if prepended_doc_summary else chunk_text
                
                # Build base chunk payload
                chunk_payload = {
                    "object_id": object_id,
                    "chunk_text": chunk_text,
                    "knowledge_id": knowledge_id,
                    **safe_extra,
                }
            
                # Add chunk-specific metadata only if it exists in the chunk
                # For ChunkMetadata type (Strategy 1)
                if isinstance(chunk, dict):
                    # if 'chunk_idx' in chunk:
                    #     chunk_payload["chunk_idx"] = chunk.get('chunk_idx', idx)
                    # else:
                    #     chunk_payload["chunk_idx"] = idx

                    # if v is not real value assigned, assign [] or ""
                    chunk_payload.update({
                        k: v if is_real_value(v) else [] if isinstance(v, list) else "" for k, v in chunk.items()
                        if k not in ('chunk_text')
                    })
                    if idx == 0:
                        logger.info(f"Chunk payload: {chunk_payload}")
                
                # if 'file_name' in metadata_dict:
                #     chunk_payload["file_name"] = metadata_dict['file_name']
                
                
                # DON'T APPEND IF EMPTY OR NONE 
                chunk_payload.update({
                    k: v for k, v in metadata_dict.items() if k in ('file_name', 'url', 'source_url', 'source_object_id')
                })

                # Add prepended doc summary if available
                # if prepended_doc_summary:
                #     chunk_payload["prepended_doc_summary"] = prepended_doc_summary
                
                # Calculate payload size for chunk
                total_payload_size_bytes += len(json.dumps(chunk_payload))
                
                chunk_records.append({
                    "id": chunk_id,
                    "embedded_text": chunk_text,
                    "payload": chunk_payload
                })
            
            # Index all chunks and get aggregated embedding metrics
            if chunk_records:
                # Track embedding time for chunks
                chunk_embedding_start = time.time()
                chunk_tokens, chunk_char_count = chunk_indexer.index_chunk_in_qdrantdb(
                    object_id, chunk_records, knowledge_id, owner_api_key=owner_api_key
                )
                chunk_embedding_duration_ms = int((time.time() - chunk_embedding_start) * 1000)
                total_embedding_duration_ms += chunk_embedding_duration_ms

                total_embedding_tokens += chunk_tokens
                total_embedding_char_count += chunk_char_count
                chunk_vector_count = len(chunk_records)
                logger.info("Successfully indexed chunks",extra={"chunks_count": len(chunk_records),"object_id": object_id,"embedding_time_ms": chunk_embedding_duration_ms})
            else:
                logger.warning("No valid chunks to index",extra={"object_id": object_id,})
        else:
            logger.info("No chunks found",extra={"object_id": object_id,})
        
        # Track vector indexing usage
        # Calculate total indexing duration (includes initialization, embedding, and Qdrant operations)
        total_indexing_duration_ms = int((time.time() - indexing_start_time) * 1000)

        try:
            provider, model_name = _get_embedding_provider_and_model(embedding_model)

            send_vector_indexing_event(
                provider=provider,
                model=model_name,
                chunks_only=chunks_only,
                document_vector_count=document_vector_count,
                chunk_vector_count=chunk_vector_count,
                payload_size_bytes=total_payload_size_bytes,
                duration_ms=total_indexing_duration_ms,
                knowledge_id=knowledge_id,
                object_id=object_id# ,
                # key_type=embedding_key_type
            )
            logger.info(f"Vector indexing event sent: total_duration={total_indexing_duration_ms}ms, vectors={document_vector_count + chunk_vector_count}")
        except Exception as e:
            logger.error(f"Error tracking vector indexing usage: {e}")

        # Send single aggregated embedding event for all embeddings
        # Use actual embedding duration (excludes Qdrant upsert operations)
        if total_embedding_char_count > 0:
            try:
                provider, model_name = _get_embedding_provider_and_model(embedding_model)
                send_embedding_event(
                    provider=provider,
                    model=model_name,
                    embedding_tokens=total_embedding_tokens,
                    char_count=total_embedding_char_count,
                    duration_ms=total_embedding_duration_ms,
                    knowledge_id=knowledge_id,
                    object_id=object_id,
                    key_type=embedding_key_type
                )
                logger.info(f"Embedding event sent: duration={total_embedding_duration_ms}ms, tokens={total_embedding_tokens}, chars={total_embedding_char_count}")
            except Exception as e:
                logger.error(f"Error sending aggregated embedding event: {e}")
        
        # Record embedding cost for this doc: stash for the Process Document trace
        # and tag the current 'Generate Embeddings And Index' run for per-doc detail.
        embed_cost_usd = embedding_cost_usd(embedding_model, total_embedding_tokens)
        add_embedding_cost(total_embedding_tokens, embed_cost_usd)
        try:
            import langsmith as ls
            run = ls.get_current_run_tree()
            if run is not None:
                run.add_metadata({
                    "embedding_tokens": total_embedding_tokens,
                    "embedding_cost_usd": embed_cost_usd,
                    "embedding_time_ms": total_embedding_duration_ms,
                })
        except Exception:
            pass

        logger.info("Successfully completed indexing",extra={"object_id": object_id,})
        return True
        
    except Exception as e:
        logger.error("Error indexing enriched metadata and chunks",extra={"error": str(e),"object_id": object_id,})
        raise


def delete_document_by_source_id(knowledge_id: str, source_id: str, embedding_model: str) -> None:
    """Delete doc-level Qdrant point by source_id before reindexing."""
    from qdrant_client.http.models import Filter, FieldCondition, MatchValue
    from qdrant_client.http import models
    document_indexer = initialize_qdrant_document_indexer(embedding_model)
    try:
        logger.info(f"vDB: deleting stale doc source_id='{source_id}' knowledge_id='{knowledge_id}'")
        document_indexer.client.delete(
            collection_name=document_indexer.collection_name,
            points_selector=models.FilterSelector(filter=Filter(must=[
                FieldCondition(key="knowledge_id", match=MatchValue(value=str(knowledge_id))),
                FieldCondition(key="source_id", match=MatchValue(value=str(source_id))),
            ])),
            wait=True,
        )
        logger.info(f"vDB: deleted stale doc for source_id='{source_id}'")
    except Exception as e:
        logger.error(f"vDB: delete_document_by_source_id failed source_id='{source_id}': {e}")
        raise


def delete_chunks_by_source_id(knowledge_id: str, source_id: str, embedding_model: str) -> None:
    """Delete all chunk Qdrant points by source_id before reindexing."""
    from qdrant_client.http.models import Filter, FieldCondition, MatchValue
    from qdrant_client.http import models
    chunk_indexer = initialize_qdrant_document_chunk_indexer(embedding_model)
    try:
        logger.info(f"vDB: deleting stale chunks source_id='{source_id}' knowledge_id='{knowledge_id}'")
        chunk_indexer.client.delete(
            collection_name=chunk_indexer.collection_name,
            points_selector=models.FilterSelector(filter=Filter(must=[
                FieldCondition(key="knowledge_id", match=MatchValue(value=str(knowledge_id))),
                FieldCondition(key="source_id", match=MatchValue(value=str(source_id))),
            ])),
            wait=True,
        )
        logger.info(f"vDB: deleted stale chunks for source_id='{source_id}'")
    except Exception as e:
        logger.error(f"vDB: delete_chunks_by_source_id failed source_id='{source_id}': {e}")
        raise