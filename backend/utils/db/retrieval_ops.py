import logging
import re
import traceback
from datetime import datetime
from utils.db.session import async_session
from utils.db.retry_utils import retry_db_connection
import json
from sqlmodel import select, and_, desc, func

from utils.pydantic_models_knowledge import MetadataResponse, MetadataField
from utils.pydantic_models_knowledge import KnowledgeConfig, Document  # ChatbotDetails, ExtractedMetadata removed
from utils.pydantic_models_enrichment import FieldConfiguration

from qdrant_client.http.models import Filter, FieldCondition, Nested, NestedCondition, MatchAny, MatchValue, MatchText, IsEmptyCondition, IsNullCondition, PayloadField, OrderBy, Direction, PayloadSchemaType

from typing import Dict, Union, Optional, List, Tuple, Any
from types import SimpleNamespace
import asyncio

from utils.tracing import track_time

logger = logging.getLogger(__name__)


def _map_field_type_to_qdrant_schema(field_type: str) -> str:
    """
    Map internal field types to Qdrant payload index schema values.
    Defaults to "keyword" when type is unknown.
    """
    if not field_type:
        return "keyword"
    normalized = field_type.strip()
    normalized_lower = normalized.lower()
    if normalized_lower.startswith("fieldtype."):
        normalized_lower = normalized_lower.split(".", 1)[-1]
    mapping = {
        "str": "keyword",
        # "string": "keyword",
        # "list[str]": "keyword",
        # "list_str": "keyword",
        "int": "integer",
        "integer": "integer",
        "float": "float",
        "bool": "keyword",
        # "dict": "keyword",
        # "list[dict]": "keyword",
        # "list_dict": "keyword",
    }
    if normalized_lower in mapping:
        return mapping[normalized_lower]
    logger.warning(f"Unknown field type '{normalized}' for Qdrant schema; defaulting to keyword")
    return "keyword"


def _get_qdrant_field_schema(field_config: FieldConfiguration, qdrant_field_name: str) -> str:
    """
    Derive the correct Qdrant field_schema from field configuration.
    Handles nested fields using the schema dictionary when available.
    """
    if not field_config:
        return "keyword"

    # Nested fields like address[].city
    if ("." in qdrant_field_name) or ("[]" in qdrant_field_name):
        nested_name = qdrant_field_name.split(".")[-1].replace("[]", "")
        if isinstance(field_config.field_schema, dict):
            nested_field_type = field_config.field_schema.get(nested_name)
            if nested_field_type:
                return _map_field_type_to_qdrant_schema(str(nested_field_type))

    return _map_field_type_to_qdrant_schema(field_config.field_type)


def _normalize_dict_for_comparison(val):
    """
    Convert a dict to a deterministic JSON string for content-based comparison.
    Non-dict values or non-serializable dicts return None.
    """
    if not isinstance(val, dict):
        return None
    try:
        return json.dumps(val, sort_keys=True)
    except TypeError:
        return None


def _extract_unique_values_by_field_type(raw_values: List[object], field_type: str) -> List[object]:
    """
    Normalize and deduplicate raw values based on field type.
    - List[str]: flatten and return unique strings
    - dict: return unique dicts by content
    - List[dict]: flatten and return unique dicts by content
    - default: return unique string representations
    """
    if not raw_values:
        return []

    if field_type == 'List[str]':
        flattened_values = []
        for value in raw_values:
            if isinstance(value, list):
                flattened_values.extend(value)
            elif isinstance(value, str):
                # Handle case where value might be a string representation of a list
                try:
                    parsed_value = json.loads(value)
                    if isinstance(parsed_value, list):
                        flattened_values.extend(parsed_value)
                    else:
                        flattened_values.append(parsed_value)
                except Exception:
                    flattened_values.append(value)
            else:
                flattened_values.append(value)

        return list(set([str(v) for v in flattened_values if v and str(v).strip()]))

    if field_type == 'dict':
        seen = set()
        unique_dicts = []
        for value in raw_values:
            candidate = value
            if isinstance(value, str):
                try:
                    parsed_value = json.loads(value)
                    candidate = parsed_value if isinstance(parsed_value, dict) else None
                except Exception:
                    candidate = None
            elif not isinstance(value, dict):
                candidate = None

            if candidate is None:
                continue

            normalized = _normalize_dict_for_comparison(candidate)
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique_dicts.append(candidate)

        return unique_dicts

    if field_type == 'List[dict]':
        flattened_dicts = []
        for value in raw_values:
            if isinstance(value, list):
                flattened_dicts.extend([item for item in value if isinstance(item, dict)])
            elif isinstance(value, str):
                try:
                    parsed_value = json.loads(value)
                    if isinstance(parsed_value, list):
                        flattened_dicts.extend([item for item in parsed_value if isinstance(item, dict)])
                    elif isinstance(parsed_value, dict):
                        flattened_dicts.append(parsed_value)
                except Exception:
                    continue
            elif isinstance(value, dict):
                flattened_dicts.append(value)

        seen = set()
        unique_dicts = []
        for item in flattened_dicts:
            normalized = _normalize_dict_for_comparison(item)
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique_dicts.append(item)

        return unique_dicts

    # Default handling: unique string representations for non-list/non-dict fields
    processed_values = []
    for value in raw_values:
        if isinstance(value, (dict, list)):
            processed_values.append(str(value))
        elif value and str(value).strip():
            processed_values.append(str(value))
    return list(set(processed_values))


def _infer_metadata_field_type(raw_values: List[object]) -> str:
    """
    Infer a field type from sampled payload values.
    Falls back to "str" when the shape is mixed or unknown.
    """
    for value in raw_values:
        if value is None:
            continue
        if isinstance(value, list):
            meaningful_items = [item for item in value if item is not None]
            if not meaningful_items:
                return "List[str]"
            if all(isinstance(item, dict) for item in meaningful_items):
                return "List[dict]"
            return "List[str]"
        if isinstance(value, dict):
            return "dict"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "float"
        return "str"
    return "str"


def _build_qdrant_filter_conditions(
    knowledge_id: str,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
) -> Filter:
    """
    Build a Qdrant filter for document metadata aggregation.
    """
    must_conditions = [
        FieldCondition(
            key="knowledge_id",
            match=MatchValue(value=knowledge_id)
        )
    ]

    if filters:
        for filter_field_name, raw_value in filters.items():
            if raw_value is None:
                continue

            normalized_values: List[object] = []

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
                    logger.warning(f"Ignoring unsupported filter shape for field '{filter_field_name}': {raw_value}")
                    continue
                iterable = candidates
            elif isinstance(raw_value, list):
                iterable = raw_value
            else:
                iterable = [raw_value]

            for v in iterable:
                if v is None:
                    continue
                if isinstance(v, (str, int)):
                    normalized_values.append(v)
                elif not isinstance(v, (dict, set, list, tuple)):
                    normalized_values.append(str(v))

            cleaned_values: List[object] = []
            for v in normalized_values:
                if isinstance(v, str):
                    if v.strip():
                        cleaned_values.append(v)
                else:
                    cleaned_values.append(v)

            if not cleaned_values:
                continue

            if len(cleaned_values) == 1:
                must_conditions.append(
                    FieldCondition(key=filter_field_name, match=MatchValue(value=cleaned_values[0]))
                )
            else:
                must_conditions.append(
                    FieldCondition(key=filter_field_name, match=MatchAny(any=cleaned_values))
                )

    return Filter(must=must_conditions)

