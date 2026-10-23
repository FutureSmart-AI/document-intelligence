"""Schemas for the usage analytics query endpoint, ported from
fs-agent-usage-limits' services/pydantic_models_analytics.py.

Terminology rewrite: `owner_id` (required top-level context in the source
service) is replaced by `knowledge_id`, which the router takes from the URL
path rather than the request body — matching every other endpoint in this
service. Batch query mode (multiple queries in one call) was dropped in this
port to keep the surface area proportional to what fs-agent-knowledge actually
needs; single-query mode covers the full filter/group_by/time_bucket/cost
feature set. See docs/plan/usage-and-limits/state.md for this gap.
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, List, Any, Union, Literal
from datetime import datetime
from enum import Enum

from utils.usage.cost_calculator import get_extraction_method_config


class TimeBucket(str, Enum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class KeyFilterType(str, Enum):
    OWNER_KEY = "owner-key"
    PLATFORM_KEY = "platform-key"


ALLOWED_METADATA_FIELDS = [
    "model", "model_name", "provider", "temperature", "file_extension",
    "extraction_method", "enrichment_strategy", "scrape_method",
    "crawl_depth", "db_host", "chunks_only", "audio_seconds",
]

ALLOWED_METRIC_GROUP_FIELDS = ["input_tokens", "output_tokens", "total_tokens"]
# knowledge_id is a real column on UsageEvent and _build_group_columns/_build_filter_conditions
# already handle any column generically — this allowlist was the only thing standing between
# "per-collection breakdown" and 500-ing/422-ing. See docs/plan/usage-and-limits/state.md
# (gap G2) for the trail: the API doc had already promised this before it actually worked.
ALLOWED_TABLE_GROUP_FIELDS = ["service_type", "operation", "label", "knowledge_id"]


class DateRange(BaseModel):
    start: str = Field(..., description="Start date (ISO8601)")
    end: str = Field(..., description="End date (ISO8601)")

    @validator("start", "end")
    def validate_date_format(cls, v):
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
            return v
        except (ValueError, AttributeError) as e:
            raise ValueError(f"Invalid ISO8601 timestamp: {e}")


class AnalyticsFilters(BaseModel):
    service_types: Optional[List[str]] = None
    operations: Optional[List[str]] = None
    labels: Optional[List[str]] = None
    key_type: Optional[Literal["platform-key", "owner-key"]] = None
    # Narrows to one collection. Optional — the request stays account-wide (every
    # knowledge base the owner has) when omitted, same as every other filter here.
    knowledge_id: Optional[str] = None
    metadata: Optional[Dict[str, Union[str, List[str]]]] = None

    @validator("metadata")
    def validate_metadata_fields(cls, v):
        if v is None:
            return v
        for key in v.keys():
            if key not in ALLOWED_METADATA_FIELDS:
                raise ValueError(f"Invalid metadata field: '{key}'. Allowed fields: {ALLOWED_METADATA_FIELDS}")
        return v


def _validate_group_by(v):
    if v is None:
        return v
    for field in v:
        if field.startswith("metadata."):
            meta_field = field.split(".", 1)[1]
            if meta_field not in ALLOWED_METADATA_FIELDS:
                raise ValueError(f"Invalid metadata field in group_by: '{meta_field}'")
        elif field not in ALLOWED_TABLE_GROUP_FIELDS and field not in ALLOWED_METRIC_GROUP_FIELDS:
            raise ValueError(
                f"Invalid group_by field: '{field}'. Allowed: {ALLOWED_TABLE_GROUP_FIELDS}, "
                f"{ALLOWED_METRIC_GROUP_FIELDS}, or metadata.*"
            )
    return v


class AnalyticsQueryRequest(BaseModel):
    """knowledge_id is injected by the router from the URL path, not carried here."""

    filters: Optional[AnalyticsFilters] = Field(default_factory=AnalyticsFilters)
    date_range: DateRange
    group_by: List[str] = Field(default_factory=list)
    time_bucket: Optional[TimeBucket] = None
    metrics_to_include: Optional[List[str]] = None
    key_filter: Optional[KeyFilterType] = None
    limit: int = Field(default=1000, ge=1, le=10000)
    offset: int = Field(default=0, ge=0)

    _validate_group_by_fields = validator("group_by", allow_reuse=True)(_validate_group_by)


class PricingPeriodCost(BaseModel):
    effective_from: str
    effective_to: Optional[str] = None
    total_cost_usd: float = 0.0
    llm_cost_usd: Optional[float] = None
    llm_input_tokens_cost_usd: Optional[float] = None
    llm_output_tokens_cost_usd: Optional[float] = None
    embedding_cost_usd: Optional[float] = None
    document_processing_cost_usd: Optional[float] = None
    transcription_cost_usd: Optional[float] = None


class CostBreakdown(BaseModel):
    total_cost_usd: float = 0.0
    llm_cost_usd: Optional[float] = None
    llm_input_tokens_cost_usd: Optional[float] = None
    llm_output_tokens_cost_usd: Optional[float] = None
    embedding_cost_usd: Optional[float] = None
    document_processing_cost_usd: Optional[float] = None
    transcription_cost_usd: Optional[float] = None
    pricing_periods: Optional[List[PricingPeriodCost]] = None


class AggregatedMetrics(BaseModel):
    request_count: int
    total_tokens: Optional[float] = None
    input_tokens: Optional[float] = None
    output_tokens: Optional[float] = None
    audio_seconds: Optional[float] = None
    llama_parse_page_count: Optional[float] = None
    processed_weblink_count: Optional[float] = None
    # Each unambiguous to one (operation, label) pair — see ALL_METRICS in
    # utils/usage/analytics.py for why char_count is deliberately not here too.
    file_count: Optional[float] = None
    text_count: Optional[float] = None
    document_vector_count: Optional[float] = None
    chunk_vector_count: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    cost: Optional[CostBreakdown] = None


class KnowledgeOverviewMetrics(BaseModel):
    total_files_processed: int = 0
    total_char_count: int = 0
    total_audio_seconds: float = 0
    total_llama_parse_pages: int = 0
    total_weblink_pages: int = 0
    total_text_count: int = 0
    total_embedding_tokens: float = 0
    total_vectors_indexed: int = 0
    cost: Optional[CostBreakdown] = None


class AnalyticsDataPoint(BaseModel):
    dimensions: Dict[str, Any]
    metrics: AggregatedMetrics


class AnalyticsQueryResponse(BaseModel):
    owner_id: str
    executed_at: str
    date_range: DateRange
    group_by: List[str]
    time_bucket: Optional[str] = None
    totals: AggregatedMetrics
    knowledge_overview_metrics: Optional[KnowledgeOverviewMetrics] = None
    data: List[AnalyticsDataPoint]
    total_groups: int
    limit: int
    offset: int
    has_more: bool


class LabelOption(BaseModel):
    label: str
    display_name: str


class OperationOption(BaseModel):
    operation: str
    display_name: str
    labels: List[LabelOption] = Field(default_factory=list)


class ServiceTypeOption(BaseModel):
    service_type: str
    display_name: str
    description: Optional[str] = None
    operations: List[OperationOption] = Field(default_factory=list)


class ServiceOptionsResponse(BaseModel):
    service_types: List[ServiceTypeOption]
    all_operations: List[str] = Field(default_factory=list)
    all_labels: List[str] = Field(default_factory=list)
