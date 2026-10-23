import time
import json
import asyncio
from typing import Dict, List, Tuple, Union, Any, Callable, Awaitable, TypeVar, Optional

# from utils.common_utils import format_docs
# from utils.qdrant.qdrant_hybrid_utils import QdrantHybridDocumentIndexer
# from utils.qdrant.qdrant_nonhybrid_utils import QdrantNonHybridDocumentIndexer
from utils.qdrant.qdrant_utils import MultiQdrantChunkIndexer, MultiQdrantDocumentIndexer
from qdrant_client.models import Filter, NestedCondition, Nested, FieldCondition, MatchValue, MatchAny, MinShould, OrderBy, Direction, PayloadSchemaType
from utils.db.retrieval_ops import (
    _sort_documents_in_memory,
    get_hybrid_search_value,
    get_summaries_for_object_ids,
    search_documents_by_query,
    # get_all_metadata,  # REMOVED: dead code — no router calls this
    get_document_metadata_enabled,
    get_object_ids_from_db,

    # Dataview utils
    _build_filter_conditions_from_dict,
    _ensure_range_index,
    _fetch_documents_with_cursor_pagination,
    _fetch_documents_with_offset_pagination,
    _get_field_index_type,
    _is_missing_index_error,

)
from utils.pydantic_models_retrieval import DocumentMetadataResponse, ChunkMetadata
from utils.logger import logger as logging
from utils.tracing import tracer, track_time


# Cached indexers by embedding model to avoid re-initialization overhead
# _HYBRID_INDEXERS: Dict[str, QdrantHybridDocumentIndexer] = {}
# _NONHYBRID_INDEXERS: Dict[str, QdrantNonHybridDocumentIndexer] = {}
_CHUNK_INDEXERS: Dict[str, MultiQdrantChunkIndexer] = {}
_DOCUMENT_INDEXERS: Dict[str, MultiQdrantDocumentIndexer] = {}


_TIndexer = TypeVar("_TIndexer")

async def _get_or_create_indexer(
    cache: Dict[str, _TIndexer],
    embedding_model: str,
    constructor: Callable[[str], _TIndexer],
    initializer: Callable[[_TIndexer], Awaitable[None]],
) -> _TIndexer:
    """Return cached indexer for model or construct+initialize and cache it."""
    if embedding_model not in cache:
        indexer = constructor(embedding_model)
        await initializer(indexer)
        cache[embedding_model] = indexer
    return cache[embedding_model]


# async def _get_hybrid_indexer(embedding_model: str) -> QdrantHybridDocumentIndexer:
#     return await _get_or_create_indexer(
#         _HYBRID_INDEXERS,
#         embedding_model,
#         QdrantHybridDocumentIndexer,
#         lambda i: i.initialize_qdrant(),
#     )


# async def _get_nonhybrid_indexer(embedding_model: str) -> QdrantNonHybridDocumentIndexer:
#     return await _get_or_create_indexer(
#         _NONHYBRID_INDEXERS,
#         embedding_model,
#         QdrantNonHybridDocumentIndexer,
#         lambda i: i.initialize_qdrant(),
#     )


async def _get_chunk_indexer(embedding_model: str) -> MultiQdrantChunkIndexer:
    return await _get_or_create_indexer(
        _CHUNK_INDEXERS,
        embedding_model,
        MultiQdrantChunkIndexer,
        lambda i: i.initialize_qdrant_async(),
    )

async def _get_document_indexer(embedding_model: str) -> MultiQdrantDocumentIndexer:
    return await _get_or_create_indexer(
        _DOCUMENT_INDEXERS,
        embedding_model,
        MultiQdrantDocumentIndexer,
        lambda i: i.initialize_qdrant_async(),
    )


# async def retrieve_documents_hybrid(
#     user_id: str,
#     chatbot_id: str,
#     refined_query: str,
#     num_of_chunks: int,
#     embedding_model: str,
# ) -> Tuple[str, List]:
#     try:
#         start_time = time.time()
#         logging.info(f"Performing hybrid search in Qdrant using {embedding_model} model")
#         indexer = await _get_hybrid_indexer(embedding_model)
#         extracted_documents = await indexer.get_retrieved_documents(
#             refined_query, user_id, chatbot_id, num_of_chunks
#         )
#         extracted_text_data = format_docs(extracted_documents) if extracted_documents else ""
#         logging.info(
#             f"Hybrid search and retrieval completed in {time.time() - start_time:.2f} seconds"
#         )
#         return extracted_text_data, extracted_documents
#     except Exception as e:
#         logging.error(f"Error in hybrid document retrieval: {e}")
#         return None, None


# async def retrieve_documents_nonhybrid(
#     user_id: str,
#     chatbot_id: str,
#     refined_query: str,
#     num_of_chunks: int,
#     embedding_model: str,
# ) -> Tuple[str, List]:
#     extracted_text_data = ""
#     extracted_documents: List = []
#     try:
#         start_time = time.time()
#         logging.info(f"Performing non-hybrid search in Qdrant using {embedding_model} model")
#         indexer = await _get_nonhybrid_indexer(embedding_model)
#         extracted_documents = await indexer.get_retrieved_documents(
#             refined_query, user_id, chatbot_id, num_of_chunks
#         )
#         if extracted_documents:
#             extracted_text_data = format_docs(extracted_documents)
#         total_time = time.time() - start_time
#         logging.info(
#             f"Non-hybrid search and retrieval completed in {total_time:.2f} seconds. Found {len(extracted_documents)} documents."
#         )
#         return extracted_text_data, extracted_documents
#     except Exception as e:
#         logging.error(f"Error in non-hybrid document retrieval: {e}", exc_info=True)
#         return None, []