# REMOVED: get_all_metadata — dead code, no router calls this. Aggregated
# metadata reads now served by get_metadata_for_field_config (Qdrant facet/scroll).
# @track_time("DB: Get All Metadata")
# @retry_db_connection(max_retries=3)
# async def get_all_metadata(knowledge_id):
#     """
#     Dynamically retrieves all metadata based on enabled field configurations from the field_configuration table
#     and fetches corresponding values from extracted_metadata table using separate queries and Python processing.
#     """
#     try:
#         async with async_session() as session:
#             field_configs_statement = select(FieldConfiguration).where(
#                 and_(
#                     FieldConfiguration.knowledge_id == knowledge_id,
#                     FieldConfiguration.is_active == True,
#                     ~FieldConfiguration.field_type.in_(['chunks', 'text']),
#                     ~FieldConfiguration.field_name.in_(['title', 'summary'])
#                 )
#             )
#             field_configs_result = await session.execute(field_configs_statement)
#             field_configs = field_configs_result.scalars().all()
#             if not field_configs:
#                 logger.warning(f"No enabled field configurations found for knowledge_id: {knowledge_id}")
#                 return MetadataResponse()
#             config_ids = [config.id for config in field_configs]
#             extracted_metadata_statement = select(ExtractedMetadata).where(
#                 and_(
#                     ExtractedMetadata.knowledge_id == knowledge_id,
#                     ExtractedMetadata.field_configuration_id.in_(config_ids),
#                     ExtractedMetadata.extracted_content.isnot(None)
#                 )
#             )
#             extracted_metadata_result = await session.execute(extracted_metadata_statement)
#             extracted_metadata = extracted_metadata_result.scalars().all()
#         metadata_by_config_id = {}
#         for metadata in extracted_metadata:
#             config_id = metadata.field_configuration_id
#             if config_id not in metadata_by_config_id:
#                 metadata_by_config_id[config_id] = []
#             metadata_by_config_id[config_id].append(metadata.extracted_content)
#         metadata_fields = []
#         for config in field_configs:
#             raw_values = metadata_by_config_id.get(config.id, [])
#             unique_values = []
#             if raw_values:
#                 if config.field_type == 'List[str]':
#                     flattened_values = []
#                     for value in raw_values:
#                         if isinstance(value, list):
#                             flattened_values.extend(value)
#                         elif isinstance(value, str):
#                             try:
#                                 parsed_value = json.loads(value)
#                                 flattened_values.extend(parsed_value) if isinstance(parsed_value, list) else flattened_values.append(parsed_value)
#                             except:
#                                 flattened_values.append(value)
#                         else:
#                             flattened_values.append(value)
#                     unique_values = list(set([str(v) for v in flattened_values if v and str(v).strip()]))
#                 else:
#                     processed_values = []
#                     for value in raw_values:
#                         if isinstance(value, (dict, list)):
#                             processed_values.append(str(value))
#                         elif value and str(value).strip():
#                             processed_values.append(str(value))
#                     unique_values = list(set(processed_values))
#             metadata_fields.append(MetadataField(
#                 field_name=config.field_name, field_type=config.field_type,
#                 description=config.description, is_custom=config.is_custom,
#                 is_active=config.is_active, is_required=config.is_required,
#                 sample_values=config.sample_values, restrict_to_choices=config.restrict_to_choices,
#                 max_items=config.max_items, values=unique_values
#             ))
#             logger.info(f"Retrieved {len(unique_values)} unique values for field {config.field_name}")
#         return MetadataResponse(fields=metadata_fields)
#     except Exception as e:
#         logger.error(f"Error retrieving metadata: {e}")
#         raise

@track_time("Qdrant: Get Summaries for Object IDs")
async def get_summaries_for_object_ids(knowledge_id, object_ids, include_metadata: bool = False):
    """
    Why:
        Single batch fetch of summaries from the Qdrant document collection.
        summary/file_name/url are written to the doc payload at ingestion — no SQL join needed.

    Guarantees:
        - One Qdrant scroll regardless of len(object_ids).
        - Missing object_ids are silently absent from the result (not an error).
        - System fields (object_id, knowledge_id, created_at) are never exposed in metadata.
        - Returns {} on empty input without hitting Qdrant.
    """
    from utils.files.retrieval_utils import _get_document_indexer
    try:
        if not object_ids or not knowledge_id:
            return {}

        _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
        indexer = await _get_document_indexer(embedding_model)

        payload_spec = True if include_metadata else ["object_id", "summary", "file_name", "url"]
        response = await indexer.client_async.scroll(
            collection_name=indexer.collection_name,
            scroll_filter=Filter(
                must=[FieldCondition(key="object_id", match=MatchAny(any=object_ids))]
            ),
            limit=len(object_ids),
            with_payload=payload_spec,
        )

        records = response[0] if isinstance(response, tuple) else []
        _sys_fields = {"object_id", "knowledge_id", "created_at"}
        summaries = {}
        for record in records:
            payload = record.payload or {}
            oid = str(payload.get("object_id", ""))
            if not oid:
                continue
            summary = payload.get("summary", "")
            # enrichment may store summary as a dict; unwrap it
            if isinstance(summary, dict):
                summary = summary.get("summary", str(summary))
            filename = payload.get("url") or payload.get("file_name") or oid
            entry: dict = {"summary": str(summary), "filename": filename}
            if include_metadata:
                entry["metadata"] = {k: v for k, v in payload.items() if k not in _sys_fields}
            summaries[oid] = entry

        logger.info(f"get_summaries_for_object_ids: fetched {len(summaries)}/{len(object_ids)} for knowledge={knowledge_id}")
        return summaries

    except Exception as e:
        logger.error(f"get_summaries_for_object_ids failed knowledge={knowledge_id} count={len(object_ids)}: {e}")
        raise


def _reciprocal_rank_fusion(result_lists: List[List[Any]], limit: int, k: int = 60) -> List[Any]:
    """
    Fuse multiple ranked Qdrant result lists via Reciprocal Rank Fusion.

    Why:
        Mirrors Qdrant's native FusionQuery(RRF) for servers that predate the
        Query API (/points/query, added in server 1.10). On older servers that
        endpoint 404s, so dense + sparse are searched separately and fused here.

    Each point's fused score is sum(1 / (k + rank)) across the lists it appears
    in (rank is 1-based; k=60 matches Qdrant's default). Dedup is by point id.
    Returns SimpleNamespace items exposing .payload and .score so downstream
    consumers are identical to a native query_points() result.
    """
    fused: Dict[Any, Dict[str, Any]] = {}
    for results in result_lists:
        for rank, point in enumerate(results, start=1):
            contribution = 1.0 / (k + rank)
            entry = fused.get(point.id)
            if entry is None:
                fused[point.id] = {"payload": point.payload, "score": contribution}
            else:
                entry["score"] += contribution
    ranked = sorted(fused.values(), key=lambda e: e["score"], reverse=True)[:limit]
    return [SimpleNamespace(payload=e["payload"], score=e["score"]) for e in ranked]


@track_time("Qdrant: Search Documents by Query")
async def search_documents_by_query(
    knowledge_id: str,
    query: str,
    embedding_model: str,
    metadata_filter: Optional[Dict[str, Any]] = None,
    limit: int = 10,
    mode: str = "hybrid",
    include_metadata: bool = False,
) -> List[Dict[str, Any]]:
    """
    Why:
        Ranks documents by relevance to a query against the Qdrant document collection.
        Hybrid mode fuses dense + BM25 via RRF — better recall than semantic-only for short queries.

    Guarantees:
        - Result order reflects Qdrant score (descending); callers must not re-sort by insertion order.
        - metadata_filter narrows the candidate set before scoring — it does not post-filter.
        - System fields (object_id, knowledge_id, created_at) are never exposed in metadata.
        - Raises on Qdrant failure so callers can decide whether to retry or degrade.
    """
    from utils.files.retrieval_utils import _get_document_indexer
    from qdrant_client import models as qdrant_models
    try:
        indexer = await _get_document_indexer(embedding_model)

        must_conditions: List[Any] = [
            FieldCondition(key="knowledge_id", match=MatchValue(value=knowledge_id)),
        ]
        if metadata_filter:
            must_conditions.extend(_build_filter_conditions_from_dict(metadata_filter))

        qdrant_filter = Filter(must=must_conditions)
        dense_vector = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: indexer.embedding_function.embed_query(query),
        )

        payload_spec = True if include_metadata else ["object_id", "file_name", "url"]

        if mode == "hybrid":
            # The Query API (/points/query) used for native RRF fusion was added in
            # Qdrant server 1.10; older servers 404 on it. Search dense + sparse
            # separately via the legacy /points/search route and fuse in Python.
            sparse_obj = next(indexer.bm25_model.query_embed(query))
            sparse_vector = sparse_obj.as_object()
            dense_hits, sparse_hits = await asyncio.gather(
                indexer.client_async.search(
                    collection_name=indexer.collection_name,
                    query_vector=qdrant_models.NamedVector(
                        name=indexer.embedding_model_name, vector=dense_vector
                    ),
                    query_filter=qdrant_filter,
                    limit=limit,
                    with_payload=payload_spec,
                ),
                indexer.client_async.search(
                    collection_name=indexer.collection_name,
                    query_vector=qdrant_models.NamedSparseVector(
                        name="bm25", vector=qdrant_models.SparseVector(**sparse_vector)
                    ),
                    query_filter=qdrant_filter,
                    limit=limit,
                    with_payload=payload_spec,
                ),
            )
            hits = _reciprocal_rank_fusion([dense_hits, sparse_hits], limit=limit)
        else:
            hits = await indexer.client_async.search(
                collection_name=indexer.collection_name,
                query_vector=(indexer.embedding_model_name, dense_vector),
                query_filter=qdrant_filter,
                limit=limit,
                with_payload=payload_spec,
            )

        _sys_fields = {"object_id", "knowledge_id", "created_at"}
        ranked = []
        for hit in hits:
            payload = hit.payload or {}
            oid = str(payload.get("object_id", ""))
            if not oid:
                continue
            filename = payload.get("url") or payload.get("file_name") or oid
            entry: Dict[str, Any] = {"object_id": oid, "filename": filename, "score": hit.score}
            if include_metadata:
                entry["metadata"] = {k: v for k, v in payload.items() if k not in _sys_fields}
            ranked.append(entry)

        logger.info(f"search_documents_by_query: mode={mode} returned {len(ranked)} hits knowledge={knowledge_id}")
        return ranked

    except Exception as e:
        logger.error(f"search_documents_by_query failed mode={mode} knowledge={knowledge_id}: {e}")
        raise


