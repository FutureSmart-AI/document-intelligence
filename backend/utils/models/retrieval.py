"""Models for search, chunk retrieval, and data view endpoints."""

from typing import List, Literal, Optional, Union, Dict
from pydantic import BaseModel, Field, model_validator


class ChunkSearchRequest(BaseModel):
    knowledge_id: str
    user_id: Optional[str] = None
    query: Optional[str] = None

    @model_validator(mode='before')
    def strip_user_id_from_payload(cls, values):
        values.pop("user_id", None)
        return values

    num_chunks: int = Field(3, ge=1, le=100)
    object_ids: Optional[Union[str, List[str]]] = None
    mode: Optional[str] = Field(
        "hybrid",
        description="Search mode: 'hybrid' (default) or 'non_hybrid'"
    )


class ChunkSearchResponse(BaseModel):
    chunks: List[dict] = []


class DocumentListResponse(BaseModel):
    object_ids: List[str]


class DocumentResponse(BaseModel):
    object_id: str
    content: Optional[str] = None
    summary: Optional[str] = None
    metadata: Optional[dict] = None


class DocumentMetadataResponse(BaseModel):
    object_id: str
    metadata: Optional[dict] = None


class ChunkSingleResponse(BaseModel):
    id: str
    object_id: Optional[str] = None
    text: Optional[str] = None
    relevance_score: Optional[float] = None
    metadata: Optional[Dict] = None


class ChunkBulkFetchRequest(BaseModel):
    chunk_ids: Union[str, List[str]]
    knowledge_id: str


class ChunkBulkFetchResponse(BaseModel):
    chunks: List[ChunkSingleResponse]


class DocumentSummariesRequest(BaseModel):
    knowledge_id: str
    object_ids: List[str]


class SummaryItem(BaseModel):
    summary: Optional[str] = None
    filename: str
    score: Optional[float] = None
    metadata: Optional[Dict] = None


class DocumentSummariesResponse(BaseModel):
    summaries: Dict[str, SummaryItem]


class ChunkMetadata(BaseModel):
    chunk_keywords: Optional[List[str]] = Field(None, description="List of significant keywords specific to this chunk's content")
    chunk_topic: Optional[str] = Field(None, description="The primary subject or theme addressed in this specific chunk")
    chunk_entities: Optional[List[str]] = Field(None, description="Important named entities (people, products, etc.) mentioned in this chunk")


class DocumentChunkResponse(BaseModel):
    id: str
    text: str
    metadata: Optional[ChunkMetadata] = None


class DocumentChunksResponse(BaseModel):
    object_id: str
    chunks: List[DocumentChunkResponse]
    total_chunks: int
    limit: int
    offset: int
    has_more: bool


# ================================================================================================
# MODELS FOR BULK DOCUMENTS METADATA ENDPOINT (FLATTENED)
# ================================================================================================

class AllDocumentsMetadataRequest(BaseModel):
    """
    Request model for POST /v1/{knowledge_id}/documents/metadata/all endpoint.

    Supports filtering, sorting, and pagination over flattened document metadata.
    """
    limit: int = Field(default=20, ge=1, le=100, description="Maximum number of documents to return (1-100)")
    offset: int = Field(default=0, ge=0, description="Number of documents to skip for pagination")
    filters: Optional[Dict[str, Union[str, int, float, List, Dict]]] = Field(
        default=None,
        description=(
            "Optional filter conditions. Supports: "
            "scalar fields {'document_type': 'resume'}, "
            "nested fields {'location.city': 'Bangalore'}, "
            "list matching {'topics': ['AI', 'ML']}, "
            "array of dicts {'experience[].year': {'gte': '2022'}}, "
            "range operators {'year': {'gte': 2020, 'lte': 2025}}, "
            "in operator {'degree': {'in': ['M.Tech', 'MS']}}"
        ),
        examples=[{
            "document_type": "resume",
            "location.city": "Bangalore",
            "topics": ["rag", "llm"],
            "experience[].year": {"gte": "2022"}
        }]
    )
    sort: Optional[List[Dict[str, str]]] = Field(
        default=None,
        description="Optional sort configuration. List of field/order pairs. Order must be 'asc' or 'desc'.",
        examples=[[
            {"field": "title", "order": "asc"},
            {"field": "created_at", "order": "desc"}
        ]]
    )


