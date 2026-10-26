"""Models for enrichment field configuration endpoints."""

import pydantic
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from typing import List, Optional, Dict, Any, Union, Literal
from datetime import datetime
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON

from utils.logger import logger
from utils.models.enums import (
    FieldType,
    ALL_FIELD_TYPES,
    CALLER_FIELD_TYPES,
    label_for_field_type,
)
from utils.models.knowledge import MetadataField


class FieldConfiguration(SQLModel, table=True):
    __tablename__ = "field_configurations"
    __table_args__ = {'extend_existing': True}
    id: Optional[int] = Field(default=None, primary_key=True)
    knowledge_id: str = Field(index=True)
    field_name: str
    description: Optional[str] = None
    field_type: str
    # The human-facing label for field_type ("Table", "Date", "Number"), stored
    # rather than derived (migration 052). NULL on every row predating that
    # migration; the response layer falls back to FIELD_TYPE_LABELS for those.
    # See utils/models/enums.py for why this cannot be a pure lookup.
    field_type_label: Optional[str] = Field(default=None, max_length=64)
    is_active: bool = Field(default=True)
    is_custom: bool = Field(default=False)
    is_required: bool = Field(default=False)
    sample_values: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    restrict_to_choices: bool = Field(default=False)
    max_items: Optional[int] = None
    field_schema: Optional[Dict[str, str]] = Field(default=None, sa_column=Column("schema", JSON))
    # v2 fields
    level: Optional[str] = Field(default="document")
    deprecated_at: Optional[datetime] = None
    deleted: bool = Field(default=False, index=True)
    deleted_at: Optional[datetime] = None
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)


class EnrichmentSyncSummary(BaseModel):
    """Structured summary of sync operations."""
    created: int
    updated: int
    deleted: int
    warnings: List[str] = []


class EnrichmentConfigurationSyncResponse(BaseModel):
    """Response model for PUT enrichment configuration sync."""
    status: str = "success"
    message: str
    summary: EnrichmentSyncSummary = pydantic.Field(..., description="Summary of operations performed")


class EnrichmentConfigurationErrorResponse(BaseModel):
    """Error response model for enrichment configuration."""
    status: str = "error"
    message: str
    details: Optional[List[Dict[str, str]]] = None


class FieldValidationError(BaseModel):
    """Model for individual field validation errors."""
    field_name: str
    error: str


class EnrichmentConfigurationValidationErrorResponse(BaseModel):
    """Validation error response model."""
    status: str = "error"
    message: str = "Validation failed on one or more fields. No changes were applied."
    details: List[FieldValidationError]


class FieldConfigurationResponseV2(BaseModel):
    """V2 response model for a single field configuration with metadata and v2 fields."""
    model_config = ConfigDict(populate_by_name=True)

    id: int
    knowledge_id: str
    field_name: str
    description: Optional[str] = None
    field_type: str
    # What the UI shows. Populated from the stored column when present, else
    # derived from FIELD_TYPE_LABELS — see _fill_field_type_label below.
    field_type_label: Optional[str] = None
    level: Literal['document', 'chunk'] = 'document'
    is_custom: bool
    is_required: bool = False
    is_active: bool = True
    sample_values: Optional[List[str]] = None
    restrict_to_choices: bool = False
    max_items: Optional[int] = None
    field_schema: Optional[Dict[str, str]] = pydantic.Field(None, alias="schema")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    deprecated_at: Optional[datetime] = None
    metadata: List[Any] = []

    @field_validator('field_type')
    @classmethod
    def v2_normalize_field_type(cls, v: str) -> str:
        """Normalize a stored field_type for the response.

        This runs on the RESPONSE path (rows read out of field_configurations),
        so it must never reject: a row that is already in the DB cannot be
        un-stored by raising here, and doing so would 500 a read of an otherwise
        healthy collection. Callers are rejected on the write path instead, by
        validate_field_configuration_sqlmodel_v2.

        What it must NOT do is silently pass an unrecognised value off as 'str' —
        that is what made a mis-stored 'List[Dict]' render as a text field with
        nothing to show it had ever been a table. An unexpected value is now
        surfaced as-is and logged loudly, so bad stored data is visible rather
        than laundered.
        """
        if not isinstance(v, str):
            return v

        if v.startswith('FieldType.'):
            v = v.split('.')[-1]
        if v.lower() in ('list[str]', 'list_str'):
            return 'List[str]'
        if v.lower() in ('list[dict]', 'list_dict'):
            return 'List[dict]'
        try:
            return FieldType[v.upper()].value
        except KeyError:
            pass
        if v in ALL_FIELD_TYPES:
            # Includes the internal 'chunks' value, which is a legitimate
            # stored type the worker writes and must round-trip untouched.
            return v

        # Unknown stored value. Surface it — a caller seeing 'List[Dict]' in a
        # response can fix the row; a caller seeing 'str' cannot.
        logger.warning(
            "Unrecognised stored field_type %r on a field_configurations row — "
            "returning it as-is. Expected one of %s. This row will not extract as "
            "its intended type; the worker's type_map has no entry for it.",
            v, sorted(ALL_FIELD_TYPES),
        )
        return v

    @model_validator(mode='after')
    def _fill_field_type_label(self):
        """Fall back to the default label only when none is stored.

        The stored label always wins: it is what this field was created with, or
        what a user reworded it to, and a map lookup must never overwrite that.
        Deriving happens only for rows predating migration 052, where the column
        is NULL — that is the whole reason this is a fallback rather than a
        computed property.
        """
        if not self.field_type_label:
            self.field_type_label = label_for_field_type(self.field_type)
        return self


class FieldConfigurationRequestV2(BaseModel):
    """V2 request model for creating or updating a field configuration."""
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[int] = None
    field_name: str
    description: Optional[str] = None
    field_type: Optional[Union[FieldType, str]] = None
    # The label the user sees and may reword. Sending a label that differs from
    # the stored one is what tells the API "the user changed this" — the LLM then
    # re-derives field_type from it, and the label is saved as the user wrote it,
    # never overwritten with the canonical one.
    field_type_label: Optional[str] = None
    level: Literal['document', 'chunk'] = 'document'
    is_custom: bool
    is_required: bool = False
    is_active: bool = True
    sample_values: Optional[List[str]] = None
    restrict_to_choices: Optional[bool] = False
    max_items: Optional[int] = None
    field_schema: Optional[Dict[str, str]] = pydantic.Field(None, alias="schema")

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        data = super().model_dump(**kwargs)
        if 'field_type' in data and data['field_type'] is not None:
            ft = data['field_type']
            if hasattr(ft, 'value'):
                data['field_type'] = ft.value
            elif isinstance(ft, str) and ft.startswith('FieldType.'):
                data['field_type'] = ft.split('.')[-1].lower()
                enum_mapping = {
                    'str': 'str',
                    'list_str': 'List[str]',
                    'int': 'int',
                    'float': 'float',
                    'bool': 'bool',
                    'dict': 'dict',
                    'list_dict': 'List[dict]'
                }
                data['field_type'] = enum_mapping.get(data['field_type'], data['field_type'])
        # sync functions expect key 'schema', not 'field_schema' (the Python attr name)
        if 'field_schema' in data:
            data['schema'] = data.pop('field_schema')
        return data


class EnrichmentConfigurationRequestV2(BaseModel):
    fields: List[FieldConfigurationRequestV2]


class EnrichmentConfigurationResponseV2(BaseModel):
    fields: List[FieldConfigurationResponseV2]
    user_defined_metadata: Optional[List[MetadataField]] = None