# REMOVED: old SQL implementation of get_summaries_for_object_ids
# Joined extracted_metadata + document tables via subquery on field_configuration.
# Replaced by Qdrant scroll above — summary/file_name/url already in doc payload.
# async def _get_summaries_for_object_ids_sql(knowledge_id, object_ids):
#     async with async_session() as session:
#         summary_config_subquery = select(FieldConfiguration.id).where(
#             and_(FieldConfiguration.knowledge_id == knowledge_id,
#                  FieldConfiguration.field_name == 'summary',
#                  FieldConfiguration.is_active == True)
#         ).limit(1).scalar_subquery()
#         statement = select(
#             ExtractedMetadata.object_id,
#             ExtractedMetadata.extracted_content.label('summary'),
#             func.coalesce(Document.url, Document.file_name).label('filename')
#         ).join(Document, ExtractedMetadata.object_id == Document.object_id, isouter=True
#         ).where(and_(
#             ExtractedMetadata.field_configuration_id == summary_config_subquery,
#             ExtractedMetadata.object_id.in_(object_ids),
#             ExtractedMetadata.extracted_content.isnot(None)
#         ))
#         result = await session.execute(statement)
#         rows = result.fetchall()
#     summaries = {}
#     for row in rows:
#         oid = str(row.object_id)
#         summary = row.summary
#         if isinstance(summary, dict):
#             summary = summary.get('summary', str(summary))
#         summaries[oid] = {"summary": str(summary), "filename": row.filename or oid}
#     return summaries


@track_time("DB: Get Hybrid Search Value")
@retry_db_connection(max_retries=3)
async def get_hybrid_search_value(knowledge_id: str):
    try:
        async with async_session() as session:
            statement = select(KnowledgeConfig.hybrid_search_enabled, KnowledgeConfig.embedding_model).where(
                and_(
                    KnowledgeConfig.knowledge_id == knowledge_id,
                    KnowledgeConfig.deleted == False
                )
            )
            result = await session.execute(statement)
            row = result.first()
            
        if row:
            logger.info(f"Retrieved hybrid search value for Knowledge ID: {knowledge_id}")
            return row[0], row[1]
        else:
            logger.info(f"No hybrid search value found for Knowledge ID: {knowledge_id}")
            return None, None
    except Exception as e:
        logger.error(f"Database error occurred during get_hybrid_search_value: {str(e)}")
        raise

@track_time("DB: Check Document Metadata Enabled")
@retry_db_connection(max_retries=3)
async def get_document_metadata_enabled(knowledge_id: str) -> bool:
    """
    Check if document_level_metadata is enabled for a knowledge service.
    
    Args:
        knowledge_id (str): The knowledge service ID
        
    Returns:
        bool: True if document_level_metadata is enabled, False otherwise
    """
    try:
        async with async_session() as session:
            statement = select(KnowledgeConfig.document_level_metadata).where(
                and_(
                    KnowledgeConfig.knowledge_id == knowledge_id,
                    KnowledgeConfig.deleted == False
                )
            )
            result = await session.execute(statement)
            row = result.first()
            
        if row:
            logger.info(f"Document metadata enabled for Knowledge ID {knowledge_id}: {row[0]}")
            return row[0] if row[0] is not None else True
        else:
            logger.info(f"No config found for knowledge {knowledge_id}, defaulting to True")
            return True
    except Exception as e:
        logger.error(f"Error checking document_metadata_enabled for knowledge {knowledge_id}: {str(e)}")
        raise

@track_time("DB: Get Object IDs from DB")
@retry_db_connection(max_retries=3)
async def get_object_ids_from_db(knowledge_id: str, limit: int = 15) -> list[str]:
    """
    Retrieve object_ids from the Document table for a given knowledge base.
    
    Args:
        knowledge_id (str): The knowledge ID
        limit (int): Maximum number of object_ids to return
    
    Returns:
        list[str]: List of object_ids
    """
    try:
        async with async_session() as session:
            statement = select(Document.object_id).where(
                and_(
                    Document.knowledge_id == knowledge_id,
                    Document.deleted == False
                )
            ).limit(limit)
            
            result = await session.execute(statement)
            rows = result.scalars().all()
            
        object_ids = [str(row) for row in rows if row]
        logger.info(f"Retrieved {len(object_ids)} object_ids from DB for knowledge {knowledge_id}")
        return object_ids
    except Exception as e:
        logger.error(f"Error retrieving object_ids from DB: {str(e)}")
        raise

@track_time("DB: Get Metadata from Qdrant Object IDs")
@retry_db_connection(max_retries=3)
async def get_metadata_from_qdrant_object_ids(
    knowledge_id: str, 
    limit: int = 5,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None
) -> MetadataResponse:
    """
    Retrieve metadata from Qdrant documents collection for a limited number of object_ids.
    This is more efficient than retrieving all metadata from the database.
    
    Args:
        knowledge_id (str): The ID of the knowledge base
        limit (int): Maximum number of object_ids to retrieve metadata from (default: 5)
        filters (Optional[Dict[str, Union[str, List[str]]]]): Optional filters to apply to documents
            Format: {"field_name": "value"} or {"field_name": ["value1", "value2"]}
    
    Returns:
        MetadataResponse: MetadataResponse object with metadata fields and their unique values
    """
    try:
        # Get field configurations first
        async with async_session() as session:
            field_configs_statement = select(FieldConfiguration).where(
                and_(
                    FieldConfiguration.knowledge_id == knowledge_id,
                    FieldConfiguration.is_active == True,
                    FieldConfiguration.deleted == False,
                    ~FieldConfiguration.field_type.in_(['chunks']),
                    ~FieldConfiguration.field_name.in_(['prepended_doc_summary', 'chunk_text', 'chunk_keywords', 'chunk_entities', 'chunk_topic'])
                ) # Removed title and summary from the list of fields to exclude # 'title', 'summary', 
            )
            
            field_configs_result = await session.execute(field_configs_statement)
            field_configs = field_configs_result.scalars().all()
            
            if not field_configs:
                logger.warning(f"No enabled field configurations found for knowledge_id: {knowledge_id}")
                return MetadataResponse()
        
        # # Get object_ids from DB
        # object_ids = await get_object_ids_from_db(knowledge_id, limit=limit)
        
        # if not object_ids:
        #     logger.warning(f"No object_ids found for knowledge_id: {knowledge_id}")
        #     return MetadataResponse()
        
        # Get embedding model
        _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
        if not embedding_model:
            logger.warning(f"No embedding model found for knowledge_id: {knowledge_id}")
            return MetadataResponse()
        
        # Initialize MultiQdrantDocumentIndexer (using cached version)
        from utils.files.retrieval_utils import _get_document_indexer
        indexer = await _get_document_indexer(embedding_model)
        
        filter_condition = _build_qdrant_filter_conditions(
            knowledge_id=knowledge_id,
            filters=filters,
        )
        
        response = await indexer.client_async.scroll(
            collection_name=indexer.collection_name,
            scroll_filter=filter_condition,
            limit=limit,
            with_payload=True
        )
        
        # Handle response format (tuple or object)
        records = response[0] if isinstance(response, tuple) and len(response) > 0 else []
        
        if not records:
            logger.warning(f"No documents found in Qdrant for knowledge_id: {knowledge_id}")
            return MetadataResponse()
        
        # Extract metadata from all documents
        # Create a mapping of field_name to list of values
        field_to_values = {}
        
        for point in records:
            payload = getattr(point, "payload", None) or {}
            
            # Filter out system fields
            fields_to_exclude = {"object_id", "knowledge_id", "created_at"}
            filtered_payload = {k: v for k, v in payload.items() if k not in fields_to_exclude}
            
            # Only include fields that are in field configurations
            field_names = {config.field_name for config in field_configs}
            
            for field_name, value in filtered_payload.items():
                if field_name in field_names and value is not None:
                    if field_name not in field_to_values:
                        field_to_values[field_name] = []
                    field_to_values[field_name].append(value)
        
        # Process each field configuration and build MetadataField objects
        metadata_fields = []
        for config in field_configs:
            field_name = config.field_name
            field_type = config.field_type
            description = config.description
            is_custom = config.is_custom
            is_active = config.is_active
            is_required = config.is_required
            sample_values = config.sample_values
            restrict_to_choices = config.restrict_to_choices
            max_items = config.max_items
            
            # Get values for this field
            raw_values = field_to_values.get(field_name, [])
            unique_values = []
            
            if raw_values:
                # Process values based on field type (same logic as get_all_metadata)
                unique_values = _extract_unique_values_by_field_type(raw_values, field_type)
            
            metadata_field = MetadataField(
                field_name=field_name,
                field_type=field_type,
                description=description,
                is_custom=is_custom,
                is_active=is_active,
                is_required=is_required,
                sample_values=sample_values,
                restrict_to_choices=restrict_to_choices,
                max_items=max_items,
                values=unique_values
            )
            metadata_fields.append(metadata_field)
            logger.info(f"Retrieved {len(unique_values)} unique values for field {field_name}")
        
        return MetadataResponse(fields=metadata_fields)
        
    except Exception as e:
        logger.error(f"Error retrieving metadata from Qdrant for knowledge_id {knowledge_id}: {e}")
        # Return empty response on error to maintain backward compatibility
        return MetadataResponse()