class AllDocumentsMetadataResponse(BaseModel):
    """
    Response model for POST /v1/{knowledge_id}/documents/metadata/all endpoint.

    Flattening rules:
    - dict fields: underscore-joined (location: {city: "NYC"} → location_city: "NYC")
    - List[str] fields: kept as arrays unchanged
    - List[dict] fields: indexed (exp: [{year: 2020}] → exp_0_year: 2020)
    - Nested dicts: recursively flattened
    """
    documents: List[Dict[str, Union[str, int, float, List, None]]] = Field(
        description="List of documents with flattened metadata. Each document includes 'object_id' at root level.",
        examples=[[
            {
                "object_id": "doc_abc123",
                "title": "Machine Learning Guide",
                "topics": ["AI", "ML", "Deep Learning"],
                "location_city": "Bangalore",
                "experience_0_year": "2020",
                "experience_0_company": "Google",
            }
        ]]
    )
    limit: int = Field(description="Number of documents requested (same as request limit)")
    offset: int = Field(description="Number of documents skipped (same as request offset)")
    has_more: bool = Field(description="True if more documents are available beyond this page")


# ================================================================================================
# MODELS FOR DATA VIEW TABULAR ENDPOINT
# ================================================================================================

class ColumnSchema(BaseModel):
    """Schema definition for a single column in the data view."""
    name: str = Field(description="Column name as it appears in the row data")
    original_field: str = Field(description="Original field name from metadata before flattening")
    data_type: str = Field(description="Data type using Python type notation (str, int, float, bool, dict, List[str], List[dict])")
    is_user_selected: bool = Field(
        default=True,
        description=(
            "True if user selected this column or if no columns filter was specified. "
            "False if user specified columns but this column was not included."
        )
    )


class ColumnSchemaV2(BaseModel):
    """Schema definition for a single column in the V2 data view (includes filter path metadata)."""
    name: str = Field(description="Flattened column key used in row data (e.g. location_country)")
    original_field: str = Field(description="Top-level field name — use in filters/sort/columns requests (e.g. location)")
    field_path: str = Field(description="Dot-notation path to use as the filter key (e.g. location.country, experience[].year)")
    subcolumn: Optional[str] = Field(default=None, description="Leaf key within nested field (e.g. country, year); null for scalar and List[str]")
    parent_type: str = Field(description="Parent field type: scalar, list_str, dict, or list_dict")
    data_type: str = Field(description="Data type using Python type notation (str, int, float, bool, dict, List[str], List[dict])")
    sort_field: Optional[str] = Field(
        default=None,
        description=(
            "Payload key to use as sort field. "
            "scalar → field name, dict → dot path (e.g. address.city), "
            "list_dict with numeric index → bracket path (e.g. experience[1].company). "
            "Null for list_str and list_dict _remaining_ columns (not sortable)."
        )
    )
    is_user_selected: bool = Field(
        default=True,
        description=(
            "True if user selected this column or if no columns filter was specified. "
            "False if user specified columns but this column was not included."
        )
    )


class DataViewRequest(BaseModel):
    """Request model for data view tabular endpoint with cursor-based pagination."""
    limit: int = Field(default=20, ge=1, le=100, description="Maximum number of documents to return (1-100)")
    page_offset: Optional[str] = Field(
        default=None,
        description="Qdrant page offset token for efficient pagination. Use next_page_offset from previous response. Omit for the first page."
    )
    columns: Optional[List[str]] = Field(
        default=None,
        description="Optional list of top-level field names to include. If not provided, all active fields are included.",
        examples=[["object_id", "title", "location", "topics"]]
    )
    filters: Optional[Dict[str, Union[str, int, float, List, Dict]]] = Field(
        default=None,
        description=(
            "Optional filter conditions. Supports scalar, nested, list, array-of-dicts, range, and in operators."
        ),
        examples=[{
            "document_type": "resume",
            "location.city": "Bangalore",
            "topics": ["rag", "llm"],
            "experience[].year": {"gte": "2022"}
        }]
    )
    sort: Optional[List[Dict[str, str]]] = Field(
        default=[{"field": "created_at", "order": "desc"}],
        description="Optional sort configuration. List of field/order pairs. Defaults to created_at descending.",
        examples=[[
            {"field": "title", "order": "asc"},
            {"field": "created_at", "order": "desc"}
        ]]
    )
    max_nested_items: int = Field(
        default=2,
        ge=0,
        le=5,
        description=(
            "Maximum number of items to expand for List[dict] fields into field_0_*, field_1_* columns. "
            "Overflow collected into field_remaining_* columns. Set to 0 to disable."
        )
    )


