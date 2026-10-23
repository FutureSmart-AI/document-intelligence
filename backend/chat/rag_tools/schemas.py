"""
Pydantic input schemas for RAG tools.
"""
from typing import Dict, List, Literal, Optional, Any, Union
from pydantic import BaseModel, Field, field_validator, model_validator
from chat.rag_tools.helpers import normalize_metadata_filter


# Shared reasoning fields injected into all 3 primary v2 tool schemas.
# Task-type taxonomy — picks how the result will be USED, not just how it's fetched.
_QUESTION_SHAPE_FIELD = Field(
    description=(
        "Classify what the user wants DONE with the documents (not just how to find them). "
        "'lookup' = find docs matching a topic or named entity, return representative results; "
        "'predicate' = find ALL docs satisfying a condition over structured fields, exhaustive set required; "
        "'selection' = pick the BEST N docs for a stated purpose, ranked with evidence; "
        "'aggregate' = count / group / summarize, exhaustive numbers required; "
        "'compare' = contrast two or more named entities side-by-side; "
        "'deep_read' = answer a question inside one named long document; "
        "'metadata_lookup' = discover field names or values; "
        "'fact_lookup' = retrieve a specific attribute/value (link, ID, status, date, owner…) from a named entity — escalates from doc summary → scoped chunks → global chunks until found; "
        "'multi_doc_synthesis' = answer requires reading and synthesizing content across multiple documents — semantic retrieval first, then always open chunks for every contributing doc."
    )
)
_REASONING_FIELD = Field(
    description=(
        "One sentence explaining why this tool and these arguments are the "
        "right choice for the classified query shape."
    )
)


# ---------------------------------------------------------------------------
# Legacy schemas (kept for backward compatibility / deep-read tools)
# ---------------------------------------------------------------------------

class GetMetadataFieldsInput(BaseModel):
    """Input schema for get-metadata-fields tool."""
    knowledge_id: str = Field(
        description="The knowledge base ID to query for metadata fields"
    )


class GetMetadataValuesInput(BaseModel):
    """Input schema for get-metadata-values tool."""
    knowledge_id: str = Field(
        description="The knowledge base ID to query"
    )
    field_name: str = Field(
        description="The metadata field name to get unique values for"
    )
    object_ids: Optional[List[str]] = Field(
        default=None,
        description="Optional list of document IDs to filter the values"
    )
    metadata_filter: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata filter to apply"
    )
    limit: Optional[int] = Field(
        default=None,
        description="Maximum number of unique values to return"
    )


class GetMetadataValuesBatchInput(BaseModel):
    """Input schema for get-metadata-values-batch tool."""
    knowledge_id: str = Field(
        description="The knowledge base ID to query"
    )
    field_names: List[str] = Field(
        description="List of metadata field names to get unique values for"
    )
    object_ids: Optional[List[str]] = Field(
        default=None,
        description="Optional list of document IDs to filter the values"
    )
    metadata_filter: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata filter to apply"
    )
    limit: Optional[int] = Field(
        default=None,
        description="Maximum number of unique values to return per field"
    )


MetadataValue = Union[str, int, float, bool, list, Dict[str, Any]]


