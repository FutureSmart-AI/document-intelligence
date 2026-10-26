"""
In-process replacement for the Orchestrator's HTTP Knowledge API client
(fs-agent-chat/chat_utils/knowledge/). Same 9 function signatures the RAG
tools import, backed by direct calls into this service's own retrieval
layer instead of an aiohttp round-trip.

Tenancy: every function takes knowledge_id from the caller (which must be
the trusted path parameter on the chat endpoint, never an LLM-tool arg) and
threads it into every retrieval call. get_chunk_by_id / get_chunks_batch /
get_chunk_metadata_by_id additionally verify the fetched payload's
knowledge_id matches, because the underlying Qdrant retrieve-by-point-id
call has no knowledge_id filter of its own (retrieve is ID-scoped, not
filter-scoped) — a foreign chunk_id would otherwise leak across tenants.
"""

from typing import Any, Dict, List, Optional

from utils.logger import logger
from utils.db.retrieval_ops import (
    get_hybrid_search_value,
    get_metadata_from_qdrant_object_ids,
    get_metadata_values_for_field,
    get_user_defined_metadata_from_qdrant_object_ids,
)
from utils.db.enrichment_db_utils import (
    get_field_configurations_sqlmodel_v2,
    get_field_manifest,
)
from utils.files.retrieval_utils import (
    _get_chunk_indexer,
    get_chunks_by_ids_wrapped,
    get_document_chunks_paginated as _get_document_chunks_paginated,
    get_document_metadata as _get_document_metadata,
    get_summaries_service,
    list_documents_get_alias,
    search_documents_service,
)

CHUNK_SYSTEM_FIELDS = {
    "object_id",
    "knowledge_id",
    "user_id",
    "sub_object_id",
    "chunk_text",
    "created_at",
    "prepended_doc_summary",
    "chunk_idx",
}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def search_chunks(
    knowledge_id: str,
    query: Optional[str] = None,
    num_chunks: int = 5,
    object_ids: Optional[List[str]] = None,
    mode: Optional[str] = None,
) -> List[dict]:
    """Search chunks — mirrors routers/search.py::search_knowledge without the HTTP hop."""
    try:
        _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
        chunk_indexer = await _get_chunk_indexer(embedding_model)
    except Exception as e:
        logger.error(f"search_chunks: failed to init chunk indexer knowledge={knowledge_id}: {e}")
        return []

    try:
        if query:
            search_mode = (mode or "hybrid").lower()
            if search_mode == "hybrid":
                return await chunk_indexer.hybrid_chunk_search(
                    object_ids=object_ids,
                    query=query,
                    knowledge_id=knowledge_id,
                    limit=num_chunks,
                    # Agent-selected IDs are relevance hints. The chat API has
                    # no caller hard-scope field; public search never sets this.
                    allow_scope_expansion=True,
                )
            return await chunk_indexer.search_chunks_semantic_only(
                query=query,
                knowledge_id=knowledge_id,
                object_ids=object_ids,
                limit=num_chunks,
                allow_scope_expansion=True,
            )
        if not object_ids:
            return []
        all_chunks = await chunk_indexer.get_all_chunks_for_document(
            object_id=object_ids, knowledge_id=knowledge_id
        )
        return all_chunks[:num_chunks]
    except Exception as e:
        logger.error(f"search_chunks failed knowledge={knowledge_id}: {e}")
        return []


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

async def get_docs(knowledge_id: str, metadata_filter: Optional[Dict[str, Any]] = None) -> List[str]:
    """Document ids only — mirrors get_document_summaries_v2's metadata_filter/no-query branches."""
    if metadata_filter:
        return await list_documents_get_alias(
            knowledge_id=knowledge_id,
            filters=metadata_filter,
            limit=1000,
            include_metadata=False,
            match_all=True,
        )
    results = await list_documents_get_alias(
        knowledge_id=knowledge_id,
        filters=None,
        limit=1000,
        include_metadata=False,
    )
    return results


