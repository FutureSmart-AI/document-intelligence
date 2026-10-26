"""
Backwards-compatible re-export shim.
All definitions have moved to utils/models/. Import from there directly.
"""

from utils.models.enums import FieldType
# from utils.models.db_tables import ExtractedMetadata
from utils.models.enrichment import (
    FieldConfiguration,
    EnrichmentSyncSummary,
    EnrichmentConfigurationSyncResponse,
    EnrichmentConfigurationErrorResponse,
    FieldValidationError,
    EnrichmentConfigurationValidationErrorResponse,
    FieldConfigurationResponseV2,
    FieldConfigurationRequestV2,
    EnrichmentConfigurationRequestV2,
    EnrichmentConfigurationResponseV2,
)

__all__ = [
    "FieldType",
    "FieldConfiguration",  # "ExtractedMetadata",
    "EnrichmentSyncSummary", "EnrichmentConfigurationSyncResponse",
    "EnrichmentConfigurationErrorResponse", "FieldValidationError",
    "EnrichmentConfigurationValidationErrorResponse",
    "FieldConfigurationResponseV2", "FieldConfigurationRequestV2",
    "EnrichmentConfigurationRequestV2", "EnrichmentConfigurationResponseV2",
]