async def list_documents_by_filter(
    knowledge_id: str,
    embedding_model: str,
    metadata_filter: Dict[str, Union[str, List[str]]] | None,
    user_id: Optional[str] = None,
    limit: int = 15,
    include_metadata: bool = False,
    include_source_id: bool = False,
    match_all: bool = False,
) -> List[Union[str, Dict]]:
    """
    Why:
        Scroll-based document listing with structured metadata filter support.
        Handles nested array-of-dicts (e.g. product attributes), operator dicts ($in/eq),
        and flat scalar/list values — each mapped to Qdrant must/min_should conditions.

    Guarantees:
        - knowledge_id is always a hard must condition.
        - Positive scalar/list filters are soft (min_should=1) by DEFAULT, so the loose
          "match at least one" recall behavior is preserved for existing callers. When
          `match_all=True` (or the slim `include_source_id` reconcile path) they become hard
          `must` (AND) conditions instead — every filter must match, so a caller can strictly
          scope results (e.g. to one space_id). This is the `match_all=true` API option.
        - Returns List[str] of object_ids by default; List[Dict] with full payload when include_metadata=True.
        - System fields (object_id, knowledge_id, created_at) are never exposed in metadata.
        - Unsupported filter shapes are warned and skipped, not raised.
    """
    try:
        # No embedding model → KB was never indexed (no Qdrant collection). Nothing to
        # list. Return empty rather than crashing in _get_document_indexer(None); this
        # keeps the reconcile sweep safe (empty kb_map → zero deletions).
        if not embedding_model:
            logging.info(f"list_documents_by_filter: no embedding_model for knowledge={knowledge_id}; returning empty")
            return []

        indexer = await _get_document_indexer(embedding_model)

        must_conditions = [
            FieldCondition(key="knowledge_id", match=MatchValue(value=knowledge_id)),
        ]
        should_conditions: List[FieldCondition] = []
        # Exclusion conditions go into Filter.must_not (hard AND) — a doc matching any of
        # these is dropped regardless of which positive should-conditions it satisfies.
        must_not_conditions: List[FieldCondition] = []

        if metadata_filter:
            # Operator keys that indicate a "scalar" filter object rather than a nested-object filter
            operator_keys = {"$in", "in", "any", "values", "eq", "value", "op", "$nin", "not_in"}

            for field_name, raw_value in metadata_filter.items():
                if raw_value is None:
                    continue

                # Negation: {"field": {"op": "ne"|"not_contains", "value": x}} or {"field": {"$nin"|"not_in": [...]}}.
                # Routed to must_not so the value is excluded from results.
                if isinstance(raw_value, dict):
                    neg_values: List[object] = []
                    if raw_value.get("op") in ("ne", "not_contains"):
                        ov = raw_value.get("value")
                        neg_values = ov if isinstance(ov, list) else ([ov] if ov is not None else [])
                    else:
                        for k in ("$nin", "not_in"):
                            if isinstance(raw_value.get(k), list):
                                neg_values = raw_value[k]
                                break
                    if neg_values:
                        cleaned_neg = [
                            v for v in neg_values
                            if v is not None and not (isinstance(v, str) and not v.strip())
                            and not isinstance(v, (dict, set, list, tuple))
                        ]
                        if cleaned_neg:
                            match_obj = (
                                MatchValue(value=cleaned_neg[0]) if len(cleaned_neg) == 1
                                else MatchAny(any=cleaned_neg)
                            )
                            must_not_conditions.append(FieldCondition(key=field_name, match=match_obj))
                        continue

                # Nested array-of-dicts: match docs where at least one element satisfies all inner conditions.
                # e.g. {"attributes": {"attribute_value_id": 22003, "attribute_name": "color"}}
                if isinstance(raw_value, dict) and not any(k in operator_keys for k in raw_value.keys()):
                    inner_conditions: List[FieldCondition] = []
                    for inner_key, inner_val in raw_value.items():
                        if inner_val is None:
                            continue
                        if isinstance(inner_val, list):
                            inner_values: List[object] = [
                                v for v in inner_val
                                if v is not None and (v.strip() if isinstance(v, str) else True)
                            ]
                            if not inner_values:
                                continue
                            match_obj = MatchAny(any=inner_values) if len(inner_values) > 1 else MatchValue(value=inner_values[0])
                        else:
                            match_obj = MatchValue(value=inner_val)
                        inner_conditions.append(FieldCondition(key=inner_key, match=match_obj))

                    if inner_conditions:
                        should_conditions.append(
                            NestedCondition(
                                nested=Nested(
                                    key=field_name,
                                    filter=Filter(must=inner_conditions),
                                )
                            )
                        )
                    continue

                # Normalize to flat list of primitives
                if isinstance(raw_value, dict):
                    candidates = None
                    for k in ("$in", "in", "any", "values"):
                        if k in raw_value and isinstance(raw_value[k], list):
                            candidates = raw_value[k]
                            break
                    if candidates is None:
                        for k in ("eq", "value"):
                            if k in raw_value:
                                candidates = [raw_value[k]]
                                break
                    if candidates is None:
                        logging.warning(f"Ignoring unsupported filter shape for field '{field_name}': {raw_value}")
                        continue
                    iterable = candidates
                elif isinstance(raw_value, list):
                    iterable = raw_value
                else:
                    iterable = [raw_value]

                cleaned_values: List[object] = []
                for v in iterable:
                    if v is None:
                        continue
                    if isinstance(v, (str, int)):
                        if not (isinstance(v, str) and not v.strip()):
                            cleaned_values.append(v)
                    elif not isinstance(v, (dict, set, list, tuple)):
                        cleaned_values.append(str(v))

                if not cleaned_values:
                    continue

                condition = (
                    FieldCondition(key=field_name, match=MatchValue(value=cleaned_values[0]))
                    if len(cleaned_values) == 1
                    else FieldCondition(key=field_name, match=MatchAny(any=cleaned_values))
                )
                # Reconcile/slim path AND explicit match_all=true need ALL filters to apply (AND)
                # so results are strictly scoped (e.g. to one folder/space); the default search
                # path keeps OR (min_should=1) for loose recall.
                if include_source_id or match_all:
                    must_conditions.append(condition)
                else:
                    should_conditions.append(condition)

        logging.info(f"list_documents_by_filter: knowledge={knowledge_id} must={len(must_conditions)} should={len(should_conditions)} must_not={len(must_not_conditions)} limit={limit}")
        # Without min_should, should conditions are optional — every doc in the knowledge base passes.
        # min_count=1 enforces that at least one metadata condition must match.
        # must_not always excludes; with excludes-only (no positives), every non-excluded doc passes.
        filter_condition = Filter(
            must=must_conditions,
            min_should=MinShould(conditions=should_conditions, min_count=1) if should_conditions else None,
            must_not=must_not_conditions or None,
        )
        
        _sys_fields = {"object_id", "knowledge_id", "created_at"}

        # Reconcile/slim path MUST see every matching doc — a partial view drives false deletes.
        # So page through scroll() to exhaustion (cursor None) instead of trusting one `limit`.
        if include_source_id:
            result = []
            offset = None
            page_size = limit if limit and limit > 0 else 1000
            # Hard ceiling so a never-None cursor (bug/loop) errors out rather than spins forever.
            max_pages = 10000
            with tracer.start_as_current_span("vDB: Scroll Documents By Filter (paged)"):
                for _ in range(max_pages):
                    records, offset = await indexer.client_async.scroll(
                        collection_name=indexer.collection_name,
                        scroll_filter=filter_condition,
                        limit=page_size,
                        with_payload=True,
                        offset=offset,
                    )
                    for record in records or []:
                        payload = record.payload or {}
                        oid = payload.get("object_id")
                        if not oid:
                            continue
                        # last_modified lets connectors skip re-uploading unchanged sources
                        # before hitting the network; None when the doc never stored one.
                        result.append({
                            "object_id": oid,
                            "source_id": payload.get("source_id"),
                            "last_modified": payload.get("last_modified"),
                        })
                    if offset is None:  # cursor exhausted — full view captured
                        return result
                # Loop ceiling hit without exhausting — refuse to return a partial view.
                raise RuntimeError(
                    f"list_documents_by_filter: scroll did not exhaust after {max_pages} pages "
                    f"(knowledge={knowledge_id}); refusing to return a partial result"
                )

        with tracer.start_as_current_span("vDB: Scroll Documents By Filter"):
            response = await indexer.client_async.scroll(
                collection_name=indexer.collection_name,
                scroll_filter=filter_condition,
                limit=limit,
                with_payload=True if include_metadata else ["object_id", "source_id"],
            )
        records = response[0] if isinstance(response, tuple) and len(response) > 0 else []
        if not records:
            return []
        if include_metadata:
            result = []
            for record in records:
                payload = record.payload or {}
                oid = payload.get("object_id")
                if not oid:
                    continue
                result.append({
                    "object_id": oid,
                    "metadata": {k: v for k, v in payload.items() if k not in _sys_fields},
                })
            return result
        return [record.payload.get("object_id") for record in records if record.payload and record.payload.get("object_id")]
    except Exception as e:
        logging.error(f"list_documents_by_filter failed knowledge={knowledge_id} filter_keys={list(metadata_filter.keys()) if metadata_filter else []}: {e}")
        # Slim/reconcile callers must NOT see a partial/empty list mistaken for "no docs" —
        # that would silently suppress deletes. Surface the error instead.
        if include_source_id:
            raise
        return []

# REMOVED: get_all_metadata_service — never called from any router.
# Aggregated metadata is served by get_metadata_for_field_config (Qdrant facet/scroll).
# async def get_all_metadata_service(knowledge_id: str) -> Any:
#     try:
#         return await get_all_metadata(knowledge_id)
#     except Exception as e:
#         logging.error(f"Error getting metadata: {e}")
#         return {"fields": []}


@track_time("Node: Get Document Metadata")
async def get_document_metadata(
    object_id: str,
    knowledge_id: str,
) -> DocumentMetadataResponse:
    """Fetch metadata for a document from the Qdrant documents collection."""
    try:
        # Check if document_level_metadata is enabled
        metadata_enabled = await get_document_metadata_enabled(knowledge_id)
        if not metadata_enabled:
            logging.info("Metadata disabled")
            return DocumentMetadataResponse(object_id=object_id, metadata=None)
        
        _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
        indexer = await _get_document_indexer(embedding_model)
        with tracer.start_as_current_span("vDB: Scroll Document Metadata"):
            response = await indexer.client_async.scroll(
                collection_name=indexer.collection_name,
                scroll_filter=Filter(must=[FieldCondition(key="object_id", match=MatchValue(value=object_id))]),
                limit=1,
                with_payload=True
            )
        records = response[0] if isinstance(response, tuple) and len(response) > 0 else []
        if not records:
            logging.info(f"No document found for object_id={object_id}")
            return DocumentMetadataResponse(object_id=object_id, metadata=None)
        point = records[0]
        payload = getattr(point, "payload", None) or {}
        
        try:
            # get field configuration for knokwledge id from db
            from utils.db.enrichment_db_utils import get_field_configurations_sqlmodel_v2
            field_configurations = await get_field_configurations_sqlmodel_v2(knowledge_id)
            logging.debug(f"Field configs: {len(field_configurations)}")
            filtered_metadata = {}
            for field_config in field_configurations:
                if field_config.get("field_name") in payload:
                    filtered_metadata[field_config.get("field_name")] = payload[field_config.get("field_name")]
        except Exception as e:
            logging.error(f"Error getting field configs: {e}")
            # return DocumentMetadataResponse(object_id=object_id, metadata=None)

        # Filter out system fields that shouldn't be returned in metadata
        fields_to_exclude = {"object_id", "knowledge_id", "created_at"}
        filtered_metadata = {k: v for k, v in payload.items() if k not in fields_to_exclude}
        
        return DocumentMetadataResponse(object_id=object_id, metadata=filtered_metadata)
    except Exception as e:
        logging.error(f"Error fetching document metadata for {object_id}: {e}")
        return DocumentMetadataResponse(object_id=object_id, metadata=None)


