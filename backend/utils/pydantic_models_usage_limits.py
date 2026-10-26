"""Schemas for quota/limit checking, ported from fs-agent-usage-limits'
services/pydantic_models_limit_checking.py.

Both sides of a limit check are account-wide: the CEILINGS (max_agents,
max_llm_tokens_per_month, ...) come from the OwnerPlatformLimits row, and the current
usage they're compared against is summed across every knowledge base the owner has.
So CurrentUsageInfo/UsageStatusResponse/UsageCheckResponse are keyed by owner_id,
matching the `/v1/owners/{owner_id}/usage/limits/...` routes.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from enum import Enum


class PeriodInfo(BaseModel):
    start: str
    end: str


class LimitsInfo(BaseModel):
    max_agents: Optional[int] = None
    max_indexed_characters: Optional[int] = None
    max_knowledge_items: Optional[int] = None
    max_conversations_per_month: Optional[int] = None
    max_messages_per_month: Optional[int] = None
    max_llm_tokens_per_month: Optional[int] = None
    max_documents: Optional[int] = None
    max_urls: Optional[int] = None
    max_messages_per_day: Optional[int] = None
    max_cost_usd_per_month: Optional[float] = None


class CurrentUsageInfo(BaseModel):
    indexed_characters: int
    # Standing counts (current totals, not month-windowed): how many knowledge items
    # the account holds right now (any ingested unit, not just uploaded files) and how
    # many knowledge bases ("agents") it has.
    knowledge_items: int = 0
    agents: int = 0
    # Standing sub-counts of knowledge_items, by content_type: documents = 'file' rows
    # (uploads, ClickUp pages included), urls = 'url' rows. documents + urls + (plain
    # text rows, uncapped) sum to knowledge_items.
    documents: int = 0
    urls: int = 0
    conversations_this_month: int
    messages_this_month: int
    llm_tokens_this_month: int
    # Always "today" (00:00 UTC to now), independent of whatever month window the
    # rest of this response was computed over.
    messages_today: int = 0
    cost_usd_this_month: float = 0.0


class RemainingQuotaInfo(BaseModel):
    indexed_characters: Optional[int] = Field(None, description="null = unlimited")
    knowledge_items: Optional[int] = None
    agents: Optional[int] = None
    conversations: Optional[int] = None
    messages: Optional[int] = None
    llm_tokens: Optional[int] = None
    documents: Optional[int] = None
    urls: Optional[int] = None
    messages_today: Optional[int] = None
    cost_usd: Optional[float] = None


class UsageStatusResponse(BaseModel):
    owner_id: str
    period: PeriodInfo
    limits: LimitsInfo
    current_usage: CurrentUsageInfo
    remaining: RemainingQuotaInfo


class EstimatedMetric(BaseModel):
    name: str
    value: float


class Operation(str, Enum):
    index_content = "index_content"
    create_knowledge_item = "create_knowledge_item"  # one ingested unit, any source
    create_agent = "create_agent"                    # one knowledge base / collection
    start_conversation = "start_conversation"
    send_message = "send_message"
    llm_call = "llm_call"


class UsageCheckRequest(BaseModel):
    operation: Operation
    estimated_metrics: List[EstimatedMetric]


class ExceededLimitInfo(BaseModel):
    limit_name: str
    current: int
    limit: int
    requested: int


class UsageCheckResponse(BaseModel):
    allowed: bool
    remaining_quota: Dict[str, Optional[int]]
    would_exceed: List[ExceededLimitInfo]


# --- Screen-shaped Limits tab, proposed in docs/product/usage-and-limits-screen.md §7 ---
# Repackages exactly what /limits/status already returns, one row per limit, so a UI
# renders it with no arithmetic and no need to know that `max_agents` means
# "collections". Adds no ceiling and changes no value — see decisions.md.

class TierInfo(BaseModel):
    name: str
    tier_type: str
    is_default: bool = True


class LimitsScreenItem(BaseModel):
    key: str
    label: str
    used: float
    limit: Optional[int] = None
    unit: str
    maximum: str
    note: str
    window: str  # "current" | "calendar_day" | "calendar_month" | "lifetime" — see the item's note for why
    lifted: bool = False
    percent: Optional[float] = None
    enforced: bool = False


class LimitsScreenGroup(BaseModel):
    group: str
    items: List[LimitsScreenItem]


class LimitsScreenResponse(BaseModel):
    owner_id: str
    tier: TierInfo
    window: PeriodInfo
    own_credentials: List[str]
    groups: List[LimitsScreenGroup]
    exceeded: List[str]