async def get_document_summaries(
    knowledge_id: str,
    object_ids: List[str],
    metadata_filter: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
) -> Dict[str, Dict[str, Any]]:
    """{object_id: {summary, filename, [metadata]}} — mirrors the direct-object_ids branch."""
    target_ids = object_ids
    if metadata_filter:
        filtered = dict(metadata_filter)
        if object_ids:
            filtered["object_id"] = object_ids
        target_ids = await list_documents_get_alias(
            knowledge_id=knowledge_id,
            filters=filtered,
            limit=limit or 10,
            include_metadata=False,
            match_all=True,
        )
    return await get_summaries_service(knowledge_id, target_ids, include_metadata=False)


async def get_document_metadata(knowledge_id: str, object_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Document-level metadata only — mirrors get_document_metadata_only."""
    if isinstance(object_id, (list, tuple)):
        if len(object_id) != 1:
            raise ValueError(f"Expected a single object_id, got: {object_id}")
        object_id = object_id[0]
    object_id = str(object_id).strip()

    result = await _get_document_metadata(object_id=object_id, knowledge_id=knowledge_id)
    metadata = result.metadata or {} if hasattr(result, "metadata") else {}
    return {"object_id": object_id, "metadata": metadata}


async def list_documents_with_metadata(
    knowledge_id: str,
    metadata_filter: Optional[Dict[str, Any]] = None,
    query: Optional[str] = None,
    mode: Optional[str] = None,
    include_metadata: bool = False,
    limit: int = 1000,
    whole_collection: bool = False,
) -> List[Dict[str, Any]]:
    """Mirrors get_document_summaries_v2's query / metadata_filter / direct branches.

    ``whole_collection`` lists every document of the collection when there is
    neither a query nor a filter — the population of an exhaustive question
    about the whole collection."""
    if query:
        ranked = await search_documents_service(
            knowledge_id=knowledge_id,
            query=query,
            metadata_filter=metadata_filter or None,
            limit=limit,
            mode=mode or "hybrid",
            include_metadata=include_metadata,
        )
        return [
            {
                "object_id": r["object_id"],
                "filename": r.get("filename"),
                "score": r.get("score"),
                **({"metadata": r.get("metadata", {})} if include_metadata else {}),
            }
            for r in ranked
        ]

    if metadata_filter:
        # match_all: every condition must hold (AND), built by the same
        # condition builder as the query branch above. The listing's legacy
        # default is loose OR, which turned {"education_level": "masters",
        # "languages": "hindi"} into 17 docs instead of 4 and made every range
        # match nothing — and exhaustive shapes (count / predicate / selection)
        # always take this branch, because their query is stripped.
        results = await list_documents_get_alias(
            knowledge_id=knowledge_id,
            filters=metadata_filter,
            limit=limit,
            include_metadata=include_metadata,
            match_all=True,
        )
        if include_metadata and results and isinstance(results[0], dict):
            return results
        return [{"object_id": oid} for oid in results]

    if whole_collection:
        results = await list_documents_get_alias(
            knowledge_id=knowledge_id,
            filters=None,
            limit=limit,
            include_metadata=include_metadata,
        )
        if include_metadata and results and isinstance(results[0], dict):
            return results
        return [{"object_id": oid} for oid in results]

    return []


async def get_document_details(knowledge_id: str, object_ids: List[str]) -> List[Dict[str, Any]]:
    """Summary + metadata per document — mirrors the include_metadata=True direct-ids branch."""
    summaries = await get_summaries_service(knowledge_id, object_ids, include_metadata=True)
    documents: List[Dict[str, Any]] = []
    for object_id, details in summaries.items():
        document: Dict[str, Any] = {"object_id": object_id}
        for key, value in details.items():
            if key == "metadata":
                continue
            document[key] = value
        if "metadata" in details:
            document["metadata"] = details["metadata"]
        documents.append(document)
    return documents


def log_duplicate_object_ids(object_ids: List[str], context: str) -> None:
    """Diagnostic only — logs how many document ids were retrieved and flags repeats."""
    from collections import Counter

    if not object_ids:
        logger.info(f"{context}: document id count=0 (empty list)")
        return
    counts = Counter(object_ids)
    if len(counts) == len(object_ids):
        logger.info(f"{context}: retrieved document count={len(object_ids)} (all unique object_ids)")
        return
    repeated = {oid: c for oid, c in counts.items() if c > 1}
    logger.warning(
        f"{context}: duplicate object_ids — total_entries={len(object_ids)} "
        f"unique_ids={len(counts)} repeated_id_counts={repeated}"
    )


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------

async def get_document_chunks_paginated(
    knowledge_id: str,
    object_id: str,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Fixed limit=20/offset=0/metadata=True — matches the Orchestrator client's hardcoded params."""
    result = await _get_document_chunks_paginated(
        object_id=object_id,
        knowledge_id=knowledge_id,
        limit=20,
        offset=0,
        include_metadata=True,
    )
    if result is None:
        return {"object_id": object_id, "chunks": [], "total_chunks": 0, "limit": 20, "offset": 0, "has_more": False}
    return result


async def get_chunks_batch(
    knowledge_id: str,
    chunk_ids: List[str],
    user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Batch chunk fetch by id — tenant-guarded (retrieve-by-id has no knowledge_id filter)."""
    chunks = await get_chunks_by_ids_wrapped(chunk_ids, knowledge_id)
    return [c for c in chunks if (c.get("metadata") or {}).get("knowledge_id") == knowledge_id]


async def get_chunk_by_id(knowledge_id: str, chunk_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Single chunk fetch by id — tenant-guarded, mirrors routers/chunk_management.py::get_chunk_by_id."""
    try:
        _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
        chunk_indexer = await _get_chunk_indexer(embedding_model)
    except Exception as e:
        logger.error(f"get_chunk_by_id: failed to init chunk indexer knowledge={knowledge_id}: {e}")
        return {}

    response = await chunk_indexer.client_async.retrieve(
        collection_name=chunk_indexer.collection_name,
        ids=[chunk_id],
        with_payload=True,
    )
    if not response:
        return {}

    point = response[0]
    payload = point.payload or {}
    if payload.get("knowledge_id") != knowledge_id:
        logger.warning(f"get_chunk_by_id: chunk {chunk_id} does not belong to knowledge_id={knowledge_id}")
        return {}

    chunk_text = payload.get("chunk_text") or payload.get("text")
    return {
        "id": str(point.id),
        "object_id": payload.get("object_id"),
        "text": chunk_text,
        "metadata": payload,
    }


async def get_chunk_metadata_by_id(knowledge_id: str, chunk_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Chunk-level metadata only — tenant-guarded, mirrors metadata_extraction_api.py::get_chunk_metadata_only."""
    try:
        _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
        chunk_indexer = await _get_chunk_indexer(embedding_model)
    except Exception as e:
        logger.error(f"get_chunk_metadata_by_id: failed to init chunk indexer knowledge={knowledge_id}: {e}")
        return {}

    response = await chunk_indexer.client_async.retrieve(
        collection_name=chunk_indexer.collection_name,
        ids=[chunk_id],
        with_payload=True,
    )
    if not response:
        return {}

    point = response[0]
    payload = point.payload or {}
    if payload.get("knowledge_id") != knowledge_id:
        logger.warning(f"get_chunk_metadata_by_id: chunk {chunk_id} does not belong to knowledge_id={knowledge_id}")
        return {}

    try:
        field_configurations = await get_field_configurations_sqlmodel_v2(knowledge_id)
        chunk_field_names = {
            cfg["field_name"]
            for cfg in field_configurations
            if cfg.get("level") == "chunk" and cfg.get("is_active", True)
        }
        filtered_metadata = {k: payload[k] for k in chunk_field_names if k in payload}
    except Exception as e:
        logger.error(f"get_chunk_metadata_by_id: failed to load field configs knowledge={knowledge_id}: {e}")
        filtered_metadata = dict(payload)

    filtered_metadata = {k: v for k, v in filtered_metadata.items() if k not in CHUNK_SYSTEM_FIELDS}

    return {
        "chunk_id": str(point.id),
        "object_id": payload.get("object_id"),
        "metadata": filtered_metadata,
    }


# ---------------------------------------------------------------------------
# Metadata / enrichment
# ---------------------------------------------------------------------------

async def get_metadata_fields(knowledge_id: str, include_values: bool = False) -> Dict[str, Any]:
    """
    Field definitions (+ optional values) — mirrors routers/enrichment_configuration_new.py::get_enrichment
    with no field_name, folding fields[] and user_defined_metadata[] into one list like the
    Orchestrator client did, and dropping chunk-level fields (document-chat scope only).
    """
    configurations = await get_field_configurations_sqlmodel_v2(knowledge_id)

    field_to_values: Dict[str, List[Any]] = {}
    if include_values:
        metadata_response = await get_metadata_from_qdrant_object_ids(knowledge_id, limit=10, filters=None)
        for f in getattr(metadata_response, "fields", []) or []:
            if f.field_name:
                field_to_values[f.field_name] = f.values or []

    fields: List[Dict[str, Any]] = []
    for cfg in configurations:
        if cfg.get("level") == "chunk":
            continue
        entry = {
            "field_name": cfg.get("field_name"),
            "description": cfg.get("description"),
            "field_type": cfg.get("field_type"),
            "level": cfg.get("level") or "document",
            "is_custom": cfg.get("is_custom", False),
            "is_required": cfg.get("is_required", False),
            "is_active": cfg.get("is_active", True),
            "sample_values": cfg.get("sample_values"),
            "restrict_to_choices": cfg.get("restrict_to_choices", False),
            "max_items": cfg.get("max_items"),
            "schema": cfg.get("schema"),
        }
        if include_values:
            entry["values"] = field_to_values.get(cfg.get("field_name"), [])
        fields.append(entry)

    try:
        manifest = await get_field_manifest(knowledge_id, active_only=True)
    except Exception as e:
        logger.warning(f"get_metadata_fields: field manifest unavailable knowledge={knowledge_id}: {e}")
        manifest = []

    if manifest:
        manifest_names = [m["field_name"] for m in manifest]
        manifest_types = {m["field_name"]: m["field_type"] for m in manifest}
        configured_names = {cfg.get("field_name") for cfg in configurations}
        user_defined_response = await get_user_defined_metadata_from_qdrant_object_ids(
            knowledge_id=knowledge_id,
            limit=10,
            filters=None,
            field_names=manifest_names,
            field_types=manifest_types,
        )
        for f in getattr(user_defined_response, "fields", []) or []:
            if f.field_name in configured_names:
                continue
            fields.append({
                "field_name": f.field_name,
                "description": f.description,
                "field_type": f.field_type,
                "level": "document",
                "is_custom": f.is_custom,
                "is_required": f.is_required,
                "is_active": f.is_active,
                "sample_values": f.sample_values,
                "restrict_to_choices": f.restrict_to_choices,
                "max_items": f.max_items,
                "schema": f.field_schema,
                "values": f.values if include_values else None,
            })

    logger.info(f"get_metadata_fields: returning {len(fields)} fields knowledge={knowledge_id}")
    return {"fields": fields}


async def get_metadata_values(
    knowledge_id: str,
    field_name: str,
    object_ids: Optional[List[str]] = None,
    include_metadata: bool = True,
    metadata_filter: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Unique values for one field — mirrors get_enrichment's field_name branch
    (configured field -> user-defined fallback). is_complete is synthesized
    (len(values) < limit) since the underlying service has no sample/exhaustive
    distinction of its own.
    """
    query_limit = limit or 100
    filters = dict(metadata_filter or {})
    if object_ids:
        filters["object_id"] = object_ids
    filters = filters or None

    try:
        metadata_field = await get_metadata_values_for_field(
            knowledge_id=knowledge_id,
            field_name=field_name,
            filters=filters,
            limit=query_limit,
        )
        values = metadata_field.values or []
        field_type = metadata_field.field_type
    except ValueError:
        metadata_response = await get_user_defined_metadata_from_qdrant_object_ids(
            knowledge_id=knowledge_id,
            limit=query_limit,
            filters=filters,
            field_name=field_name,
        )
        matched = next((f for f in (metadata_response.fields or []) if f.field_name == field_name), None)
        if not matched:
            logger.warning(f"get_metadata_values: field '{field_name}' not found knowledge={knowledge_id}")
            return {"values": [], "field_type": None, "is_complete": True}
        values = matched.values or []
        field_type = matched.field_type

    is_complete = len(values) < query_limit
    return {"values": values, "field_type": field_type, "is_complete": is_complete}