async def list_documents_get_alias(
    knowledge_id: str,
    filters: Dict[str, Union[str, List[str]]] | None,
    user_id: Optional[str] = None,
    limit: int = 15,
    include_metadata: bool = False,
    include_source_id: bool = False,
    match_all: bool = False,
) -> List[Union[str, Dict]]:
    """
    Why:
        Routes document listing to DB or Qdrant depending on whether metadata is enabled.
        DB path is a fallback — it can only return object_ids regardless of include_metadata,
        because metadata fields are stored in Qdrant, not in the documents table.

    Guarantees:
        - Always returns a list; empty list on no results.
        - When metadata is disabled, include_metadata is silently ignored (DB has no payload).
    """
    metadata_enabled = await get_document_metadata_enabled(knowledge_id)

    if not metadata_enabled:
        # metadata fields live in Qdrant, not the DB — fall back to ID-only query.
        # source_id lives in the Qdrant payload too, so the slim path can't be served here.
        if include_source_id:
            logging.warning(f"list_documents_get_alias: include_source_id requested but metadata disabled for knowledge={knowledge_id}; source_id unavailable from DB fallback")
        logging.info(f"list_documents_get_alias: metadata disabled for knowledge={knowledge_id}, falling back to DB")
        return await get_object_ids_from_db(knowledge_id, limit)

    _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
    return await list_documents_by_filter(
        knowledge_id=knowledge_id,
        embedding_model=embedding_model,
        user_id=user_id,
        metadata_filter=filters,
        limit=limit,
        include_metadata=include_metadata,
        include_source_id=include_source_id,
        match_all=match_all,
    )

@track_time("Node: Get Chunks By IDs")
async def get_chunks_by_ids_wrapped(
    chunk_ids: List[str],
    knowledge_id: str,
) -> List[dict]:
    if not chunk_ids:
        return []
    try:
        _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
        # Normalize: split any comma-joined entries into individual IDs
        normalized_ids: List[str] = []
        for cid in chunk_ids:
            if isinstance(cid, str) and "," in cid:
                normalized_ids.extend([c.strip() for c in cid.split(",") if c.strip()])
            else:
                normalized_ids.append(cid)
        indexer = await _get_chunk_indexer(embedding_model)
        with tracer.start_as_current_span("vDB: Retrieve Chunks By IDs"):
            response = await indexer.client_async.retrieve(
                collection_name=indexer.collection_name,
                ids=normalized_ids,
                with_payload=True,
            )
        chunks: List[dict] = []
        for point in response or []:
            payload = point.payload or {}
            chunk_text = payload.get("chunk_text") or payload.get("text")
            chunks.append({
                "id": str(point.id),
                "object_id": payload.get("object_id"),
                "text": chunk_text,
                "metadata": payload,
            })
        return chunks
    except Exception as e:
        logging.error(f"Error fetching chunks by ids: {e}")
        return []

async def get_summaries_service(knowledge_id: str, object_ids: List[str], include_metadata: bool = False) -> Dict[str, Dict]:
    """
    Why:
        Service boundary for summary fetches — isolates callers from Qdrant errors.
        Returns {} on failure so the endpoint can still build a partial response.

    Guarantees:
        - Single batch Qdrant scroll regardless of list size.
        - Never raises; logs and returns {} on failure.
    """
    try:
        return await get_summaries_for_object_ids(knowledge_id, object_ids, include_metadata=include_metadata)
    except Exception as e:
        logging.error(f"get_summaries_service failed knowledge={knowledge_id} count={len(object_ids)}: {e}")
        return {}

async def search_documents_service(
    knowledge_id: str,
    query: str,
    metadata_filter: Optional[Dict] = None,
    limit: int = 10,
    mode: str = "hybrid",
    include_metadata: bool = False,
) -> List[Dict]:
    """
    Why:
        Service boundary for document search — resolves embedding model and delegates to Qdrant.
        Returns [] on failure so the endpoint degrades gracefully rather than 500ing.

    Guarantees:
        - Result order reflects Qdrant relevance score; callers must preserve it.
        - Never raises; logs and returns [] on failure.
    """
    try:
        _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
        return await search_documents_by_query(
            knowledge_id=knowledge_id,
            query=query,
            embedding_model=embedding_model,
            metadata_filter=metadata_filter,
            limit=limit,
            mode=mode,
            include_metadata=include_metadata,
        )
    except Exception as e:
        logging.error(f"search_documents_service failed knowledge={knowledge_id} mode={mode}: {e}")
        return []


async def get_document_chunks_paginated(
    object_id: str,
    knowledge_id: str,
    limit: int = 20,
    offset: int = 0,
    include_metadata: bool = False,
) -> Dict[str, Any]:
    """
    Retrieve chunks for a document with pagination and optional metadata.
    Chunks are returned in the order they appeared in the original document.
    
    Args:
        object_id: The object ID to retrieve chunks for
        knowledge_id: Knowledge ID for authentication
        limit: Maximum number of chunks to return (1-100)
        offset: Number of chunks to skip for pagination
        include_metadata: Whether to include chunk metadata in the response
    
    Returns:
        Dict containing chunks, pagination info, and metadata
    """
    try:
        # Get the embedding model for this knowledge base
        _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
        
        # Get the chunk indexer
        indexer = await _get_chunk_indexer(embedding_model)
        
        # Get all chunks for the document
        all_chunks = await indexer.get_all_chunks_for_document(object_id, knowledge_id)
        
        # Sort chunks by chunk_idx to ensure correct order
        # chunk_idx starts from 0, so missing values are placed at the end
        def get_chunk_idx(chunk):
            chunk_idx = chunk.get("chunk_idx")
            return chunk_idx if chunk_idx is not None else float('inf')
        
        all_chunks.sort(key=get_chunk_idx)
        
        # Calculate pagination
        total_chunks = len(all_chunks)
        paginated_chunks = all_chunks[offset:offset + limit]
        has_more = (offset + limit) < total_chunks
        
        # Format chunks for response
        formatted_chunks = []
        for chunk in paginated_chunks:
            chunk_data = {
                "id": str(chunk.get("id", "")),
                "text": chunk.get("chunk_text"),
            }
            
            # Include metadata if requested
            if include_metadata:
                fields_to_exclude = {
                    "object_id", 
                    "knowledge_id", 
                    "user_id", 
                    "sub_object_id",
                    "chunk_text",  # The actual text content, not metadata
                    "created_at",  # System timestamp
                    "prepended_doc_summary",
                    "chunk_idx"
                }
                filtered_metadata = {
                    k: v for k, v in chunk.items() 
                    if k not in fields_to_exclude
                }
                chunk_data["metadata"] = filtered_metadata
            
            formatted_chunks.append(chunk_data)
        
        return {
            "object_id": object_id,
            "chunks": formatted_chunks,
            "total_chunks": total_chunks,
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
        }
        
    except Exception as e:
        logging.error(f"Error retrieving paginated chunks for document {object_id}: {e}")



# ================================================================================================
# MAIN SERVICE FUNCTION: GET ALL DOCUMENTS METADATA (FLATTENED)
# ================================================================================================