@track_time("DB: Get User Defined Metadata from Qdrant Object IDs")
@retry_db_connection(max_retries=3)
async def get_user_defined_metadata_from_qdrant_object_ids(
    knowledge_id: str,
    limit: int = 5,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
    field_name: Optional[str] = None,
    field_names: Optional[List[str]] = None,
    field_types: Optional[Dict[str, str]] = None,
) -> MetadataResponse:
    """
    Retrieve metadata values for payload fields that are present on documents
    and were supplied via document.extra_metadata.

    Values are still sampled from up to ``limit`` documents. When ``field_names`` is
    provided (the persisted user-defined field manifest), every name in it is guaranteed
    to appear in the response even if no value was found in the sample — this makes field
    DISCOVERY deterministic and complete while keeping value sampling bounded. For names
    with no sampled value, ``field_types`` supplies the type (fallback "str").
    When ``field_names`` is None, behavior is unchanged (backward compatible).
    """
    try:
        object_ids: Optional[List[str]] = None
        if filters:
            _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
            if not embedding_model:
                logger.warning(f"No embedding model found for knowledge_id: {knowledge_id}")
                return MetadataResponse()

            from utils.files.retrieval_utils import _get_document_indexer
            indexer = await _get_document_indexer(embedding_model)
            filter_condition = _build_qdrant_filter_conditions(
                knowledge_id=knowledge_id,
                filters=filters,
            )

            response = await indexer.client_async.scroll(
                collection_name=indexer.collection_name,
                scroll_filter=filter_condition,
                limit=limit,
                with_payload=["object_id"]
            )
            records = response[0] if isinstance(response, tuple) and len(response) > 0 else []
            object_ids = [
                str((getattr(point, "payload", None) or {}).get("object_id"))
                for point in records
                if (getattr(point, "payload", None) or {}).get("object_id")
            ]
            if not object_ids:
                return MetadataResponse()

        raw_values_by_field: Dict[str, List[object]] = {}
        async with async_session() as session:
            statement = select(Document.extra_metadata).where(
                and_(
                    Document.knowledge_id == knowledge_id,
                    Document.deleted == False,
                    Document.extra_metadata.isnot(None),
                )
            )
            if object_ids is not None:
                statement = statement.where(Document.object_id.in_(object_ids))
            statement = statement.limit(limit)

            result = await session.execute(statement)
            extra_metadata_rows = result.scalars().all()

        for extra_metadata in extra_metadata_rows:
            if not isinstance(extra_metadata, dict):
                continue
            for extra_metadata_field_name, value in extra_metadata.items():
                if field_name and extra_metadata_field_name != field_name:
                    continue
                if value is None:
                    continue
                raw_values_by_field.setdefault(extra_metadata_field_name, []).append(value)

        # Names to emit: keys discovered in the sample, plus (when not doing a single-field
        # lookup) every name from the manifest so discovery is complete and deterministic.
        names_to_emit = set(raw_values_by_field.keys())
        if field_name is None and field_names:
            names_to_emit.update(field_names)

        metadata_fields: List[MetadataField] = []
        for payload_field_name in sorted(names_to_emit):
            raw_values = raw_values_by_field.get(payload_field_name, [])
            if raw_values:
                inferred_field_type = _infer_metadata_field_type(raw_values)
                unique_values = _extract_unique_values_by_field_type(raw_values, inferred_field_type)
            else:
                # Manifest field with no value in the sample: keep the persisted type.
                inferred_field_type = (field_types or {}).get(payload_field_name, "str")
                unique_values = []

            metadata_fields.append(
                MetadataField(
                    field_name=payload_field_name,
                    field_type=inferred_field_type,
                    description=None,
                    is_custom=True,
                    is_active=True,
                    is_required=False,
                    sample_values=None,
                    restrict_to_choices=False,
                    max_items=None,
                    values=unique_values,
                )
            )

        return MetadataResponse(fields=metadata_fields)

    except Exception as e:
        logger.error(f"Error retrieving user-defined metadata from Qdrant for knowledge_id {knowledge_id}: {e}")
        return MetadataResponse()