class DocumentExtractionContext(BaseModel):
    """Field-name evidence from the latest successful attempt, never a schema version."""
    status: Literal["recorded", "unknown"] = "unknown"
    event_key: Optional[str] = None
    processed_at: Optional[str] = None
    processed_fields: Optional[List[str]] = None
    definition_history: Literal["unknown"] = "unknown"


class DataViewResponse(BaseModel):
    """Response model for data view tabular endpoint with fixed schema."""
    columns: List[ColumnSchema] = Field(description="Fixed schema definition for all columns in the data view.")
    rows: List[Dict[str, Union[str, int, float, bool, List, None]]] = Field(
        description="Data rows conforming to the column schema. Every row contains all columns, null for missing values."
    )
    total_count: int = Field(description="Total number of documents matching the filter criteria (before pagination)")
    current_count: int = Field(description="Number of documents returned in the current response")
    next_page_offset: Optional[str] = Field(
        default=None,
        description="Qdrant page offset token for fetching the next page. None if no more pages available."
    )
    has_more: bool = Field(description="True if more documents are available beyond this page")
    extraction_context: Dict[str, DocumentExtractionContext] = Field(
        default_factory=dict, description="Per returned object ID, latest successful field-name context. Unknown is not an attempted-empty result; definitions/types are not recorded."
    )


# ================================================================================================
# MODELS FOR DATA VIEW TABULAR ENDPOINT V2 (PAGE-BASED PAGINATION)
# ================================================================================================

class DataViewRequestV2(BaseModel):
    """Request model for data view tabular endpoint with page-based pagination (v2 API)."""
    page: int = Field(default=1, ge=1, description="1-indexed page number (first page is 1)")
    page_size: int = Field(default=20, ge=1, le=100, description="Items per page (1-100)")
    columns: Optional[List[str]] = Field(
        default=None,
        description="Optional list of top-level field names to include. If not provided, all active fields are included.",
        examples=[["object_id", "title", "location", "topics"]]
    )
    filters: Optional[Dict[str, Union[str, int, float, List, Dict]]] = Field(
        default=None,
        description="Optional filter conditions for document metadata.",
        examples=[{
            "document_type": "resume",
            "location.city": "Bangalore",
            "topics": ["rag", "llm"],
            "experience[].year": {"gte": "2022"}
        }]
    )
    sort: Optional[List[Dict[str, str]]] = Field(
        default=[{"field": "created_at", "order": "desc"}],
        description="Optional sort configuration. Defaults to created_at descending.",
        examples=[[
            {"field": "title", "order": "asc"},
            {"field": "created_at", "order": "desc"}
        ]]
    )
    max_nested_items: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Maximum number of items to expand into individual columns for List[dict] fields. Set to 0 to disable."
    )


class DataViewResponseV2(BaseModel):
    """Response model for data view tabular endpoint with page-based pagination (v2 API)."""
    columns: List[ColumnSchemaV2] = Field(description="Schema definitions for each column in the data view")
    rows: List[Dict[str, Union[str, int, float, bool, List, None]]] = Field(
        description="Data rows conforming to the column schema. Every row contains all columns, null for missing values."
    )
    total_count: int = Field(description="Total number of documents matching the filter criteria (before pagination)")
    current_count: int = Field(description="Number of documents returned in the current page")
    current_page: int = Field(description="Current page number (1-indexed)")
    total_pages: int = Field(description="Total number of pages available based on page_size and total_count")
    extraction_context: Dict[str, DocumentExtractionContext] = Field(
        default_factory=dict, description="Per returned object ID, latest successful field-name context. Unknown is not an attempted-empty result; definitions/types are not recorded."
    )