@track_time("Node: Get Data View Tabular")
async def get_data_view_tabular(
    knowledge_id: str,
    limit: int = 20,
    columns: Optional[List[str]] = None,
    filters: Optional[Dict[str, Any]] = None,
    sort: Optional[List[Dict[str, str]]] = None,
    max_nested_items: int = 3,
    page_offset: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch documents metadata in fixed-schema tabular format optimized for display and export.

    This function differs from flatten_metadata by:
    1. Building a fixed column schema FIRST from field configurations
    2. Expanding List[dict] to a fixed number of columns (max_nested_items)
       - Creates: field_0_*, field_1_*, field_2_* (always present, even if empty/null)
       - Creates: field_remaining_* columns to collect overflow items as lists
    3. Every row has ALL columns with null for missing values
    4. Returns total_count (requires count query)
    5. Caps sorting memory usage (max 10k docs)
    6. Uses cursor-based pagination via page_offset for efficiency

    Args:
        knowledge_id: The knowledge base ID
        limit: Maximum number of documents to return (1-100)
        columns: Optional list of specific columns to include (None = all active fields)
        filters: Optional filter conditions dict
        sort: Optional sort configuration list
        max_nested_items: Maximum number of items to expand for List[dict] fields (default=3)
        page_offset: Qdrant page offset token from previous response for efficient pagination (None for first page)

    Returns:
        Dictionary containing:
        - columns: List[ColumnSchema] - Fixed schema definition
        - rows: List[Dict] - Data rows conforming to schema (all have same keys)
        - total_count: Total matching documents (before pagination)
        - current_count: Number of documents returned in the current response
        - next_page_offset: Qdrant page offset token for next page (use this for subsequent requests, None if no more pages)
        - has_more: Boolean indicating if more documents exist
    """
    try:
        # 1. Check if document metadata is enabled for this knowledge base
        metadata_enabled = await get_document_metadata_enabled(knowledge_id)
        if not metadata_enabled:
            logging.info("Metadata disabled")
            return {
                "columns": [],
                "rows": [],
                "total_count": 0,
                "current_count": 0,
                "next_page_offset": None,
                "has_more": False
            }

        # 2. Initialize Qdrant document indexer
        _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
        indexer = await _get_document_indexer(embedding_model)

        # 3. Fetch field configurations to build the column schema
        from utils.db.enrichment_db_utils import get_field_configurations_sqlmodel_v2
        field_configurations = await get_field_configurations_sqlmodel_v2(knowledge_id)

        # Filter for document-level active fields only
        active_fields = [
            config
            for config in field_configurations
            if config.get("level") == "document"
            and config.get("is_active", True)
        ]

        # 4. Build column schema from field configurations
        column_schemas = []
        schema_map = {}  # Maps column name -> ColumnSchema for quick lookup

        # Track which columns were explicitly requested by the user
        user_selected_columns = set(columns) if columns is not None else None

        # Always include object_id as first column
        object_id_schema = {
            "name": "object_id",
            "original_field": "object_id",
            "data_type": "str",
            "is_user_selected": (user_selected_columns is None) or ("object_id" in user_selected_columns),
        }
        column_schemas.append(object_id_schema)
        schema_map["object_id"] = object_id_schema

        # Build schema from field configurations
        for field_config in active_fields:
            field_name = field_config.get("field_name")
            field_type = field_config.get("field_type", "str")
            schema = field_config.get("schema")  # Schema for dict/List[dict] fields

            # Determine if this field was user-selected
            # If columns is None, all fields are considered selected
            # If columns is specified, only those in the list are selected
            is_user_selected = (user_selected_columns is None) or (field_name in user_selected_columns)

            # Handle List[dict] expansion - create fixed number of indexed columns
            if field_type == "List[dict]" and max_nested_items > 0:
                # Expand List[dict] to fixed indexed columns with nested fields
                # If schema exists: Creates field_0_year, field_0_degree, field_1_year, field_1_degree, etc.
                # If no schema: Creates field_0, field_1, field_2 (whole dict objects)

                if schema and isinstance(schema, dict):
                    # Expand with nested field names from schema
                    # Schema nested types are limited to: "str" and "List[str]"
                    for idx in range(max_nested_items):
                        for nested_field, nested_type in schema.items():
                            # Return the field type as-is for UI mapping
                            # nested_type will be "str" or "List[str]"
                            indexed_column = {
                                "name": f"{field_name}_{idx}_{nested_field}",
                                "original_field": field_name,
                                "data_type": nested_type,
                                "is_user_selected": is_user_selected,
                            }
                            column_schemas.append(indexed_column)
                            schema_map[f"{field_name}_{idx}_{nested_field}"] = indexed_column

                    # Add "remaining" columns for overflow items beyond max_nested_items
                    # These columns collect all remaining values as lists
                    for nested_field, nested_type in schema.items():
                        # Determine list type based on nested_type
                        # If nested_type is already a list type (e.g., "List[str]"), wrap it again
                        # Otherwise, convert scalar type to list (e.g., "str" -> "List[str]")
                        if nested_type.startswith("List["):
                            # Already a list type, wrap it: List[str] -> List[List[str]]
                            list_type = f"List[{nested_type}]"
                        else:
                            # Scalar type, make it a list: str -> List[str]
                            list_type = f"List[{nested_type}]"

                        remaining_column = {
                            "name": f"{field_name}_remaining_{nested_field}",
                            "original_field": field_name,
                            "data_type": list_type,
                            "is_user_selected": is_user_selected,
                        }
                        column_schemas.append(remaining_column)
                        schema_map[f"{field_name}_remaining_{nested_field}"] = remaining_column
                else:
                    # No schema - store whole dict objects
                    for idx in range(max_nested_items):
                        indexed_column = {
                            "name": f"{field_name}_{idx}",
                            "original_field": field_name,
                            "data_type": "dict",
                            "is_user_selected": is_user_selected,
                        }
                        column_schemas.append(indexed_column)
                        schema_map[f"{field_name}_{idx}"] = indexed_column

                    # Add "remaining" column for overflow dict objects
                    remaining_column = {
                        "name": f"{field_name}_remaining",
                        "original_field": field_name,
                        "data_type": "List[dict]",
                        "is_user_selected": is_user_selected,
                    }
                    column_schemas.append(remaining_column)
                    schema_map[f"{field_name}_remaining"] = remaining_column

            elif field_type == "dict" and schema and isinstance(schema, dict):
                # Flatten dict fields with schema
                # Creates: field_year, field_degree, field_institution, etc.
                # Schema nested types are limited to: "str" and "List[str]"
                for nested_field, nested_type in schema.items():
                    # Return the field type as-is for UI mapping
                    # nested_type will be "str" or "List[str]"
                    field_schema = {
                        "name": f"{field_name}_{nested_field}",
                        "original_field": field_name,
                        "data_type": nested_type,
                        "is_user_selected": is_user_selected,
                    }
                    column_schemas.append(field_schema)
                    schema_map[f"{field_name}_{nested_field}"] = field_schema

            else:
                # Regular fields: str, int, float, List[str], dict, List[dict] without schema
                # Return the field type as-is for UI mapping
                field_schema = {
                    "name": field_name,
                    "original_field": field_name,
                    "data_type": field_type,
                    "is_user_selected": is_user_selected,
                }
                column_schemas.append(field_schema)
                schema_map[field_name] = field_schema

        # 5. Build Qdrant filter conditions
        must_conditions = [
            FieldCondition(key="knowledge_id", match=MatchValue(value=knowledge_id))
        ]

        if filters:
            try:
                filter_conditions = _build_filter_conditions_from_dict(filters)
                must_conditions.extend(filter_conditions)
                logging.debug(f"Filters: {len(filter_conditions)}")
            except Exception as e:
                logging.warning(f"Filter build error: {e}")

        filter_condition = Filter(must=must_conditions)

        # 6. Get total count of matching documents
        # Use count API for accurate total before pagination
        try:
            with tracer.start_as_current_span("vDB: Count Documents"):
                count_response = await indexer.client_async.count(
                    collection_name=indexer.collection_name,
                    count_filter=filter_condition,
                    exact=True
                )
            total_count = count_response.count if hasattr(count_response, 'count') else 0
        except Exception as e:
            logging.warning(f"Error getting document count: {e}, using estimated count")
            total_count = 0

        # 7. Fetch and sort documents using Qdrant native order_by when possible
        all_documents = []
        next_page_offset_result = None

        if sort:
            # Determine sort strategy:
            # - Single field sort: use Qdrant native cursor-based pagination (efficient)
            # - Multi-field sort: use offset-based pagination with in-memory sort (accurate)
            timestamp_fields = ["created_at", "updated_at"]
            is_multi_field_sort = len(sort) > 1

            if is_multi_field_sort:
                # Multi-field sort: use offset-based pagination for correct global ordering
                # Qdrant only supports order_by on a single field, so we fetch a larger pool,
                # sort all fields in-memory, and paginate with offset cursor
                logging.info(f"Multi-field sort ({len(sort)} fields): using offset-based pagination")

                try:
                    paginated_documents, has_more, next_page_offset_result = await _fetch_documents_with_offset_pagination(
                        indexer, filter_condition, sort, limit, page_offset
                    )
                    all_documents = paginated_documents

                except Exception as e:
                    logging.warning(f"Offset pagination failed: {type(e).__name__}: {str(e)[:200]}")
                    all_documents = []

            else:
                # Single field sort: use Qdrant native cursor-based pagination
                # Reorder: ensure created_at is at index 0 if present
                created_at_idx = next((i for i, s in enumerate(sort) if s.get("field") in timestamp_fields), None)

                if created_at_idx is not None and created_at_idx != 0:
                    created_at_sort = sort.pop(created_at_idx)
                    sort.insert(0, created_at_sort)

                primary_sort = sort[0]
                field = primary_sort.get("field")
                order = primary_sort.get("order", "asc")
                direction = Direction.ASC if order == "asc" else Direction.DESC

                try:
                    logging.info(f"Single-field sort: {field} {order} (page_offset={'present' if page_offset else 'none'})")

                    paginated_documents, has_more, next_page_offset_result = await _fetch_documents_with_cursor_pagination(
                        indexer, filter_condition, field, direction, limit, page_offset
                    )
                    all_documents = paginated_documents

                except Exception as e:
                    logging.warning(f"order_by failed: {type(e).__name__}: {str(e)[:200]}")

                    # Check if the error is due to missing range index
                    if _is_missing_index_error(e):
                        logging.warning(f"Missing index: {field}")
                        logging.info(f"Auto-creating index: {field}")

                        field_index_type = _get_field_index_type(field, active_fields)

                        index_created = await _ensure_range_index(
                            indexer=indexer,
                            field_name=field,
                            field_type=field_index_type,
                            wait_seconds=2.0
                        )

                        if index_created:
                            try:
                                logging.info(f"Retrying with index: {field}")

                                paginated_documents, has_more, next_page_offset_result = await _fetch_documents_with_cursor_pagination(
                                    indexer, filter_condition, field, direction, limit, page_offset
                                )
                                all_documents = paginated_documents

                                logging.info(f"Retry successful")

                            except Exception as retry_error:
                                logging.warning(f"Retry failed: {retry_error}")
                                pass
                        else:
                            logging.warning(f"Index creation failed")

            # Fallback: if no documents were fetched (order_by/offset failed), fetch + sort in-memory
            if not all_documents and sort:
                max_docs_for_sort = 10000
                logging.info(f"Fallback: in-memory sort (max {max_docs_for_sort})")

                batch_size = 500
                current_page_offset = None
                iteration = 0

                while len(all_documents) < max_docs_for_sort:
                    iteration += 1
                    remaining = max_docs_for_sort - len(all_documents)
                    scroll_limit = min(batch_size, remaining)

                    logging.info(f"Fallback iter {iteration}: fetching {scroll_limit} docs (have {len(all_documents)}/{max_docs_for_sort}), offset={current_page_offset}")

                    with tracer.start_as_current_span("vDB: Scroll Fallback In-Memory Sort"):
                        response = await indexer.client_async.scroll(
                            collection_name=indexer.collection_name,
                            scroll_filter=filter_condition,
                            with_payload=True,
                            with_vectors=False,
                            limit=scroll_limit,
                            offset=current_page_offset
                        )

                    records, current_page_offset = response if isinstance(response, tuple) else (response, None)

                    logging.info(f"Fallback iter {iteration}: got {len(records) if records else 0} records, next_offset={current_page_offset}")

                    if not records:
                        break

                    all_documents.extend(records)

                    if current_page_offset is None:
                        break

                    if len(all_documents) >= max_docs_for_sort:
                        break

                    if iteration > 10:
                        logging.error(f"Fallback: too many iterations ({iteration}), breaking")
                        break

                logging.info(f"Fallback: fetched {len(all_documents)} docs in {iteration} iterations")

                all_documents = _sort_documents_in_memory(all_documents, sort)

                paginated_documents = all_documents[:limit]
                next_page_offset_result = None
                has_more = len(all_documents) > limit or (len(all_documents) >= max_docs_for_sort and current_page_offset is not None)

        else:
            # No sorting - use efficient scroll-based pagination
            # Fetch exactly limit documents
            documents_collected = 0
            current_page_offset = page_offset

            while documents_collected < limit:
                # Calculate remaining documents needed
                remaining = limit - documents_collected

                with tracer.start_as_current_span("vDB: Scroll Documents No Sort"):
                    response = await indexer.client_async.scroll(
                        collection_name=indexer.collection_name,
                        scroll_filter=filter_condition,
                        with_payload=True,
                        with_vectors=False,
                        limit=remaining,
                        offset=current_page_offset
                    )

                records, current_page_offset = response if isinstance(response, tuple) else (response, None)

                # logging.info(f"Qdrant scroll response received response, {response}")
                if not records:
                    break

                for point in records:
                    if documents_collected >= limit:
                        break

                    all_documents.append(point)
                    documents_collected += 1

                if documents_collected >= limit or current_page_offset is None:
                    break

            # Set pagination results
            paginated_documents = all_documents
            next_page_offset_result = current_page_offset
            has_more = current_page_offset is not None

        # 8. Build rows conforming to fixed schema
        rows = []

        for point in paginated_documents:
            payload = getattr(point, "payload", None) or {}

            # Initialize row with all columns as None
            row = {col["name"]: None for col in column_schemas}

            # Fill in object_id
            row["object_id"] = payload.get("object_id")

            # Process each field configuration
            for field_config in active_fields:
                field_name = field_config.get("field_name")
                field_type = field_config.get("field_type", "str")
                schema = field_config.get("schema")

                # Get the value from payload
                value = payload.get(field_name)

                if value is None:
                    continue

                # Handle different field types
                if field_type == "List[dict]" and max_nested_items > 0:
                    # Expand List[dict] to indexed columns with nested fields
                    if isinstance(value, list):
                        if schema and isinstance(schema, dict):
                            # Expand with nested field names from schema
                            for idx in range(max_nested_items):
                                if idx < len(value) and isinstance(value[idx], dict):
                                    # Extract nested fields from the dict at this index
                                    dict_item = value[idx]
                                    for nested_field in schema.keys():
                                        col_name = f"{field_name}_{idx}_{nested_field}"
                                        row[col_name] = dict_item.get(nested_field)
                                # If idx >= len(value), columns remain None (already initialized)

                            # Collect overflow items beyond max_nested_items into "remaining" columns
                            if len(value) > max_nested_items:
                                remaining_items = value[max_nested_items:]
                                for nested_field in schema.keys():
                                    col_name = f"{field_name}_remaining_{nested_field}"
                                    remaining_values = []
                                    for item in remaining_items:
                                        if isinstance(item, dict):
                                            val = item.get(nested_field)
                                            if val is not None:
                                                remaining_values.append(val)
                                    # Store as list if there are values, otherwise None
                                    row[col_name] = remaining_values if remaining_values else None
                        else:
                            # No schema - store whole dict objects
                            for idx in range(max_nested_items):
                                col_name = f"{field_name}_{idx}"
                                if idx < len(value):
                                    row[col_name] = value[idx]
                                # else: remains None

                            # Collect overflow dict objects into "remaining" column
                            if len(value) > max_nested_items:
                                remaining_items = value[max_nested_items:]
                                row[f"{field_name}_remaining"] = remaining_items if remaining_items else None

                elif field_type == "dict" and schema and isinstance(schema, dict):
                    # Flatten dict fields with schema
                    if isinstance(value, dict):
                        for nested_field in schema.keys():
                            col_name = f"{field_name}_{nested_field}"
                            if col_name in row:
                                row[col_name] = value.get(nested_field)

                else:
                    # Regular fields: str, int, float, List[str], or dict without schema
                    # Store value directly
                    if field_name in row:
                        row[field_name] = value

            rows.append(row)

        # 9. Return response with schema and data
        return {
            "columns": column_schemas,
            "rows": rows,
            "total_count": total_count,
            "current_count": len(rows),
            "next_page_offset": next_page_offset_result,
            "has_more": has_more
        }

    except Exception as e:
        logging.error(f"Error fetching data view: {e}")
        return {
            "columns": [],
            "rows": [],
            "total_count": 0,
            "current_count": 0,
            "next_page_offset": None,
            "has_more": False
        }


@track_time("Node: Get Data View Tabular V2")
async def get_data_view_tabular_v2(
    knowledge_id: str,
    page: int = 1,
    page_size: int = 20,
    columns: Optional[List[str]] = None,
    filters: Optional[Dict[str, Any]] = None,
    sort: Optional[List[Dict[str, str]]] = None,
    max_nested_items: int = 2,
) -> Dict[str, Any]:
    """
    Fetch documents metadata in fixed-schema tabular format with page-based pagination (v2).

    Uses true offset-based pagination for simple page number navigation (1, 2, 3...).
    This function properly implements offset-based retrieval using _fetch_documents_with_offset_pagination
    for sorted queries and direct scroll for unsorted queries.

    Args:
        knowledge_id: The knowledge base ID
        page: 1-indexed page number (first page is 1)
        page_size: Number of items per page (1-100)
        columns: Optional list of specific columns to include (None = all active fields)
        filters: Optional filter conditions dict
        sort: Optional sort configuration list (defaults to created_at desc)
        max_nested_items: Maximum number of items to expand for List[dict] fields (default=2)

    Returns:
        Dictionary containing:
        - columns: List[ColumnSchema] - Fixed schema definition
        - rows: List[Dict] - Data rows conforming to schema
        - total_count: Total matching documents (before pagination)
        - current_count: Number of documents in current page
        - current_page: Current page number (1-indexed)
        - total_pages: Total number of pages available
    """
    import math

    # Convert page number to offset for retrieval
    # Page 1 starts at offset 0, page 2 at offset page_size, etc.
    offset = (page - 1) * page_size

    logging.info(
        f"V2 API: Fetching page {page} (offset={offset}, limit={page_size}) "
        f"for knowledge_id={knowledge_id}"
    )

    try:
        # 1. Check if document metadata is enabled
        metadata_enabled = await get_document_metadata_enabled(knowledge_id)
        if not metadata_enabled:
            logging.info("V2 API: Metadata disabled for this knowledge base")
            return {
                "columns": [],
                "rows": [],
                "total_count": 0,
                "current_count": 0,
                "current_page": page,
                "total_pages": 0,
            }

        # 2. Initialize Qdrant indexer
        _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
        indexer = await _get_document_indexer(embedding_model)

        # 3. Fetch field configurations and build column schema
        from utils.db.enrichment_db_utils import get_field_configurations_sqlmodel_v2
        field_configurations = await get_field_configurations_sqlmodel_v2(knowledge_id)

        # Include all document-level enrichment fields (active + inactive).
        # Why: payload still carries inactive-field values until physically purged, and the data
        # view should reflect what's actually stored. Inactive fields default to is_user_selected=False
        # so the UI can dim/hide them unless explicitly requested via the `columns` param.
        active_fields = [
            config for config in field_configurations
            if config.get("level") == "document"
        ]

        # 4. Build column schema (reuse v1 logic)
        column_schemas = []

        # Track which columns were explicitly requested by the user
        user_selected_columns = set(columns) if columns is not None else None

        object_id_schema = {
            "name": "object_id",
            "original_field": "object_id",
            "field_path": "object_id",
            "subcolumn": None,
            "parent_type": "scalar",
            "data_type": "str",
            "sort_field": "object_id",
            "is_user_selected": (user_selected_columns is None) or ("object_id" in user_selected_columns),
        }
        column_schemas.append(object_id_schema)

        for field_config in active_fields:
            field_name = field_config.get("field_name")
            field_type = field_config.get("field_type", "str")
            schema = field_config.get("schema")
            is_active = field_config.get("is_active", True)

            # Inactive fields default to is_user_selected=False so UI dims them; explicit
            # request via `columns` still wins.
            if user_selected_columns is None:
                is_user_selected = bool(is_active)
            else:
                is_user_selected = field_name in user_selected_columns

            if field_type == "List[dict]" and max_nested_items > 0:
                if schema and isinstance(schema, dict):
                    for idx in range(max_nested_items):
                        for nested_field, nested_type in schema.items():
                            column_schemas.append({
                                "name": f"{field_name}_{idx}_{nested_field}",
                                "original_field": field_name,
                                "field_path": f"{field_name}[].{nested_field}",
                                "subcolumn": nested_field,
                                "parent_type": "list_dict",
                                "data_type": nested_type,
                                "sort_field": f"{field_name}[{idx}].{nested_field}",
                                "is_user_selected": is_user_selected,
                            })
                    for nested_field, nested_type in schema.items():
                        list_type = f"List[{nested_type}]" if not nested_type.startswith("List[") else f"List[{nested_type}]"
                        column_schemas.append({
                            "name": f"{field_name}_remaining_{nested_field}",
                            "original_field": field_name,
                            "field_path": f"{field_name}[].{nested_field}",
                            "subcolumn": nested_field,
                            "parent_type": "list_dict",
                            "data_type": list_type,
                            "sort_field": None,
                            "is_user_selected": is_user_selected,
                        })
                else:
                    for idx in range(max_nested_items):
                        column_schemas.append({
                            "name": f"{field_name}_{idx}",
                            "original_field": field_name,
                            "field_path": field_name,
                            "subcolumn": None,
                            "parent_type": "list_dict",
                            "data_type": "dict",
                            "sort_field": None,
                            "is_user_selected": is_user_selected,
                        })
                    column_schemas.append({
                        "name": f"{field_name}_remaining",
                        "original_field": field_name,
                        "field_path": field_name,
                        "subcolumn": None,
                        "parent_type": "list_dict",
                        "data_type": "List[dict]",
                        "sort_field": None,
                        "is_user_selected": is_user_selected,
                    })
            elif field_type == "dict" and schema and isinstance(schema, dict):
                for nested_field, nested_type in schema.items():
                    column_schemas.append({
                        "name": f"{field_name}_{nested_field}",
                        "original_field": field_name,
                        "field_path": f"{field_name}.{nested_field}",
                        "subcolumn": nested_field,
                        "parent_type": "dict",
                        "data_type": nested_type,
                        "sort_field": f"{field_name}.{nested_field}",
                        "is_user_selected": is_user_selected,
                    })
            else:
                parent_type = "list_str" if field_type == "List[str]" else "scalar"
                column_schemas.append({
                    "name": field_name,
                    "original_field": field_name,
                    "field_path": field_name,
                    "subcolumn": None,
                    "parent_type": parent_type,
                    "data_type": field_type,
                    "sort_field": field_name if parent_type == "scalar" else None,
                    "is_user_selected": is_user_selected,
                })

        # ---- 4b. Discover user-provided extra_metadata keys from the Qdrant payload ----
        # Why: extra_metadata is merged into the Qdrant document payload at index time but has no
        # declared schema in field_configuration. To surface these keys as data-view columns, we
        # sample a fixed number of points and union their payload keys. Anything not a system key
        # or an enrichment field is treated as user-provided metadata.
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        # Constants
        # SYSTEM_PAYLOAD_KEYS: never shown as columns even if present in payload.
        # excluded_payload_keys: union of system keys + every enrichment field name (active OR
        # inactive). An enrichment field already gets its own column from section 4 — don't
        # duplicate it as an "extra" column here.
        EXTRA_SAMPLE_SIZE = 200
        SYSTEM_PAYLOAD_KEYS = {"object_id", "knowledge_id", "created_at", "source_id"}
        enrichment_field_names = {c.get("field_name") for c in active_fields if c.get("field_name")}
        excluded_payload_keys = SYSTEM_PAYLOAD_KEYS | enrichment_field_names

        # Sample + type inference
        # extra_key_info[key] tracks the set of value shapes seen across the sample. We accumulate
        # rather than commit to one type immediately so we can detect conflicts (e.g. some docs
        # have str, others have dict — falls back to scalar/str at resolve time).
        # dict_keys / list_dict_keys hold the union of nested keys seen for dict / list[dict]
        # shapes, used to drive subcolumn expansion below.
        extra_key_info: Dict[str, Dict[str, Any]] = {}
        try:
            # One Qdrant scroll, no sort — Qdrant returns whatever order is cheapest. Sample is
            # representative, not deterministic. Failure here is non-fatal: we just get zero
            # extra columns and proceed.
            sample_filter = Filter(must=[FieldCondition(key="knowledge_id", match=MatchValue(value=knowledge_id))])
            with tracer.start_as_current_span("vDB: Sample Extra Metadata Keys"):
                sample_response = await indexer.client_async.scroll(
                    collection_name=indexer.collection_name,
                    scroll_filter=sample_filter,
                    with_payload=True,
                    with_vectors=False,
                    limit=EXTRA_SAMPLE_SIZE,
                )
            sample_records, _ = sample_response if isinstance(sample_response, tuple) else (sample_response, None)

            # Per-payload key inspection. bool is checked before int because bool is a subclass of
            # int in Python — order matters here.
            for pt in (sample_records or []):
                pl = getattr(pt, "payload", None) or {}
                for k, v in pl.items():
                    if k in excluded_payload_keys or v is None:
                        continue
                    info = extra_key_info.setdefault(k, {"types": set(), "dict_keys": set(), "list_dict_keys": set()})
                    if isinstance(v, bool):
                        info["types"].add("bool")
                    elif isinstance(v, int):
                        info["types"].add("int")
                    elif isinstance(v, float):
                        info["types"].add("float")
                    elif isinstance(v, str):
                        info["types"].add("str")
                    elif isinstance(v, dict):
                        info["types"].add("dict")
                        info["dict_keys"].update(v.keys())
                    elif isinstance(v, list):
                        # Treat list[dict] specially so it gets the same indexed-column expansion
                        # as enrichment List[dict]. Anything else (list of scalars, mixed list,
                        # empty list) collapses to list_str — one column holding the raw list.
                        if v and all(isinstance(item, dict) for item in v):
                            info["types"].add("list_dict")
                            for item in v:
                                info["list_dict_keys"].update(item.keys())
                        else:
                            info["types"].add("list_str")
                    else:
                        # Unknown shape — fall back to scalar/str so it still surfaces somewhere.
                        info["types"].add("str")
        except Exception as e:
            logging.warning(f"V2 API: Extra metadata key sampling failed: {e}")

        # Type resolution
        # If a key showed exactly one shape across the sample, use it. Mixed shapes (e.g. str in
        # some docs, dict in others) collapse to scalar/str — the safe lowest-common-denominator.
        # See spec: per-doc mismatches against the resolved type are dropped for dict/list_dict
        # columns (no base column exists for those shapes).
        def _resolve_extra_type(types: set) -> str:
            if len(types) == 1:
                return next(iter(types))
            return "str"

        # Column generation per extra_metadata key
        # extra_columns_meta is the row-builder's lookup table: {key, kind, subkeys}. We build it
        # in parallel with column_schemas so row-building doesn't need to re-infer types.
        # Sorting by key gives deterministic column order across requests.
        extra_columns_meta: List[Dict[str, Any]] = []
        for key in sorted(extra_key_info.keys()):
            info = extra_key_info[key]
            kind = _resolve_extra_type(info["types"])
            is_extra_user_selected = (user_selected_columns is None) or (key in user_selected_columns)

            if kind == "list_dict" and max_nested_items > 0 and info["list_dict_keys"]:
                # list[dict] expansion: indexed subcolumns up to max_nested_items, plus a
                # `_remaining_<subkey>` overflow column per subkey holding everything past the
                # cap. Mirrors enrichment List[dict] behavior so the UI doesn't need special
                # casing.
                subkeys = sorted(info["list_dict_keys"])
                for idx in range(max_nested_items):
                    for nk in subkeys:
                        column_schemas.append({
                            "name": f"{key}_{idx}_{nk}",
                            "original_field": key,
                            "field_path": f"{key}[].{nk}",
                            "subcolumn": nk,
                            "parent_type": "list_dict",
                            "data_type": "str",
                            "sort_field": f"{key}[{idx}].{nk}",
                            "is_user_selected": is_extra_user_selected,
                        })
                for nk in subkeys:
                    column_schemas.append({
                        "name": f"{key}_remaining_{nk}",
                        "original_field": key,
                        "field_path": f"{key}[].{nk}",
                        "subcolumn": nk,
                        "parent_type": "list_dict",
                        "data_type": "List[str]",
                        "sort_field": None,
                        "is_user_selected": is_extra_user_selected,
                    })
                extra_columns_meta.append({"key": key, "kind": "list_dict", "subkeys": subkeys})
            elif kind == "dict" and info["dict_keys"]:
                # dict expansion: one subcolumn per nested key (union across sample). field_path
                # uses dot notation so the UI's filter builder can target each leaf directly.
                subkeys = sorted(info["dict_keys"])
                for nk in subkeys:
                    column_schemas.append({
                        "name": f"{key}_{nk}",
                        "original_field": key,
                        "field_path": f"{key}.{nk}",
                        "subcolumn": nk,
                        "parent_type": "dict",
                        "data_type": "str",
                        "sort_field": f"{key}.{nk}",
                        "is_user_selected": is_extra_user_selected,
                    })
                extra_columns_meta.append({"key": key, "kind": "dict", "subkeys": subkeys})
            else:
                # Scalar / list_str / fallback: one column, no expansion.
                # data_type narrows to the actual Python type for scalars; list_str collapses to
                # List[str] regardless of inner element type (data view doesn't differentiate).
                parent_type = "list_str" if kind == "list_str" else "scalar"
                data_type = "List[str]" if kind == "list_str" else (kind if kind in {"int", "float", "bool", "str"} else "str")
                column_schemas.append({
                    "name": key,
                    "original_field": key,
                    "field_path": key,
                    "subcolumn": None,
                    "parent_type": parent_type,
                    "data_type": data_type,
                    "sort_field": key if parent_type == "scalar" else None,
                    "is_user_selected": is_extra_user_selected,
                })
                extra_columns_meta.append({"key": key, "kind": kind, "subkeys": []})

        logging.info(f"V2 API: Discovered {len(extra_columns_meta)} extra_metadata keys from sample")

        # 5. Build filter conditions
        must_conditions = [FieldCondition(key="knowledge_id", match=MatchValue(value=knowledge_id))]

        if filters:
            try:
                filter_conditions = _build_filter_conditions_from_dict(filters)
                must_conditions.extend(filter_conditions)
            except Exception as e:
                logging.warning(f"V2 API: Filter build error: {e}")

        filter_condition = Filter(must=must_conditions)

        # 6. Get total count
        try:
            with tracer.start_as_current_span("vDB: Count Documents"):
                count_response = await indexer.client_async.count(
                    collection_name=indexer.collection_name,
                    count_filter=filter_condition,
                    exact=True
                )
            total_count = count_response.count if hasattr(count_response, 'count') else 0
        except Exception as e:
            logging.warning(f"V2 API: Error getting count: {e}")
            total_count = 0

        # Calculate total pages
        total_pages = math.ceil(total_count / page_size) if total_count > 0 else 0

        # 7. Fetch documents using offset-based pagination
        all_documents = []

        # Qdrant's scroll `offset` is a point-ID cursor, not a skip-count, so page-based
        # slicing always needs the pool+sort+slice helper below (never a raw scroll with
        # offset=<int>). Fall back to created_at desc when no sort is given so unsorted
        # requests still get a deterministic order to slice against.
        effective_sort = sort or [{"field": "created_at", "order": "desc"}]
        page_offset_cursor = json.dumps({"offset": offset}) if offset > 0 else None

        try:
            paginated_documents, _, _ = await _fetch_documents_with_offset_pagination(
                indexer, filter_condition, effective_sort, page_size, page_offset_cursor
            )
            all_documents = paginated_documents
            logging.info(f"V2 API: Fetched {len(all_documents)} docs (offset={offset})")
        except Exception as e:
            logging.error(f"V2 API: Offset pagination failed: {e}")

        # 8. Build rows conforming to schema (reuse v1 logic)
        rows = []
        for point in all_documents:
            payload = getattr(point, "payload", None) or {}
            row = {col["name"]: None for col in column_schemas}
            row["object_id"] = payload.get("object_id")

            for field_config in active_fields:
                field_name = field_config.get("field_name")
                field_type = field_config.get("field_type", "str")
                schema = field_config.get("schema")

                value = payload.get(field_name)
                if value is None:
                    continue

                if field_type == "List[dict]" and max_nested_items > 0:
                    if isinstance(value, list):
                        if schema and isinstance(schema, dict):
                            for idx in range(max_nested_items):
                                if idx < len(value) and isinstance(value[idx], dict):
                                    for nested_field in schema.keys():
                                        row[f"{field_name}_{idx}_{nested_field}"] = value[idx].get(nested_field)
                            if len(value) > max_nested_items:
                                for nested_field in schema.keys():
                                    remaining_values = [
                                        item.get(nested_field) for item in value[max_nested_items:]
                                        if isinstance(item, dict) and item.get(nested_field) is not None
                                    ]
                                    row[f"{field_name}_remaining_{nested_field}"] = remaining_values if remaining_values else None
                        else:
                            for idx in range(max_nested_items):
                                if idx < len(value):
                                    row[f"{field_name}_{idx}"] = value[idx]
                            if len(value) > max_nested_items:
                                row[f"{field_name}_remaining"] = value[max_nested_items:]
                elif field_type == "dict" and schema and isinstance(schema, dict):
                    if isinstance(value, dict):
                        for nested_field in schema.keys():
                            row[f"{field_name}_{nested_field}"] = value.get(nested_field)
                else:
                    row[field_name] = value

            # ---- Populate user-provided extra_metadata columns ----
            # Type-mismatch handling (per-doc value vs. sample-inferred kind):
            #   scalar / list_str  → write raw value into the base column (always present).
            #   dict / list_dict   → expand into subcolumns when value shape matches; otherwise
            #                        the `if key in row` guard is a no-op (no base column exists
            #                        for these kinds) and the value is silently dropped from the
            #                        row. Data still lives in the Qdrant payload, so filters and
            #                        other APIs continue to see it.
            for meta in extra_columns_meta:
                key = meta["key"]
                kind = meta["kind"]
                subkeys = meta["subkeys"]
                value = payload.get(key)
                if value is None:
                    continue

                if kind == "list_dict" and max_nested_items > 0 and subkeys:
                    # Same indexed-slot + _remaining_<subkey> layout as enrichment List[dict].
                    # Mismatch (value is not a list of dicts) → silently dropped: no base column
                    # exists for list_dict, so there's nowhere to put the raw value.
                    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
                        for idx in range(max_nested_items):
                            if idx < len(value):
                                for nk in subkeys:
                                    row[f"{key}_{idx}_{nk}"] = value[idx].get(nk)
                        # Overflow: items beyond max_nested_items collapse into a list per subkey.
                        # None values are stripped so the cell only shows present-and-non-null
                        # remainders; entirely-empty overflow lists become None for clean
                        # tabular rendering.
                        if len(value) > max_nested_items:
                            for nk in subkeys:
                                remaining = [
                                    item.get(nk) for item in value[max_nested_items:]
                                    if isinstance(item, dict) and item.get(nk) is not None
                                ]
                                row[f"{key}_remaining_{nk}"] = remaining if remaining else None
                elif kind == "dict" and subkeys:
                    # Dict expansion: each pre-declared subkey gets a column. Subkeys not present
                    # on this specific doc stay None (already initialized by the row-template
                    # comprehension up top). Mismatch (value is not a dict) → silently dropped:
                    # no base column exists for dict.
                    if isinstance(value, dict):
                        for nk in subkeys:
                            row[f"{key}_{nk}"] = value.get(nk)
                else:
                    # Scalar / list_str / fallback: single column write, no shape checks needed.
                    row[key] = value

            rows.append(row)

        current_count = len(rows)

        logging.info(
            f"V2 API: Returning {current_count} documents "
            f"(page {page}/{total_pages}, total_count={total_count})"
        )

        # 9. Return v2 response
        return {
            "columns": column_schemas,
            "rows": rows,
            "total_count": total_count,
            "current_count": current_count,
            "current_page": page,
            "total_pages": total_pages,
        }

    except Exception as e:
        logging.error(f"V2 API: Error in get_data_view_tabular_v2: {e}", exc_info=True)
        return {
            "columns": [],
            "rows": [],
            "total_count": 0,
            "current_count": 0,
            "current_page": page,
            "total_pages": 0,
        }


# async def get_all_documents_metadata_flattened(
#     knowledge_id: str,
#     limit: int = 20,
#     offset: int = 0,
#     filters: Optional[Dict[str, Any]] = None,
#     sort: Optional[List[Dict[str, str]]] = None,
# ) -> Dict[str, Any]:
#     """
#     Fetch all documents' metadata in flattened format with optional filtering, sorting, and pagination.

#     This function retrieves document-level metadata from Qdrant, applies optional filters and sorting,
#     and returns the results in a flattened format suitable for tabular display or export.

#     Args:
#         knowledge_id: The knowledge base ID
#         limit: Maximum number of documents to return (1-100)
#         offset: Number of documents to skip for pagination
#         filters: Optional filter conditions dict (see examples below)
#         sort: Optional sort configuration list (see examples below)

#     Returns:
#         Dictionary containing:
#         - documents: List of flattened document metadata dicts
#         - limit: Number of documents in response
#         - offset: Offset used for pagination
#         - has_more: Boolean indicating if more documents exist

#     Filter Examples:
#         {
#             "document_type": "resume",              # Exact match
#             "location.city": "Bangalore",           # Nested dict field
#             "topics": ["rag", "llm"],               # List (any match)
#             "experience[].year": {"gte": "2022"},   # Array of dicts with range
#             "experience[].degree": {"in": ["M.Tech", "MS"]}  # Array with in operator
#         }

#     Sort Examples:
#         [
#             {"field": "title", "order": "asc"},
#             {"field": "created_at", "order": "desc"}
#         ]

#     Flattening Rules:
#         - dict: location: {city: "NYC"} → location_city: "NYC"
#         - List[str]: topics: ["AI", "ML"] → topics: ["AI", "ML"] (unchanged)
#         - List[dict]: exp: [{year: 2020}] → exp_0_year: 2020
#         - Nested: a: {b: {c: "val"}} → a_b_c: "val"
#     """
#     try:
#         # 1. Check if document metadata is enabled for this knowledge base
#         metadata_enabled = await get_document_metadata_enabled(knowledge_id)
#         if not metadata_enabled:
#             logging.info("Metadata disabled")
#             return {
#                 "documents": [],
#                 "limit": limit,
#                 "offset": offset,
#                 "has_more": False
#             }

#         # 2. Initialize Qdrant document indexer
#         _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
#         indexer = await _get_document_indexer(embedding_model)

#         # 3. Fetch field configurations to determine which fields are active
#         from utils.db.enrichment_db_utils import get_field_configurations_sqlmodel_v2
#         field_configurations = await get_field_configurations_sqlmodel_v2(knowledge_id)

#         # Filter for document-level active fields only
#         active_field_names = {
#             config["field_name"]
#             for config in field_configurations
#             if config.get("level") == "document"
#             and config.get("is_active", True)
#         }

#         # 4. Build Qdrant filter conditions
#         # Start with base filter for knowledge_id (required)
#         must_conditions = [
#             FieldCondition(key="knowledge_id", match=MatchValue(value=knowledge_id))
#         ]

#         # Add user-specified filters if provided
#         if filters:
#             try:
#                 filter_conditions = _build_filter_conditions_from_dict(filters)
#                 must_conditions.extend(filter_conditions)
#                 logging.debug(f"Filters: {len(filter_conditions)}")
#             except Exception as e:
#                 logging.warning(f"Filter build error: {e}")

#         filter_condition = Filter(must=must_conditions)

#         # 5. Determine if we need to fetch all documents (for sorting) or just a page
#         # If sorting is requested, we need to fetch all matching documents, sort them, then paginate
#         # If no sorting, we can use Qdrant's pagination directly

#         all_documents = []
#         next_page_offset = None

#         if sort:
#             # Fetch ALL matching documents for in-memory sorting
#             # Note: This can be memory-intensive for large datasets
#             # Consider adding a max limit or using Qdrant's native sorting if available
#             logging.info(f"Fetching all documents for sorting with {len(sort)} sort fields")

#             while True:
#                 response = await indexer.client_async.scroll(
#                     collection_name=indexer.collection_name,
#                     scroll_filter=filter_condition,
#                     with_payload=True,
#                     limit=100,  # Batch size for scrolling
#                     offset=next_page_offset
#                 )

#                 # Handle response format: (records, next_offset) tuple
#                 records, next_page_offset = response if isinstance(response, tuple) else (response, None)

#                 if not records:
#                     break

#                 all_documents.extend(records)

#                 if next_page_offset is None:
#                     break

#             # Sort the documents in memory
#             all_documents = _sort_documents_in_memory(all_documents, sort)

#             # Apply pagination after sorting
#             paginated_documents = all_documents[offset:offset + limit]
#             has_more = (offset + limit) < len(all_documents)

#         else:
#             # No sorting - use Qdrant's efficient scroll-based pagination
#             # Scroll until we have enough documents (offset + limit)
#             current_skip = 0
#             documents_collected = 0

#             while documents_collected < (offset + limit):
#                 response = await indexer.client_async.scroll(
#                     collection_name=indexer.collection_name,
#                     scroll_filter=filter_condition,
#                     with_payload=True,
#                     limit=100,  # Batch size for scrolling
#                     offset=next_page_offset
#                 )

#                 # Handle response format: (records, next_offset) tuple
#                 records, next_page_offset = response if isinstance(response, tuple) else (response, None)

#                 if not records:
#                     break

#                 for point in records:
#                     # Skip documents until we reach the offset
#                     if current_skip < offset:
#                         current_skip += 1
#                         continue

#                     # Stop if we've collected enough documents
#                     if documents_collected >= limit:
#                         break

#                     all_documents.append(point)
#                     documents_collected += 1

#                 # Stop if we've collected enough or no more documents
#                 if documents_collected >= limit or next_page_offset is None:
#                     break

#             paginated_documents = all_documents
#             has_more = next_page_offset is not None

#         # 6. Process each document: filter fields and flatten metadata
#         flattened_documents = []

#         for point in paginated_documents:
#             payload = getattr(point, "payload", None) or {}

#             # Filter by active document-level fields
#             filtered_payload = {
#                 k: v for k, v in payload.items()
#                 if k in active_field_names
#             }

#             # Exclude system fields (but keep object_id separate for top level)
#             # System fields: knowledge_id, created_at, chunk_idx (internal tracking)
#             fields_to_exclude = {"knowledge_id", "created_at", "chunk_idx"}
#             filtered_payload = {
#                 k: v for k, v in filtered_payload.items()
#                 if k not in fields_to_exclude
#             }

#             # Flatten the metadata according to the rules
#             flattened = flatten_metadata(filtered_payload)

#             # Add object_id at top level (not flattened, per spec)
#             flattened["object_id"] = payload.get("object_id")

#             flattened_documents.append(flattened)

#         # 7. Return response with pagination metadata
#         return {
#             "documents": flattened_documents,
#             "limit": limit,
#             "offset": offset,
#             "has_more": has_more
#         }

#     except Exception as e:
#         logging.error(f"Error fetching documents: {e}")
#         # Return empty result on error (graceful degradation)
#         return {
#             "documents": [],
#             "limit": limit,
#             "offset": offset,
#             "has_more": False
#         }
#         raise


