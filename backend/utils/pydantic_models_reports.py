"""Request/response models for Batch Query Reports.

Public models (user-facing) and internal models (Worker-facing) live
together here, mirroring `utils/pydantic_models_knowledge_invitations.py`'s
convention — see docs/plan/batch-query-reports/schema.md. Ported from
fs-agent-dashboard's `batch_query_reports/pydantic_models_restful.py`; no
`agent_id`/`owner_email` fields — `knowledge_id` is always the router's path
param (see routers/reports.py), matching this service's own convention.
"""
from pydantic import BaseModel, field_validator
from typing import Optional, List, Any
from datetime import datetime

try:  # pydantic v2
    from typing import Literal
    GroupType = Literal["independent", "sequential"]
    Frequency = Literal["daily", "weekly", "monthly", "cron"]
    InternalStatus = Literal["running", "completed", "failed"]
except Exception:  # pragma: no cover
    GroupType = str
    Frequency = str
    InternalStatus = str


# ---------------------------------------------------------------------------
# Public — report definition
# ---------------------------------------------------------------------------

class QueryGroupInput(BaseModel):
    type: GroupType
    queries: List[str]


class ScheduleInput(BaseModel):
    frequency: Frequency
    cron: Optional[str] = None
    timezone: Optional[str] = "UTC"


class CreateReportRequest(BaseModel):
    name: str
    report_instructions: Optional[str] = None
    query_groups: List[QueryGroupInput]
    analysis_prompt: Optional[str] = None
    schedule: Optional[ScheduleInput] = None
    run_immediately: bool = False

    @field_validator("query_groups")
    @classmethod
    def _non_empty(cls, v):
        if not v:
            raise ValueError("query_groups must contain at least one group")
        return v


class UpdateReportRequest(BaseModel):
    name: Optional[str] = None
    report_instructions: Optional[str] = None
    query_groups: Optional[List[QueryGroupInput]] = None
    analysis_prompt: Optional[str] = None
    schedule: Optional[ScheduleInput] = None


class UpdateReportSettingsRequest(BaseModel):
    # New per-report query cap. Range-checked in the router (1..1000).
    max_queries_per_report: int


# ---------------------------------------------------------------------------
# Internal — Worker-facing
# ---------------------------------------------------------------------------

class StatusUpdateRequest(BaseModel):
    status: InternalStatus
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    failure_reason: Optional[str] = None


class ResultItem(BaseModel):
    query_id: str
    group_id: str
    session_id: Optional[str] = None
    answer: Optional[str] = None
    answer_ref: Optional[str] = None
    sources_used: Optional[List[Any]] = None
    retrieved_documents: Optional[List[Any]] = None
    response_time_ms: Optional[int] = None
    token_usage: Optional[int] = None
    error: Optional[str] = None


class ResultsSubmitRequest(BaseModel):
    results: List[ResultItem]


class AnalysisSubmitRequest(BaseModel):
    overall_summary: str
