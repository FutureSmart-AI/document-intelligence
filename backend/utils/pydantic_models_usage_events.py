"""Request/response schemas for usage event recording, ported from
fs-agent-usage-limits' services/pydantic_models_usage_events.py.

Scoping: `owner_id` is the primary attribution field and comes from the route's
`/v1/owners/{owner_id}/...` path, not the body. `knowledge_id` is an optional
per-event field — it attributes an event to one knowledge base for breakdown
purposes, and events within a single batch may name different ones (or none).
"""

from pydantic import BaseModel, Field, validator, root_validator
from typing import Optional, Dict, List, Any
from datetime import datetime, timezone, timedelta


class UsageMetric(BaseModel):
    """A single metric measurement, e.g. {"name": "input_tokens", "value": 2500}."""

    name: str = Field(..., min_length=1, max_length=100)
    value: float

    class Config:
        json_schema_extra = {"example": {"name": "input_tokens", "value": 2500}}


class UsageEventRequest(BaseModel):
    """Single usage event to be recorded against an account.

    owner_id is supplied by the router from the URL path, not by the caller — it is
    not a field here on purpose, matching how every other write endpoint in this
    service takes its scope from the path rather than the body.
    """

    knowledge_id: Optional[str] = Field(
        None, max_length=255,
        description="Optional — which knowledge base this event belongs to, for per-knowledge-base breakdown",
    )
    key_type: Optional[str] = Field(None, description="'platform-key' or 'owner-key', for cost tracking")
    agent_id: Optional[str] = Field(None, min_length=1, max_length=255)
    service_type: str = Field(..., min_length=1, max_length=100)
    operation: str = Field(..., min_length=1, max_length=100)
    timestamp: str
    label: Optional[str] = Field(None, max_length=100)
    session_id: Optional[str] = Field(None, max_length=255)
    request_id: Optional[str] = Field(None, max_length=255)
    operation_id: Optional[str] = Field(None, max_length=255)
    metadata: Optional[Dict[str, Any]] = None
    metrics: Optional[List[UsageMetric]] = None

    @validator("timestamp")
    def validate_timestamp(cls, v):
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                raise ValueError("timestamp must include timezone information (use ISO8601 format with Z or +00:00)")
            now = datetime.now(timezone.utc)
            if dt > now:
                raise ValueError("timestamp cannot be in the future")
            if (now - dt) > timedelta(days=7):
                raise ValueError("timestamp cannot be older than 7 days")
            return v
        except (ValueError, AttributeError) as e:
            if "timestamp must include timezone" in str(e):
                raise
            raise ValueError(f"Invalid ISO8601 timestamp: {e}")

    @validator("metrics")
    def validate_metrics(cls, v):
        if v is not None and len(v) > 100:
            raise ValueError("Maximum 100 metrics per event")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "key_type": "platform-key",
                "service_type": "knowledge",
                "operation": "llm_generation",
                "label": "rag_agent",
                "session_id": "sess_789",
                "timestamp": "2025-12-09T10:30:00Z",
                "metadata": {"provider": "anthropic", "model": "claude-sonnet-4"},
                "metrics": [
                    {"name": "input_tokens", "value": 2500},
                    {"name": "output_tokens", "value": 350},
                ],
            }
        }


class UsageEventsRequest(BaseModel):
    """Bulk usage event recording request for one account (owner)."""

    events: List[UsageEventRequest]

    @validator("events")
    def validate_events(cls, v):
        if not v:
            raise ValueError("events array cannot be empty")
        if len(v) > 1000:
            raise ValueError("maximum 1000 events per request")
        return v


class FailedEvent(BaseModel):
    index: int
    error: str
    event: Dict[str, Any]


class UsageEventsResponse(BaseModel):
    processed_count: int
    failed_count: int
    failed_events: List[FailedEvent] = Field(default_factory=list)


class EventMetricResponse(BaseModel):
    name: str
    value: float


class UsageEventResponse(BaseModel):
    event_id: str
    knowledge_id: Optional[str] = None
    owner_id: Optional[str] = None
    key_type: Optional[str] = None
    agent_id: Optional[str] = None
    service_type: str
    operation: str
    timestamp: str
    label: Optional[str] = None
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    operation_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    metrics: List[EventMetricResponse] = Field(default_factory=list)
    created_at: str


class UsageEventsListResponse(BaseModel):
    total_count: int
    events: List[UsageEventResponse]
    limit: int
    offset: int


class AggregatedMetric(BaseModel):
    metric_name: str
    total: float
    avg: float
    min: float
    max: float
    count: int


class AggregatedUsageResponse(BaseModel):
    group_by: List[str]
    groups: List[Dict[str, Any]]


class ServiceOperationSummary(BaseModel):
    service_type: str
    operation: str
    event_count: int
    metrics: List[AggregatedMetric] = Field(default_factory=list)


class UsageSummaryResponse(BaseModel):
    owner_id: str
    start_date: str
    end_date: str
    total_events: int
    services: List[ServiceOperationSummary]
