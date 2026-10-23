from fastapi import APIRouter, HTTPException, Path, Query, Depends, Body
from typing import Optional
from utils.files.retrieval_utils import (
    _get_chunk_indexer,
    get_document_metadata,
    get_data_view_tabular,
    get_data_view_tabular_v2,
)
from utils.db.retrieval_ops import get_hybrid_search_value
from utils.logger import logger
from utils.auth.dependencies import require_permission

from utils.pydantic_models_retrieval import (
    DataViewRequest,
    DataViewResponse,
    DataViewRequestV2,
    DataViewResponseV2,
)


router = APIRouter(dependencies=[Depends(require_permission("read"))])


# 23. Get Document Metadata (Only)
@router.get("/v1/{knowledge_id}/documents/{object_id}/metadata")
async def get_document_metadata_only(
    knowledge_id: str = Path(..., description="Knowledge ID"),
    object_id: str = Path(..., description="Document Object ID"),
):
    """
    Return document-level metadata only (no chunks/text).
    Includes: document_outline, document_type, entities, keywords, organizations,
    prepended_doc_summary, summary, temporal_context, title, topics, file_name
    """
    try:

        # Reuse existing service to fetch document metadata payload
        result = await get_document_metadata(
            object_id=object_id,
            knowledge_id=knowledge_id,
        )
        metadata = (result.metadata or {}) if hasattr(result, "metadata") else {}
        return {
            "object_id": object_id,
            "metadata": metadata,
        }
    except Exception as e:
        logger.error(f"Error retrieving document metadata for {object_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve document metadata: {e}")