@track_time("DB: Get Metadata Values for Field")
@retry_db_connection(max_retries=3)
async def get_metadata_values_for_field(
    knowledge_id: str,
    field_name: str,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
    limit: int = 100
) -> MetadataField:
    """
    Retrieve unique metadata values for a specific field with optional filtering.
    
    Args:
        knowledge_id (str): The ID of the knowledge base
        field_name (str): The name of the field to retrieve values for.
            Supports dot-notation for nested fields (e.g., 'address.city')
        filters (Optional[Dict[str, Union[str, List[str]]]]): Optional filters to apply to documents
            Format: {"field_name": "value"} or {"field_name": ["value1", "value2"]}
        limit (int): Maximum number of documents to query (default: 100)
    
    Returns:
        MetadataField: MetadataField object with unique values for the specified field
        
    Raises:
        ValueError: If field_name is not found in field configurations (for non-nested fields)
    """
    try:
        from time import perf_counter
        start_time = perf_counter()
        
        # Check if this is a nested field (contains dot notation)
        qdrant_field_name = field_name
        is_nested_field = '.' in field_name
        if is_nested_field:
            # get the base field name
            field_name = field_name.split('.')[0]
            if '[]' in field_name:
                field_name = field_name.replace('[]', '')
            else:
                field_name = field_name.split('.')[0]
        
        # Get field configuration for the specified field
        # For nested fields, check the base field configuration
        async with async_session() as session:
            field_config_statement = select(FieldConfiguration).where(
                and_(
                    FieldConfiguration.knowledge_id == knowledge_id,
                    FieldConfiguration.field_name == field_name,
                    FieldConfiguration.is_active == True,
                    FieldConfiguration.deleted == False
                )
            )
            
            field_config_result = await session.execute(field_config_statement)
            field_config = field_config_result.scalar_one_or_none()
            
            if not field_config:
                raise ValueError(f"Field '{field_name}' not found or not active for knowledge_id: {knowledge_id}")
        
        logger.info(f"Time taken to get field configuration: {perf_counter() - start_time} seconds")
        
        # Get embedding model
        _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
        if not embedding_model:
            logger.warning(f"No embedding model found for knowledge_id: {knowledge_id}")
            # Return empty MetadataField if no embedding model
            return MetadataField(
                field_name=field_name,
                field_type="str",
                description=None,
                is_custom=False,
                is_active=False,
                is_required=False,
                sample_values=None,
                restrict_to_choices=False,
                max_items=None,
                values=[]
            )
        indexer_start_time = perf_counter()
        # Initialize MultiQdrantDocumentIndexer (using cached version)
        from utils.files.retrieval_utils import _get_document_indexer
        indexer = await _get_document_indexer(embedding_model)
        logger.info(f"Time taken to initialize MultiQdrantDocumentIndexer(time taken: {perf_counter() - indexer_start_time} seconds)")
        # Build filter conditions
        must_conditions = [
            FieldCondition(
                key="knowledge_id",
                match=MatchValue(value=knowledge_id)
            )
        ]
        
        # Add filters if provided
        if filters:
            for filter_field_name, raw_value in filters.items():
                if raw_value is None:
                    continue
                
                # Normalize input into a flat list of primitive values (str | int)
                normalized_values: List[object] = []
                
                # Support common filter shapes: single value, list, or dict with $in/any/eq
                if isinstance(raw_value, dict):
                    candidates = None
                    # Array candidates
                    for k in ("$in", "in", "any", "values"):
                        if k in raw_value and isinstance(raw_value[k], list):
                            candidates = raw_value[k]
                            break
                    # Single value candidates
                    if candidates is None:
                        for k in ("eq", "value"):
                            if k in raw_value:
                                candidates = [raw_value[k]]
                                break
                    if candidates is None:
                        logger.warning(f"Ignoring unsupported filter shape for field '{filter_field_name}': {raw_value}")
                        continue
                    iterable = candidates
                elif isinstance(raw_value, list):
                    iterable = raw_value
                else:
                    iterable = [raw_value]
                
                # Keep only str|int (Qdrant MatchAny supports str or int). Convert others safely.
                for v in iterable:
                    if v is None:
                        continue
                    if isinstance(v, (str, int)):
                        normalized_values.append(v)
                    else:
                        # Convert floats and other simple scalars to string; skip nested structures
                        if not isinstance(v, (dict, set, list, tuple)):
                            normalized_values.append(str(v))
                
                # Remove empties and whitespace-only strings
                cleaned_values: List[object] = []
                for v in normalized_values:
                    if isinstance(v, str):
                        if v.strip():
                            cleaned_values.append(v)
                    else:
                        cleaned_values.append(v)
                
                if not cleaned_values:
                    continue
                
                # Add filter condition (use must for AND logic)
                if len(cleaned_values) == 1:
                    must_conditions.append(
                        FieldCondition(key=filter_field_name, match=MatchValue(value=cleaned_values[0]))
                    )
                else:
                    must_conditions.append(
                        FieldCondition(key=filter_field_name, match=MatchAny(any=cleaned_values))
                    )
        
        filter_condition = Filter(must=must_conditions) if must_conditions else None
        
        from time import time
        qdrant_start_time = time()
        
        unique_values = []
        value_counts: Optional[List[Dict[str, Any]]] = None

        # Facetable field types map cleanly onto a Qdrant keyword/integer/float
        # payload index (see _map_field_type_to_qdrant_schema) — dict and
        # List[dict] don't, since facet needs a scalar-or-array-of-scalars
        # field, not a nested object. Nested fields (address[].city) always
        # go through facet regardless of the parent's own field_type, since
        # is_nested_field already implies the leaf is a scalar.
        _FACETABLE_FIELD_TYPES = {"str", "int", "integer", "float", "bool", "list[str]"}
        field_type_normalized = (field_config.field_type or "").strip().lower()
        use_facet = is_nested_field or field_type_normalized in _FACETABLE_FIELD_TYPES

        if use_facet:
            # Facet returns BOTH the full distinct-value set and each value's
            # document count in one query — exhaustive, not a sample. This
            # replaces the old scroll-and-sample path for every field type it
            # covers: previously only nested fields used facet, so a plain
            # scalar field's "unique values" were whatever turned up in the
            # first `limit` (default 10) documents, presented as if it were
            # the complete list, with counts nowhere to be found even for the
            # nested fields that DID use facet (the API returns hit.count,
            # the old code read hit.value and discarded it).
            try:
                await indexer.client_async.create_payload_index(
                    collection_name=indexer.collection_name,
                    field_name=qdrant_field_name,
                    field_schema=_get_qdrant_field_schema(field_config, qdrant_field_name),
                )

                facet_response = await indexer.client_async.facet(
                    collection_name=indexer.collection_name,
                    key=qdrant_field_name,
                    facet_filter=filter_condition,
                    limit=limit
                )

                # FacetValue objects carry .value and .count; response shape
                # has varied across qdrant-client versions (.hits vs .values).
                hits = getattr(facet_response, "hits", None)
                if hits is None:
                    hits = getattr(facet_response, "values", None) or []

                counted_pairs = [
                    (str(hit.value), int(hit.count))
                    for hit in hits
                    if hit.value is not None and str(hit.value).strip() and getattr(hit, "count", None) is not None
                ]
                if not counted_pairs and hits:
                    logger.warning(f"Facet hits for {qdrant_field_name} had no usable (value, count) pairs")
                elif not hits:
                    logger.warning(f"No facet results found for field {qdrant_field_name}")

                # Most-common-first — this is what an Explore-style value list
                # is for; `values` stays alphabetically sorted for callers that
                # only want the distinct set.
                counted_pairs.sort(key=lambda pair: pair[1], reverse=True)
                unique_values = sorted(value for value, _count in counted_pairs)
                value_counts = [{"value": value, "count": count} for value, count in counted_pairs]

            except Exception as e:
                logger.error(f"Error using facet API for field {qdrant_field_name}: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                unique_values = []
                value_counts = None
        else:
            # dict / List[dict] fields: no Qdrant payload index supports
            # faceting a nested object, so these stay on the scroll-and-sample
            # path — `limit` documents (default 10), not exhaustive. No
            # value_counts here: a count computed from a sample would be
            # actively wrong, not merely missing, so it stays None rather
            # than being synthesized.
            response = await indexer.client_async.scroll(
                collection_name=indexer.collection_name,
                scroll_filter=filter_condition,
                limit=limit,
                with_payload=True
            )

            # Handle response format (tuple or object)
            records = response[0] if isinstance(response, tuple) and len(response) > 0 else []

            if not records:
                logger.warning(f"No documents found in Qdrant for knowledge_id: {knowledge_id} with filters")
                # Return field config with empty values
                return MetadataField(
                    field_name=field_config.field_name,
                    field_type=field_config.field_type,
                    description=field_config.description,
                    is_custom=field_config.is_custom,
                    is_active=field_config.is_active,
                    is_required=field_config.is_required,
                    sample_values=field_config.sample_values,
                    restrict_to_choices=field_config.restrict_to_choices,
                    max_items=field_config.max_items,
                    values=[],
                    value_counts=None,
                )

            # Extract values for the specified field from filtered documents
            raw_values = []
            for point in records:
                payload = getattr(point, "payload", None) or {}
                value = payload.get(field_name)
                if value is not None:
                    raw_values.append(value)

            filter_start_time = perf_counter()
            # Process values based on field type
            if raw_values:
                field_type = field_config.field_type
                unique_values = _extract_unique_values_by_field_type(raw_values, field_type)
            logger.info(f"Time taken to process values: {perf_counter() - filter_start_time} seconds")

        qdrant_end_time = time()
        logger.info(f"Time taken to query Qdrant: {qdrant_end_time - qdrant_start_time} seconds")
        logger.info(f"Retrieved {len(unique_values)} unique values for field {field_name} from Qdrant")

        # Build and return MetadataField
        # For nested fields, use Qdrant notation (address[].city) in response
        # For regular fields, use the original field_name
        response_field_name = qdrant_field_name if is_nested_field else field_name
        return MetadataField(
            field_name=response_field_name,  # Use Qdrant notation for nested fields (e.g., "address[].city")
            field_type=field_config.field_type,
            description=field_config.description,
            is_custom=field_config.is_custom,
            is_active=field_config.is_active,
            is_required=field_config.is_required,
            sample_values=field_config.sample_values,
            restrict_to_choices=field_config.restrict_to_choices,
            max_items=field_config.max_items,
            values=unique_values,
            value_counts=value_counts,
        )

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Error retrieving metadata values for field {field_name} in knowledge_id {knowledge_id}: {e}")
        raise

def flatten_metadata(data: Dict[str, Any], parent_key: str = "", sep: str = "_") -> Dict[str, Any]:
    """
    Recursively flatten nested dictionary and list structures according to specific rules.

    Flattening rules:
    - dict: Flatten with separator (e.g., location: {city: "NYC"} → location_city: "NYC")
    - List[str]: Keep as array (e.g., topics: ["AI", "ML"] stays unchanged)
    - List[dict]: Flatten each dict with index (e.g., exp: [{year: 2020}] → exp_0_year: 2020)
    - Nested dict: Recursively flatten (e.g., a: {b: {c: "val"}} → a_b_c: "val")

    Args:
        data: Dictionary to flatten
        parent_key: Parent key for recursive calls (used for prefixing)
        sep: Separator for concatenating keys (default: "_")

    Returns:
        Flattened dictionary with keys following the flattening rules

    Examples:
        >>> flatten_metadata({"location": {"city": "NYC", "country": "USA"}})
        {"location_city": "NYC", "location_country": "USA"}

        >>> flatten_metadata({"topics": ["AI", "ML"]})
        {"topics": ["AI", "ML"]}

        >>> flatten_metadata({"exp": [{"year": 2020, "company": "Google"}]})
        {"exp_0_year": 2020, "exp_0_company": "Google"}
    """
    items = []

    for key, value in data.items():
        # Build the new key by concatenating parent and current key
        new_key = f"{parent_key}{sep}{key}" if parent_key else key

        if value is None:
            # Keep None values as-is
            items.append((new_key, None))

        elif isinstance(value, dict):
            # Recursively flatten nested dictionaries
            items.extend(flatten_metadata(value, new_key, sep=sep).items())

        elif isinstance(value, list):
            if not value:
                # Empty list - keep as-is
                items.append((new_key, []))

            elif all(isinstance(item, str) for item in value):
                # List[str] - keep as array (per spec)
                items.append((new_key, value))

            elif all(isinstance(item, (int, float, bool)) for item in value):
                # List of scalar types - keep as array
                items.append((new_key, value))

            elif any(isinstance(item, dict) for item in value):
                # List[dict] - flatten each dict with index (per spec)
                for idx, item in enumerate(value):
                    if isinstance(item, dict):
                        indexed_key = f"{new_key}{sep}{idx}"
                        items.extend(flatten_metadata(item, indexed_key, sep=sep).items())
                    else:
                        # Mixed list with non-dict items - flatten with index
                        items.append((f"{new_key}{sep}{idx}", item))

            else:
                # Other list types - keep as-is
                items.append((new_key, value))

        else:
            # Scalar values (str, int, float, bool) - keep as-is
            items.append((new_key, value))

    return dict(items)


def _build_filter_conditions_from_dict(filters: Dict[str, Any]) -> List[Any]:
    """
    Build Qdrant filter conditions from filter dictionary.

    Supports various filter types:
    - Scalar fields: {"document_type": "resume"}
    - Nested dict fields: {"location.city": "Bangalore"}
    - List fields (any match): {"topics": ["rag", "llm"]}
    - Array of dicts: {"experience[].year": {"gte": "2022"}}
    - Range operators: {"year": {"gte": 2020, "lte": 2025}}
    - In operator: {"degree": {"in": ["M.Tech", "MS"]}}
    - Extended op syntax: {"title": {"op": "contains", "value": "engineer"}}

    Returns a list of condition objects (FieldCondition, NestedCondition, or Filter)
    all safe to place directly inside a Qdrant Filter(must=[...]).
    """
    from qdrant_client.models import Range

    logger.info(f"Building filter conditions for {len(filters)} field(s): {list(filters.keys())}")
    conditions = []

    for field_path, value in filters.items():
        try:
            if "[]." in field_path:
                # Array of dicts — wrap inner condition in NestedCondition
                array_field, nested_key = field_path.split("[].")
                logger.info(f"Filter [nested-array] field='{field_path}' array='{array_field}' key='{nested_key}' value={value!r}")
                inner_cond, negate = _build_simple_field_condition(nested_key, value)
                nested = NestedCondition(
                    nested=Nested(key=array_field, filter=Filter(must=[inner_cond]))
                )
                if negate:
                    logger.info(f"Filter [nested-array] field='{field_path}' → must_not[NestedCondition]")
                    conditions.append(Filter(must_not=[nested]))
                else:
                    logger.info(f"Filter [nested-array] field='{field_path}' → NestedCondition(must)")
                    conditions.append(nested)
            else:
                logger.info(f"Filter [simple] field='{field_path}' value={value!r}")
                cond, negate = _build_simple_field_condition(field_path, value)
                if negate:
                    logger.info(f"Filter [simple] field='{field_path}' → must_not")
                    conditions.append(Filter(must_not=[cond]))
                else:
                    logger.info(f"Filter [simple] field='{field_path}' → must")
                    conditions.append(cond)
        except Exception as e:
            logger.error(f"Failed to build condition for field='{field_path}' value={value!r}: {e}")
            raise

    logger.info(f"Built {len(conditions)} filter condition(s)")
    return conditions


_RANGE_OPS = ("gte", "lte", "gt", "lt")


def _looks_like_date_bound(v: Any) -> bool:
    """Is this range bound a date/datetime string rather than a number?

    Decided from the VALUE, not the field config, because this module builds
    conditions from a filter dict and has no field_configurations to consult.
    That is the right call anyway: the bound the caller wrote is what has to
    match the index, and a `date` field's bounds are always ISO strings.
    """
    if not isinstance(v, str):
        return False
    try:
        # Accepts "2024-05-01" and full RFC-3339 timestamps, which is exactly
        # the set Qdrant's DATETIME index accepts.
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return True
    except (ValueError, TypeError):
        return False


def _build_range_condition(field: str, bounds: Dict[str, Any]) -> Any:
    """Build a Qdrant range condition, picking Range vs DatetimeRange by value type.

    A `date` field is indexed as PayloadSchemaType.DATETIME (see
    _get_field_index_type), and a numeric Range against a datetime index does
    not match — it fails silently, returning nothing, which is the worst
    possible failure mode for a filter. So string bounds route to DatetimeRange.
    """
    from qdrant_client.models import DatetimeRange, Range

    present = {op: bounds[op] for op in _RANGE_OPS if bounds.get(op) is not None}
    if present and all(_looks_like_date_bound(v) for v in present.values()):
        logger.info(f"Field '{field}': datetime range {present}")
        return FieldCondition(key=field, range=DatetimeRange(**present))

    # Mixed or numeric bounds. Mixed is a caller error worth naming rather than
    # silently half-applying — one numeric bound against a datetime index would
    # match nothing.
    if present and any(_looks_like_date_bound(v) for v in present.values()):
        logger.warning(
            f"Field '{field}': range mixes date and numeric bounds {present} — "
            f"building a numeric Range, which will not match a date-indexed field"
        )
    logger.info(f"Field '{field}': numeric range {present}")
    return FieldCondition(key=field, range=Range(**present))


def _build_simple_field_condition(field: str, value: Any) -> tuple:
    """
    Build a single Qdrant condition for a field.

    Returns (condition, negate: bool). When negate=True the caller must wrap
    the condition in Filter(must_not=[condition]).

    Supported value shapes:
    - Scalar / list: direct equality / any-match
    - {"gte": ..., "lte": ...}: range
    - {"in": [...]}: match any
    - {"op": "<op>", "value": ...}: extended operators
      ops: eq, ne, contains, not_contains, is_empty, is_not_empty
    """
    from qdrant_client.models import Range

    if isinstance(value, dict) and "op" in value:
        op = value["op"]
        op_value = value.get("value")
        logger.info(f"Field '{field}': op-syntax op='{op}' value={op_value!r}")

        if op == "eq":
            if isinstance(op_value, str):
                variants = list(dict.fromkeys([op_value, op_value.lower(), op_value.upper(), op_value.capitalize()]))
                logger.info(f"Field '{field}': eq(str) → MatchAny({len(variants)} variants)")
                return FieldCondition(key=field, match=MatchAny(any=variants)), False
            logger.info(f"Field '{field}': eq(non-str) → MatchValue({op_value!r})")
            return FieldCondition(key=field, match=MatchValue(value=op_value)), False

        elif op == "ne":
            if isinstance(op_value, str):
                variants = list(dict.fromkeys([op_value, op_value.lower(), op_value.upper(), op_value.capitalize()]))
                logger.info(f"Field '{field}': ne(str) → must_not MatchAny({len(variants)} variants)")
                return FieldCondition(key=field, match=MatchAny(any=variants)), True
            logger.info(f"Field '{field}': ne(non-str) → must_not MatchValue({op_value!r})")
            return FieldCondition(key=field, match=MatchValue(value=op_value)), True

        elif op == "contains":
            logger.info(f"Field '{field}': contains → MatchText({op_value!r})")
            return FieldCondition(key=field, match=MatchText(text=str(op_value))), False

        elif op == "not_contains":
            logger.info(f"Field '{field}': not_contains → must_not MatchText({op_value!r})")
            return FieldCondition(key=field, match=MatchText(text=str(op_value))), True

        elif op == "is_empty":
            logger.info(f"Field '{field}': is_empty → IsNull OR IsEmpty")
            return Filter(
                should=[
                    IsNullCondition(is_null=PayloadField(key=field)),
                    IsEmptyCondition(is_empty=PayloadField(key=field)),
                ]
            ), False

        elif op == "is_not_empty":
            logger.info(f"Field '{field}': is_not_empty → must_not(IsNull OR IsEmpty)")
            return Filter(
                should=[
                    IsNullCondition(is_null=PayloadField(key=field)),
                    IsEmptyCondition(is_empty=PayloadField(key=field)),
                ]
            ), True

        elif op in _RANGE_OPS:
            logger.info(f"Field '{field}': single-op range {op}={op_value!r}")
            return _build_range_condition(field, {op: op_value}), False

        elif op == "in":
            expanded = []
            for v in (op_value or []):
                if isinstance(v, str):
                    expanded += list(dict.fromkeys([v, v.lower(), v.upper(), v.capitalize()]))
                else:
                    expanded.append(v)
            logger.info(f"Field '{field}': in → MatchAny({len(expanded)} values)")
            return FieldCondition(key=field, match=MatchAny(any=expanded)), False

        elif op in ("nin", "not_in"):
            # Exclude any of the listed values → MatchAny wrapped in must_not by the caller.
            expanded = []
            for v in (op_value or []):
                if isinstance(v, str):
                    expanded += list(dict.fromkeys([v, v.lower(), v.upper(), v.capitalize()]))
                else:
                    expanded.append(v)
            logger.info(f"Field '{field}': {op} → must_not MatchAny({len(expanded)} values)")
            return FieldCondition(key=field, match=MatchAny(any=expanded)), True

        else:
            logger.warning(f"Field '{field}': unknown op='{op}' — skipping filter for this field")
            raise ValueError(f"Unsupported filter op '{op}' for field '{field}'")

    elif isinstance(value, dict):
        # Shorthand range: {"gte": 2020, "lte": 2025}
        if any(op in value for op in _RANGE_OPS):
            logger.info(f"Field '{field}': shorthand range {value}")
            return _build_range_condition(field, value), False
        # Shorthand in: {"in": [...]} or {"$in": [...]}
        elif "in" in value or "$in" in value:
            raw_list = value.get("in", value.get("$in")) or []
            expanded = []
            for v in raw_list:
                if isinstance(v, str):
                    expanded += list(dict.fromkeys([v, v.lower(), v.upper(), v.capitalize()]))
                else:
                    expanded.append(v)
            logger.info(f"Field '{field}': shorthand in → MatchAny({len(expanded)} values)")
            return FieldCondition(key=field, match=MatchAny(any=expanded)), False
        # Shorthand not-in: {"$nin": [...]} or {"not_in": [...]} → exclude listed values
        elif "$nin" in value or "not_in" in value:
            raw_list = value.get("$nin", value.get("not_in")) or []
            expanded = []
            for v in raw_list:
                if isinstance(v, str):
                    expanded += list(dict.fromkeys([v, v.lower(), v.upper(), v.capitalize()]))
                else:
                    expanded.append(v)
            logger.info(f"Field '{field}': shorthand not_in → must_not MatchAny({len(expanded)} values)")
            return FieldCondition(key=field, match=MatchAny(any=expanded)), True
        else:
            logger.warning(f"Field '{field}': unrecognized dict filter shape {value} — no op/range/in keys found")
            raise ValueError(f"Unrecognized filter shape for field '{field}': {value}")

    elif isinstance(value, list):
        expanded = []
        for v in value:
            if isinstance(v, str):
                expanded += list(dict.fromkeys([v, v.lower(), v.upper(), v.capitalize()]))
            else:
                expanded.append(v)
        logger.info(f"Field '{field}': list → MatchAny({len(expanded)} values)")
        return FieldCondition(key=field, match=MatchAny(any=expanded)), False

    else:
        if isinstance(value, str):
            variants = list(dict.fromkeys([value, value.lower(), value.upper(), value.capitalize()]))
            logger.info(f"Field '{field}': scalar str → MatchAny({len(variants)} variants)")
            return FieldCondition(key=field, match=MatchAny(any=variants)), False
        logger.info(f"Field '{field}': scalar {type(value).__name__} → MatchValue({value!r})")
        return FieldCondition(key=field, match=MatchValue(value=value)), False


def _build_payload_getter(field: str):
    """
    Precompile a dot-notation field accessor for payload dicts.

    Handles:
      - top-level scalars: "current_role"
      - nested dicts: "address.city"
      - array first element: "experience[].role"  ([] → index 0)
      - array explicit index: "experience[1].role" ([N] → index N)
      - negative index: "experience[-1].role" ([-N] → last N-th)
    """
    # Parse "experience[1].role" → [("experience", 0), ("role", None)]
    # token groups: (key, bracket_content_or_None)
    _PART = re.compile(r'(\w+)(?:\[(-?\d*)\])?')
    parts = [(m.group(1), m.group(2)) for m in _PART.finditer(field)]
    # parts: [("experience", "1"), ("role", None)]

    def getter(payload: dict):
        val = payload
        for key, idx_str in parts:
            if not isinstance(val, dict):
                return None
            val = val.get(key)

            # descend into array when this token had brackets
            if idx_str is not None and isinstance(val, list):
                idx = int(idx_str) if idx_str else 0  # "" → 0 for bare []
                try:
                    val = val[idx]
                except IndexError:
                    return None

        # unwrap bare list at the leaf (field with no brackets, e.g. "skills")
        if isinstance(val, list):
            val = val[0] if val else None
        return val

    return getter


def _sort_documents_in_memory(
    documents: List[Any],
    sort_configs: List[Dict[str, str]]
) -> List[Any]:
    """
    Sort documents in memory based on sort configuration.

    Handles: top-level scalars, dot-notation nested dicts (address.city),
    array fields (experience[].role), numerics, and timestamps.
    Nulls always sort last regardless of direction.

    Args:
        documents: List of document points from Qdrant
        sort_configs: List of sort specifications [{"field": "title", "order": "asc"}, ...]

    Returns:
        Sorted list of documents
    """
    # Apply sorts in reverse order for stable multi-field sorting
    # (Python's sort is stable, so later sorts preserve earlier ordering)
    for sort_config in reversed(sort_configs):
        field = sort_config.get("field")
        order = sort_config.get("order", "asc")
        reverse = (order == "desc")

        # Precompile accessor once — O(depth) per doc instead of re-splitting each call
        getter = _build_payload_getter(field)
        is_timestamp = field in ("created_at", "updated_at")

        logger.info(f"In-memory sorting on field '{field}' ({order})")

        def sort_key(doc, _getter=getter, _reverse=reverse, _is_ts=is_timestamp, _field=field):
            payload = getattr(doc, "payload", {}) or {}
            value = _getter(payload)

            # Nulls always last — (1, ...) sorts after (0, ...)
            if value is None:
                return (1, "")

            if isinstance(value, (int, float)):
                return (0, value)

            if _is_ts and isinstance(value, str):
                try:
                    return (0, float(value))
                except (ValueError, TypeError):
                    pass

            if isinstance(value, str):
                return (0, value.lower())

            return (0, str(value).lower())

        documents.sort(key=sort_key, reverse=reverse)

    return documents


# ================================================================================================
# HELPER FUNCTIONS FOR QDRANT INDEX MANAGEMENT AND CURSOR PAGINATION
# ================================================================================================

async def _fetch_documents_with_cursor_pagination(
    indexer: Any,
    filter_condition: Filter,
    field: str,
    direction: Direction,
    limit: int,
    page_offset: Optional[str] = None
) -> Tuple[List[Any], bool, Optional[str]]:
    """
    Fetch documents using cursor-based pagination with order_by.

    Args:
        indexer: Qdrant indexer instance
        filter_condition: Filter to apply
        field: Sort field name
        direction: Sort direction (ASC/DESC)
        limit: Number of documents to fetch
        page_offset: JSON cursor from previous page

    Returns:
        Tuple of (documents, has_more, next_page_offset)
    """
    # Decode cursor from page_offset
    last_sort_value = None
    last_object_id = None
    if page_offset:
        try:
            cursor = json.loads(page_offset)
            last_sort_value = cursor.get("value")
            last_object_id = cursor.get("id")
            logging.info(f"Resume cursor: value={last_sort_value}, id={last_object_id}")
        except (json.JSONDecodeError, TypeError) as e:
            logging.warning(f"Invalid cursor: {e}")

    all_documents = []
    documents_collected = 0

    while documents_collected < limit:
        remaining = limit - documents_collected
        fetch_limit = remaining + 1  # Fetch +1 to handle boundary duplicates

        order_config = OrderBy(
            key=field,
            direction=direction,
            start_from=last_sort_value if last_sort_value is not None else None
        )

        response = await indexer.client_async.scroll(
            collection_name=indexer.collection_name,
            scroll_filter=filter_condition,
            with_payload=True,
            with_vectors=False,
            limit=fetch_limit,
            order_by=order_config
        )

        records, _ = response if isinstance(response, tuple) else (response, None)
        logging.info(f"Fetched {len(records) if records else 0} records (requested {fetch_limit})")

        if not records:
            break

        skipped_count = 0
        for point in records:
            if documents_collected >= limit:
                logging.info(f"Reached limit during processing")
                break

            payload = getattr(point, "payload", {}) or {}
            current_object_id = payload.get("object_id")

            # Skip duplicate from previous page
            if last_object_id and current_object_id == last_object_id:
                logging.info(f"Skipping duplicate object_id: {current_object_id}")
                skipped_count += 1
                last_sort_value = payload.get(field)
                last_object_id = current_object_id
                continue

            all_documents.append(point)
            documents_collected += 1
            logging.info(f"Collected {documents_collected}/{limit} docs")

            # Update cursor
            last_sort_value = payload.get(field)
            last_object_id = current_object_id

        # Break conditions
        if documents_collected >= limit:
            break
        if len(records) < fetch_limit and skipped_count == 0:
            break
        if len(records) == 1 and skipped_count == 1:
            break

    # Check if more documents exist
    has_more = False
    if documents_collected >= limit and last_sort_value is not None:
        has_more = await _check_has_more_documents(
            indexer, filter_condition, field, direction, last_sort_value, last_object_id
        )

    paginated_documents = all_documents[:limit]
    next_page_offset = json.dumps({"value": last_sort_value, "id": last_object_id}) if has_more else None

    logging.info(f"Cursor pagination: collected {documents_collected} docs, has_more={has_more}")

    return paginated_documents, has_more, next_page_offset


async def _check_has_more_documents(
    indexer: Any,
    filter_condition: Filter,
    field: str,
    direction: Direction,
    last_sort_value: Any,
    last_object_id: str
) -> bool:
    """
    Check if more documents exist beyond the current page by fetching one extra document.

    Args:
        indexer: Qdrant indexer instance
        filter_condition: Filter to apply
        field: Sort field name
        direction: Sort direction
        last_sort_value: Last sort value from current page
        last_object_id: Last object_id from current page

    Returns:
        True if more documents exist, False otherwise
    """
    try:
        check_order_config = OrderBy(
            key=field,
            direction=direction,
            start_from=last_sort_value
        )
        check_response = await indexer.client_async.scroll(
            collection_name=indexer.collection_name,
            scroll_filter=filter_condition,
            with_payload=True,
            with_vectors=False,
            limit=2,  # Fetch 2: one might be duplicate, second confirms more exist
            order_by=check_order_config
        )
        check_records, _ = check_response if isinstance(check_response, tuple) else (check_response, None)

        # Check if there's at least one document that's NOT the last one we collected
        if check_records:
            for record in check_records:
                check_payload = getattr(record, "payload", {}) or {}
                check_id = check_payload.get("object_id")
                if check_id != last_object_id:
                    return True

        logging.debug(f"has_more check: found {len(check_records) if check_records else 0} records, has_more=False")
        return False
    except Exception as check_error:
        logging.warning(f"has_more check failed: {check_error}, assuming True")
        return True  # Assume more exist if check fails


async def count_documents_with_field(knowledge_id: str, field_name: str) -> Optional[int]:
    """How many documents already carry a non-empty value for this field?

    Used to decide whether a field's ``field_type`` may still be changed. Once a
    document has been extracted under the old type, re-typing the field does NOT
    re-extract it — the stored value keeps its old shape and then fails Tier 1
    ``type_conformance`` forever, until a re-ingest nobody triggers. So a type
    change is allowed only while this returns 0.

    Returns None when the count cannot be established (no collection yet, Qdrant
    unreachable, an unexpected client error). **None is not 0** — callers must
    treat it as "unknown, so do not allow the change", because allowing one on a
    failed check is exactly the destructive case this guards.
    """
    from utils.files.retrieval_utils import _get_document_indexer
    try:
        if not knowledge_id or not field_name:
            return None

        _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
        indexer = await _get_document_indexer(embedding_model)

        # "Has a value" = present, not null, not empty. IsEmpty alone would count
        # a field explicitly stored as null, which is the extractor saying "not in
        # this document" — that is not a value anyone depends on.
        result = await indexer.client_async.count(
            collection_name=indexer.collection_name,
            count_filter=Filter(
                must_not=[
                    IsNullCondition(is_null=PayloadField(key=field_name)),
                    IsEmptyCondition(is_empty=PayloadField(key=field_name)),
                ]
            ),
            exact=True,
        )
        count = getattr(result, "count", None)
        logger.info(
            f"count_documents_with_field | knowledge={knowledge_id} "
            f"field={field_name} count={count}"
        )
        return count
    except Exception as e:
        # Deliberately not re-raised: the caller's fallback (refuse the type
        # change) is safe, and a write should not 500 because a count failed.
        logger.warning(
            f"count_documents_with_field failed | knowledge={knowledge_id} "
            f"field={field_name} | {e} — treating as unknown"
        )
        return None


def _get_field_index_type(field_name: str, field_configurations: List[Dict[str, Any]]) -> PayloadSchemaType:
    """
    Determine the appropriate Qdrant index type for a field.

    Args:
        field_name: Name of the field to index
        field_configurations: List of field configuration dicts

    Returns:
        PayloadSchemaType for the field (DATETIME, FLOAT, INTEGER, or KEYWORD)
    """
    # Check field configurations for type information
    for config in field_configurations:
        if config.get("field_name") == field_name:
            field_type = config.get("field_type", "str")
            if field_type == "date":
                # Stored as an ISO-8601 string, indexed as DATETIME so
                # DatetimeRange works. A KEYWORD index on the same string would
                # only support equality — "which contracts expire in Q2" needs
                # the range, and that is the whole reason the type exists.
                return PayloadSchemaType.DATETIME
            if field_type == "float":
                return PayloadSchemaType.FLOAT
            elif field_type == "int":
                return PayloadSchemaType.INTEGER
            elif field_type in ("str", "List[str]"):
                return PayloadSchemaType.KEYWORD

    # Timestamp fields are typically stored as floats (Unix timestamps)
    if field_name in ("created_at", "updated_at", "timestamp"):
        return PayloadSchemaType.FLOAT

    # Default to keyword for string fields
    return PayloadSchemaType.KEYWORD


async def _ensure_range_index(
    indexer: Any,
    field_name: str,
    field_type: PayloadSchemaType,
    wait_seconds: float = 2.0
) -> bool:
    """
    Create a range index for a field in Qdrant if it doesn't exist.

    Args:
        indexer: Qdrant indexer instance with client_async
        field_name: Name of the field to index
        field_type: PayloadSchemaType for the field
        wait_seconds: Seconds to wait for indexing to complete

    Returns:
        True if index was created/exists, False if creation failed
    """
    try:
        logging.info(f"Creating range index: {field_name} ({field_type})")

        # Create payload index with range support
        await indexer.client_async.create_payload_index(
            collection_name=indexer.collection_name,
            field_name=field_name,
            field_schema=field_type
        )

        # Wait for indexing to complete (async operation in Qdrant)
        await asyncio.sleep(wait_seconds)
        logging.info(f"Index ready: {field_name}")

        return True

    except Exception as index_error:
        error_msg = str(index_error).lower()

        # Ignore "already exists" errors - this is fine
        if "already exists" in error_msg or "conflict" in error_msg:
            logging.debug(f"Index exists: {field_name}")
            return True

        # Log other errors but don't fail
        logging.warning(f"Index creation failed: {field_name} - {index_error}")
        return False


async def _fetch_documents_with_offset_pagination(
    indexer: Any,
    filter_condition: Filter,
    sort_configs: List[Dict[str, str]],
    limit: int,
    page_offset: Optional[str] = None,
    sort_pool_multiplier: int = 3,
    max_pool_size: int = 10000,
) -> Tuple[List[Any], bool, Optional[str]]:
    """
    Fetch documents using offset-based pagination with full multi-field in-memory sort.

    For multi-field sorting, cursor-based pagination cannot guarantee correct global
    ordering because Qdrant only supports order_by on a single field. This function
    fetches a larger pool of documents, applies the full multi-field sort in-memory,
    and uses an offset cursor to paginate through the sorted results.

    Args:
        indexer: Qdrant indexer instance
        filter_condition: Filter to apply
        sort_configs: Full sort specification e.g. [{"field": "name", "order": "asc"}, ...]
        limit: Number of documents to return per page
        page_offset: JSON cursor with {"offset": N} from previous page
        sort_pool_multiplier: Multiplier for fetch pool size (pool = offset + limit * multiplier)
        max_pool_size: Maximum documents to fetch for sorting (memory safety cap)

    Returns:
        Tuple of (documents, has_more, next_page_offset)
    """
    # Decode offset from cursor
    offset = 0
    if page_offset:
        try:
            cursor = json.loads(page_offset)
            offset = cursor.get("offset", 0)
            logging.info(f"Offset pagination: resuming at offset={offset}")
        except (json.JSONDecodeError, TypeError) as e:
            logging.warning(f"Invalid offset cursor: {e}")

    # Calculate pool size: need enough docs to cover offset + limit after sorting
    # Use multiplier to fetch a large enough pool for accurate sorting
    pool_size = min(offset + limit * sort_pool_multiplier, max_pool_size)

    logging.info(f"Offset pagination: fetching pool of {pool_size} docs (offset={offset}, limit={limit}, multiplier={sort_pool_multiplier})")

    # Fetch documents using scroll (no order_by, just raw fetch)
    all_documents = []
    scroll_offset = None
    batch_size = 500
    iteration = 0

    while len(all_documents) < pool_size:
        iteration += 1
        remaining = pool_size - len(all_documents)
        scroll_limit = min(batch_size, remaining)

        response = await indexer.client_async.scroll(
            collection_name=indexer.collection_name,
            scroll_filter=filter_condition,
            with_payload=True,
            with_vectors=False,
            limit=scroll_limit,
            offset=scroll_offset
        )

        records, scroll_offset = response if isinstance(response, tuple) else (response, None)

        if not records:
            break

        all_documents.extend(records)

        if scroll_offset is None:
            break

        # Safety: prevent infinite loop
        if iteration > (max_pool_size // batch_size) + 5:
            logging.error(f"Offset pagination: too many iterations ({iteration}), breaking")
            break

    total_fetched = len(all_documents)
    logging.info(f"Offset pagination: fetched {total_fetched} docs in {iteration} iterations")

    # Apply full multi-field sort in-memory
    all_documents = _sort_documents_in_memory(all_documents, sort_configs)

    # Slice to requested page
    page_start = offset
    page_end = offset + limit
    paginated_documents = all_documents[page_start:page_end]

    # Determine has_more: either more docs in our pool, or we hit pool cap (more might exist)
    has_more_in_pool = page_end < total_fetched
    might_have_more = total_fetched >= pool_size and scroll_offset is not None
    has_more = has_more_in_pool or might_have_more

    # Build next cursor
    next_page_offset = None
    if has_more:
        next_page_offset = json.dumps({"offset": page_end})

    logging.info(f"Offset pagination: returning {len(paginated_documents)} docs, offset={offset}, has_more={has_more}")

    return paginated_documents, has_more, next_page_offset


def _is_missing_index_error(error: Exception) -> bool:
    """
    Check if the error is due to a missing range index for order_by.

    Args:
        error: Exception from Qdrant query

    Returns:
        True if error indicates missing range index
    """
    error_msg = str(error).lower()
    return (
        "no range index" in error_msg or
        "range index for `order_by`" in error_msg or
        ("400" in error_msg and "order_by" in error_msg)
    )