class ListDocumentsInput(BaseModel):
    """Input schema for list-documents tool."""

    knowledge_id: str = Field(
        description="The knowledge base ID to query"
    )

    metadata_filter: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Flat metadata key-value filter (no logical operators). Supports nested dictionaries which will be flattened to dot-notation."
    )

    @field_validator('metadata_filter', mode='before')
    @classmethod
    def _normalize_metadata_filter(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Normalize nested dictionaries to dot-notation before validation."""
        return normalize_metadata_filter(v)

    class Config:
        extra = "forbid"


class GetDocumentMetadataInput(BaseModel):
    """Input schema for get-document-metadata tool."""
    knowledge_id: str = Field(
        description="The knowledge base ID"
    )
    object_id: str = Field(
        description="The document ID to retrieve metadata for"
    )


class GetDocumentSummariesFilteredInput(BaseModel):
    """Input schema for get-document-summaries-filtered tool."""
    knowledge_id: str = Field(
        description="The knowledge base ID to query"
    )
    object_ids: Optional[List[str]] = Field(
        default=None,
        description="Optional list of specific document IDs to get summaries for"
    )
    metadata_filter: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata filter to select documents (used if object_ids not provided)"
    )
    limit: Optional[int] = Field(
        default=None,
        description="Maximum number of document summaries to return"
    )


class GetDocumentDetailsInput(BaseModel):
    """Input schema for get-document-details tool."""
    knowledge_id: str = Field(
        description="The knowledge base ID"
    )
    object_ids: List[str] = Field(
        description="List of document IDs to retrieve details for"
    )


class SearchChunksInput(BaseModel):
    """Input schema for search-chunks tool."""
    knowledge_id: str = Field(
        description="The knowledge base ID to search in"
    )
    query: str = Field(
        description="The search query for semantic/hybrid search"
    )
    num_chunks: int = Field(
        default=3,
        description="Number of chunks to retrieve (default: 3)"
    )
    object_ids: Optional[List[str]] = Field(
        default=None,
        description="Optional list of document IDs to restrict the search to"
    )
    mode: str = Field(
        default="hybrid",
        description="Search mode: 'hybrid', 'semantic', or 'keyword' (default: 'hybrid')"
    )


class GetDocumentChunksInput(BaseModel):
    """Input schema for get-document-chunks tool."""
    knowledge_id: str = Field(
        description="The knowledge base ID"
    )
    object_id: str = Field(
        description="The document ID to retrieve chunks from"
    )


class GetChunksBatchInput(BaseModel):
    """Input schema for get-chunks-batch tool."""
    knowledge_id: str = Field(
        description="The knowledge base ID"
    )
    chunk_ids: List[str] = Field(
        description="List of chunk IDs to retrieve"
    )


class GetChunkMetadataInput(BaseModel):
    """Input schema for get-chunk-metadata tool."""
    knowledge_id: str = Field(
        description="The knowledge base ID"
    )
    chunk_id: str = Field(
        description="The chunk ID to retrieve metadata for"
    )


# ---------------------------------------------------------------------------
# v2 Consolidated schemas
# ---------------------------------------------------------------------------

class RetrieveDocumentsInput(BaseModel):
    """Input schema for the consolidated retrieve_documents tool.

    Replaces: list-documents, get-document-summaries-filtered,
              get-document-details, get-document-metadata.
    """
    question_shape: Literal["lookup", "predicate", "selection", "aggregate", "compare", "deep_read", "metadata_lookup", "fact_lookup", "multi_doc_synthesis"] = _QUESTION_SHAPE_FIELD
    reasoning: str = _REASONING_FIELD
    knowledge_id: str = Field(
        description="The knowledge base ID to query"
    )
    query: Optional[str] = Field(
        default=None,
        description=(
            "Natural-language query for semantic ranking. REQUIRED unless `object_ids` "
            "is provided — `query` and `object_ids` are mutually exclusive (search mode "
            "vs. direct-fetch mode). In search mode every retrieve_documents call ranks "
            "by semantic relevance to `query`. When `metadata_filter` is also provided "
            "(hybrid mode), the filter scopes the population to exact matches and `query` "
            "ranks those matches; when no filter applies, `query` alone drives retrieval. "
            "Derive `query` from the user's question — typically the attribute or concept "
            "you are trying to find (e.g. 'owner' when asking who owns a service, "
            "'authentication method' when asking how a service authenticates, or the "
            "user's question verbatim when no narrower attribute applies). In search mode "
            "never omit `query` — without it, documents are returned in arbitrary order "
            "and the doc most likely to contain the answer may be ranked below the limit "
            "and never returned. To fetch specific documents by ID, use `object_ids` "
            "instead (and leave `query` unset)."
        ),
    )
    metadata_filter: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "OPTIONAL flat metadata key-value filter. Add it whenever the user's "
            "query mentions a value that matches a catalog field (a tag, category, "
            "status, level, tool name, entity name, organization, etc.) — "
            "`resolve_filter_value` returns the canonical values to use. The filter "
            "scopes the population; `query` ranks within it (hybrid mode). Omit when "
            "no catalog field maps to the user's constraints — `query` alone then "
            "drives retrieval. Supports nested dictionaries which are flattened to "
            "dot-notation. Positive operators: a plain value, a list, or `$in` "
            "(`{field: {\"$in\": [...]}}`). NEGATIVE operators (to EXCLUDE values) "
            "are also supported: `{field: {\"$ne\": x}}` excludes one value, "
            "`{field: {\"$nin\": [...]}}` (or `{\"not_in\": [...]}`) excludes a list. "
            "The verbose form `{field: {\"op\": \"ne\"|\"nin\"|\"not_in\", "
            "\"value\": x}}` is equivalent. Substring, range (gt/lt/gte/lte) and "
            "logical operators ($or/$and/$nor) are NOT supported."
        ),
    )
    object_ids: Optional[List[str]] = Field(
        default=None,
        description=(
            "Specific document IDs to fetch directly (direct-fetch mode). When provided "
            "the listing / filter / ranking path is skipped and these documents are "
            "fetched as-is. Mutually exclusive with `query` — leave `query` unset when "
            "using `object_ids`."
        ),
    )
    limit: Optional[int] = Field(
        default=None,
        description=(
            "Maximum number of documents to return. When omitted, the tool "
            "picks a shape-adaptive default: fact_lookup=5, deep_read=3, "
            "compare=10, lookup=10, multi_doc_synthesis=25, metadata_lookup=5. "
            "For exhaustive shapes (predicate/selection/aggregate) the limit is "
            "overridden to fetch the complete population regardless of what you "
            "pass. Only pass `limit` explicitly when you need to override the "
            "shape default."
        ),
    )
    include_summary: bool = Field(
        default=True,
        description="Include the document summary in the response (default: True)",
    )
    include_metadata: bool = Field(
        default=True,
        description="Include full document metadata in the response (default: True)",
    )
    include_urls: bool = Field(
        default=False,
        description="Include document URLs in the response when available (default: False)",
    )

    @field_validator("metadata_filter", mode="before")
    @classmethod
    def _normalize_metadata_filter(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return normalize_metadata_filter(v)

    @model_validator(mode="after")
    def _require_a_retrieval_mode(self) -> "RetrieveDocumentsInput":
        # Three valid retrieval modes, exactly one driver per call:
        #   - `query`          → semantic ranking (optionally scoped by a filter)
        #   - `metadata_filter`→ exact filtered population, no ranking
        #   - `object_ids`     → direct fetch by ID
        # The only illegal combos are query+object_ids (search vs. direct fetch
        # are mutually exclusive) and an empty call with no driver at all.
        # This validator is the single authoritative source of these errors.
        has_query = bool(self.query and self.query.strip())
        has_filter = bool(self.metadata_filter)
        has_ids = bool(self.object_ids)
        if has_query and has_ids:
            raise ValueError(
                "Provide either `query` (semantic search) OR `object_ids` "
                "(direct fetch), not both — they are mutually exclusive modes."
            )
        if not (has_query or has_filter or has_ids):
            raise ValueError(
                "Provide a retrieval driver: `query` for semantic search "
                "(e.g. query='owner'), `metadata_filter` for an exact filtered "
                "set, or `object_ids` to fetch specific documents by ID."
            )
        return self

    class Config:
        extra = "forbid"


class RetrieveMetadataCatalogInput(BaseModel):
    """Input schema for the consolidated retrieve_metadata_catalog tool.

    Replaces: get-metadata-fields, get-metadata-values, get-metadata-values-batch.

    question_shape / reasoning have DEFAULTS here (unlike the retrieval tools):
    catalog exploration is shape-agnostic and models routinely omit them on
    this tool — a required field turns that omission into a validation-rejected
    ToolMessage and a wasted model round-trip (observed in traces).
    """
    question_shape: Literal["lookup", "predicate", "selection", "aggregate", "compare", "deep_read", "metadata_lookup", "fact_lookup", "multi_doc_synthesis"] = Field(
        default="metadata_lookup",
        description="The classified query shape driving this catalog call (defaults to 'metadata_lookup').",
    )
    reasoning: str = Field(
        default="",
        description=(
            "One sentence explaining why this tool and these arguments are the "
            "right choice for the classified query shape."
        ),
    )
    knowledge_id: str = Field(
        description="The knowledge base ID to query"
    )
    field_names: Optional[List[str]] = Field(
        default=None,
        description=(
            "Specific field names to retrieve values for. "
            "If omitted all fields are returned."
        ),
    )
    include_values: bool = Field(
        default=False,
        description=(
            "When True, fetch unique values for the requested fields. "
            "When False, return field definitions only (default: False)."
        ),
    )
    metadata_filter: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata filter to scope returned values",
    )
    object_ids: Optional[List[str]] = Field(
        default=None,
        description="Optional document IDs to scope returned values",
    )
    limit: int = Field(
        default=100,
        description="Maximum number of unique values to return per field (default: 100)",
    )


class RetrieveChunksInput(BaseModel):
    """Input schema for retrieve_chunks (renamed search-chunks)."""
    question_shape: Literal["lookup", "predicate", "selection", "aggregate", "compare", "deep_read", "metadata_lookup", "fact_lookup", "multi_doc_synthesis"] = _QUESTION_SHAPE_FIELD
    reasoning: str = _REASONING_FIELD
    knowledge_id: str = Field(
        description="The knowledge base ID to search in"
    )
    query: Optional[str] = Field(
        default=None,
        description=(
            "The search query for semantic/hybrid search. "
            "Required for general chunk search. May be OMITTED ONLY when "
            "object_ids is provided AND you want all chunks from those "
            "documents in original order (no semantic ranking)."
        ),
    )
    num_chunks: int = Field(
        default=3,
        description="Number of chunks to retrieve (default: 3)"
    )
    object_ids: Optional[List[str]] = Field(
        default=None,
        description="Optional list of document IDs to restrict the search to"
    )
    mode: str = Field(
        default="hybrid",
        description="Search mode: 'hybrid', 'semantic', or 'keyword' (default: 'hybrid')"
    )