# 24. Get Chunk Metadata (Only)
@router.get("/v1/{knowledge_id}/chunks/{chunk_id}/metadata")
async def get_chunk_metadata_only(
    knowledge_id: str = Path(..., description="Knowledge ID"),
    chunk_id: str = Path(..., description="Chunk ID"),
):
    """
    Return chunk-level metadata only (no text).
    Dynamically returns all chunk-level metadata fields based on field configurations.
    Excludes system fields like object_id, knowledge_id, user_id, sub_object_id, and chunk_text.
    """
    try:

        # Initialize chunk indexer to retrieve the chunk payload
        try:
            _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
            chunk_indexer = await _get_chunk_indexer(embedding_model)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to initialize chunk indexer: {e}")

        response = await chunk_indexer.client_async.retrieve(
            collection_name=chunk_indexer.collection_name,
            ids=[chunk_id],
            with_payload=True,
        )

        if not response or len(response) == 0:
            raise HTTPException(status_code=404, detail=f"Chunk {chunk_id} not found")

        point = response[0]
        payload = point.payload or {}
        
        print(payload)
        # Get field configurations for chunk-level metadata fields
        try:
            from utils.db.enrichment_db_utils import get_field_configurations_sqlmodel_v2
            field_configurations = await get_field_configurations_sqlmodel_v2(knowledge_id)
            logger.info(f"Loaded {len(field_configurations)} field configurations")
            
            # Filter for chunk-level fields that are active
            chunk_field_names = {
                config["field_name"] 
                for config in field_configurations 
                if config.get("level") == "chunk" 
                and config.get("is_active", True)
            }
            logger.info(f"Chunk field names: {chunk_field_names}")
            # Build filtered metadata from payload based on field configurations
            filtered_metadata = {}
            for field_name in chunk_field_names:
                if field_name in payload:
                    filtered_metadata[field_name] = payload[field_name]
        except Exception as e:
            logger.error(f"Error getting field configurations for knowledge {knowledge_id}: {e}")
            # Fallback: return all fields except system fields
            filtered_metadata = payload.copy()

        # Filter out system fields that shouldn't be returned in metadata
        # These are structural/system fields, not metadata
        fields_to_exclude = {
            "object_id", 
            "knowledge_id", 
            "user_id", 
            "sub_object_id",
            "chunk_text",  # The actual text content, not metadata
            "created_at", # System timestamp
            "prepended_doc_summary",
            "chunk_idx"
        }
        filtered_metadata = {
            k: v for k, v in filtered_metadata.items() 
            if k not in fields_to_exclude
        }

        return {
            "chunk_id": str(point.id),
            "object_id": payload.get("object_id"),
            "metadata": filtered_metadata,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving metadata for chunk {chunk_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve chunk metadata: {e}")


# ================================================================================================
# 25. Get All Documents Metadata (Flattened) - NEW ENDPOINT
# ================================================================================================

@router.post(
    "/v1/{knowledge_id}/documents/metadata/all",
    response_model=DataViewResponse,
    summary="Get all documents metadata in tabular format with fixed schema",
    description="""
        Key Features

        Fixed schema (null for missing values)
        Includes column metadata + total count + user selection flags
        List[dict] fields expanded → exp_0_year, exp_1_year, …
        Sorting capped at 10k docs (memory safety)
        Efficient cursor-based pagination using Qdrant's next_page_offset

        Column Selection with Availability Flag

        All available columns are ALWAYS returned in the response
        Each column has an is_user_selected flag indicating if it was explicitly requested
        When columns parameter is omitted/null: all fields have is_user_selected=true
        When columns=['field1', 'field2']: only those fields have is_user_selected=true
        UI can use is_user_selected to determine default column visibility
        Users can discover and add additional columns without re-fetching data

        Transformation Rules

        dict → flatten with _ (location.city)
        List[str] → kept as array
        List[dict] → expanded to indexed columns (controlled by max_nested_items, default=3)
                     + overflow items collected in field_remaining_* columns as lists
        Nested dicts → recursive flatten

        Pagination (Cursor-Based)

        Uses efficient cursor-based pagination via page_offset
        First request: omit page_offset or set to null
        Subsequent requests: use next_page_offset from previous response
        Example first page: {"limit": 20}
        Example next page: {"limit": 20, "page_offset": "qdrant_offset_token"}
        Response includes next_page_offset (null when no more pages) and has_more flag

        Filtering (examples)

        {"document_type": "resume"}
        {"location.city": "Bangalore"}
        {"topics": ["rag", "llm"]} (any match)
        {"experience[].year": {"gte": 2022}}
        {"year": {"gte": 2020, "lte": 2025}}
        {"degree": {"in": ["M.Tech", "MS"]}}

        Sorting (uses Qdrant native order_by for efficiency)
        [{"field": "created_at", "order": "desc"}]
        [{"field": "title", "order": "asc"}, {"field": "created_at", "order": "desc"}]
        Column Selection (optional)
        ["object_id", "title", "location.city"]
        Notes

        object_id always first
        Max page size: 100
        Only active document-level fields
        System fields excluded
        All rows = same columns
    """
)
async def get_all_documents_metadata_flattened_endpoint(
    knowledge_id: str = Path(..., description="Knowledge base ID"),
    request: DataViewRequest = Body(
        default=DataViewRequest(),
        description="Request body with pagination, columns, filters, sort options, and array expansion control",
        examples=[
            {
                "limit": 20,
                "max_nested_items": 3
            },
            {
                "limit": 50,
                "columns": ["object_id", "title", "topics", "experience"],
                "filters": {
                    "document_type": "resume",
                    "location.city": "Bangalore",
                    "topics": ["rag", "llm"],
                    "experience[].year": {"gte": "2022"}
                },
                "sort": [
                    {"field": "title", "order": "asc"},
                    {"field": "created_at", "order": "desc"}
                ],
                "max_nested_items": 5
            },
            {
                "limit": 20,
                "page_offset": "qdrant_page_token_from_previous_response",
                "max_nested_items": 3
            }
        ]
    ),
):
    """
    POST endpoint to retrieve all documents' metadata in fixed-schema tabular format.

    This endpoint is designed for:
    - Exporting document metadata in tabular/flat format with consistent schema
    - Building dashboards and data tables with document metadata
    - Filtering and searching across document metadata
    - Pagination through large document collections
    - Understanding data structure via column schema

    Returns fixed-schema tabular data with column definitions, total count, and pagination.
    """
    try:
        logger.info(
            f"Received request for data view tabular for knowledge "
            f"(limit={request.limit}, page_offset={request.page_offset}, "
            f"columns={request.columns}, "
            f"filters={request.filters}, "
            f"sort={request.sort}, "
            f"max_nested_items={request.max_nested_items})"
        )
        # Call the new data view tabular service function
        result = await get_data_view_tabular(
            knowledge_id=knowledge_id,
            limit=request.limit,
            columns=request.columns,
            filters=request.filters,
            sort=request.sort,
            max_nested_items=request.max_nested_items,
            page_offset=request.page_offset,
        )

        from utils.knowledge_health.extraction_context import get_extraction_context
        result["extraction_context"] = await get_extraction_context(
            knowledge_id, [row.get("object_id") for row in result["rows"]]
        )

        # Log successful request
        logger.info(
            f"Retrieved {len(result['rows'])} documents with {len(result['columns'])} columns "
            f"for knowledge {knowledge_id} "
            f"(limit={request.limit}, page_offset={request.page_offset}, "
            f"total={result['total_count']}, "
            f"filters={'Yes' if request.filters else 'No'}, "
            f"sort={'Yes' if request.sort else 'No'}, "
            f"max_nested_items={request.max_nested_items})"
        )

        return result

    except Exception as e:
        logger.error(f"Error retrieving data view tabular for {knowledge_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve data view tabular: {str(e)}"
        )


@router.post(
    "/v2/{knowledge_id}/documents/metadata/all",
    response_model=DataViewResponseV2,
    summary="Get all documents metadata in tabular format with page-based pagination (v2)",
    description="""
        Simplified API with page-based pagination (1, 2, 3...) for UI table views.

        **Key Features**
        - Fixed schema tabular format (null for missing values)
        - Simple page numbers instead of cursor tokens
        - Column schema includes field_path, subcolumn, parent_type for UI filter builder
        - List[dict] fields expanded → experience_0_role, experience_1_role, …
        - Overflow items collected in experience_remaining_role columns

        **Column Schema (ColumnSchemaV2)**
        - name: flattened column key used in row data (e.g. address_city)
        - original_field: top-level field — use in columns/sort/filters (e.g. address)
        - field_path: filter key in dot/bracket notation (e.g. address.city, experience[].role)
        - subcolumn: leaf key for nested fields (e.g. city, role); null for scalar/List[str]
        - parent_type: scalar | list_str | dict | list_dict
        - is_user_selected: true if field was in columns request or columns was omitted

        **Pagination**
        - 1-indexed (page=1 is first page)
        - has_more = current_page < total_pages

        **Filter Syntax** — {"field": {"op": "<op>", "value": <val>}}
        - eq: exact match — {"document_type": {"op": "eq", "value": "Resume"}}
        - ne: not equal — {"document_type": {"op": "ne", "value": "Resume"}}
        - contains: substring match — {"summary": {"op": "contains", "value": "Python"}}
        - not_contains: does not contain — {"summary": {"op": "not_contains", "value": "Java"}}
        - is_empty: null or empty field — {"phone": {"op": "is_empty"}}
        - is_not_empty: non-null, non-empty — {"phone": {"op": "is_not_empty"}}
        - gte / lte / gt / lt: range — {"education[].year": {"op": "gte", "value": "2018"}}
        - in: match any in list — {"current_role": {"op": "in", "value": ["Dev", "Analyst"]}}
        - Nested dict field: use dot notation key — {"address.city": {"op": "eq", "value": "Kochi"}}
        - Array of dicts: use bracket notation key — {"experience[].company": {"op": "eq", "value": "KeralaSoft"}}

        **Sorting**
        - Default: created_at desc
        - Single field: Qdrant native order_by (efficient)
        - Multi-field: in-memory sort, capped at 10,000 docs
    """
)
async def get_all_documents_metadata_v2_endpoint(
    knowledge_id: str = Path(..., description="Knowledge base ID"),
    request: DataViewRequestV2 = Body(
        default=DataViewRequestV2(),
        description="Request body with page number, page size, columns, filters, sort options, and array expansion control",
        openapi_examples={
            "basic_pagination": {
                "summary": "Basic pagination",
                "value": {
                    "page": 1,
                    "page_size": 5
                }
            },
            "filter_eq_ne": {
                "summary": "op: eq / ne",
                "value": {
                    "page": 1,
                    "page_size": 5,
                    "filters": {
                        "document_type": {"op": "eq", "value": "Resume"}
                    }
                }
            },
            "filter_contains": {
                "summary": "op: contains / not_contains",
                "value": {
                    "page": 1,
                    "page_size": 5,
                    "filters": {
                        "summary": {"op": "contains", "value": "Python"}
                    }
                }
            },
            "filter_is_empty": {
                "summary": "op: is_empty / is_not_empty",
                "value": {
                    "page": 1,
                    "page_size": 5,
                    "filters": {
                        "phone": {"op": "is_not_empty"}
                    }
                }
            },
            "filter_in": {
                "summary": "op: in",
                "value": {
                    "page": 1,
                    "page_size": 5,
                    "filters": {
                        "current_role": {"op": "in", "value": ["Senior Python Developer", "Data Scientist"]}
                    }
                }
            },
            "filter_range": {
                "summary": "op: gte / lte (range)",
                "value": {
                    "page": 1,
                    "page_size": 5,
                    "filters": {
                        "education[].year": {"op": "gte", "value": "2018"}
                    }
                }
            },
            "combined_filters_sort": {
                "summary": "Combined op filters + sort",
                "value": {
                    "page": 1,
                    "page_size": 10,
                    "columns": ["object_id", "current_role", "address", "skills", "experience"],
                    "filters": {
                        "document_type": {"op": "eq", "value": "Resume"},
                        "address.city": {"op": "eq", "value": "Kochi"},
                        "phone": {"op": "is_not_empty"},
                        "experience[].company": {"op": "contains", "value": "Kerala"}
                    },
                    "sort": [{"field": "current_role", "order": "asc"}],
                    "max_nested_items": 2
                }
            }
        }
    ),
):
    """
    Retrieve all documents metadata with page-based pagination (v2).

    Response columns use ColumnSchemaV2 which includes field_path, subcolumn, and parent_type
    so the UI can build filter controls directly from the schema without hardcoding field names.
    """
    try:
        logger.info(
            f"V2 API: Received request for knowledge_id={knowledge_id} "
            f"(page={request.page}, page_size={request.page_size}, "
            f"columns={len(request.columns) if request.columns else 'all'}, "
            f"filters={'yes' if request.filters else 'no'}, "
            f"sort={'yes' if request.sort else 'default'}, "
            f"max_nested_items={request.max_nested_items})"
        )

        # Call the v2 service function with page-based pagination
        result = await get_data_view_tabular_v2(
            knowledge_id=knowledge_id,
            page=request.page,
            page_size=request.page_size,
            columns=request.columns,
            filters=request.filters,
            sort=request.sort,
            max_nested_items=request.max_nested_items,
        )

        from utils.knowledge_health.extraction_context import get_extraction_context
        result["extraction_context"] = await get_extraction_context(
            knowledge_id, [row.get("object_id") for row in result["rows"]]
        )

        # Log successful response
        logger.info(
            f"V2 API: Retrieved {result['current_count']} documents with {len(result['columns'])} columns "
            f"for knowledge_id={knowledge_id} "
            f"(page={result['current_page']}/{result['total_pages']}, "
            f"total_count={result['total_count']})"
        )

        return result

    except Exception as e:
        logger.error(f"V2 API: Error retrieving data view for {knowledge_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve data view tabular (v2): {str(e)}"
        )
