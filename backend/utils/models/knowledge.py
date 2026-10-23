"""Models for knowledge service generation and metadata retrieval."""

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Optional, List, Dict, Any


class GenerateKnowledgeRequest(BaseModel):
    owner_id: str
    collection_name: Optional[str] = Field(default=None, description="Short display name for the collection (G1). Optional — a collection created without one has no name until set later via PUT /config.")


class GenerateKnowledgeResponse(BaseModel):
    status: str
    knowledge_id: str
    message: str


class MetadataField(BaseModel):
    """Represents a single metadata field with its configuration and values."""
    model_config = ConfigDict(populate_by_name=True)

    field_name: str = Field(..., description="Name of the metadata field")
    field_type: str = Field(..., description="Type of the metadata field")
    description: Optional[str] = Field(None, description="Field description")
    is_custom: bool = Field(..., description="Whether this is a custom field")
    is_active: bool = Field(..., description="Whether this field is active")
    is_required: bool = Field(..., description="Whether this field is required")
    sample_values: Optional[Any] = Field(None, description="Sample values for this field")
    restrict_to_choices: bool = Field(..., description="Whether values are restricted to choices")
    max_items: Optional[int] = Field(None, description="Maximum number of items allowed")
    field_schema: Optional[Dict[str, str]] = Field(None, alias="schema", description="Schema defining child field names and their types. Optional for all field types.")
    values: List[Any] = Field(default_factory=list, description="Unique values extracted for this field")
    value_counts: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=(
            "[{value, count}], most-common-first, from Qdrant's facet API — exhaustive "
            "across the collection, not a sample. Only populated for facetable field "
            "types (str/int/float/bool/List[str], plus nested fields). Null for dict / "
            "List[dict] fields, which stay on a sampled scroll with no reliable count "
            "to report — absent rather than a count computed from a sample."
        ),
    )


class MetadataResponse(BaseModel):
    """Response model for metadata retrieval."""
    fields: List[MetadataField] = Field(default_factory=list, description="List of metadata fields and their values")

    def to_dict(self) -> Dict[str, List[Any]]:
        return {field.field_name: field.values for field in self.fields}
