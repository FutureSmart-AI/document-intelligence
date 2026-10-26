"""Schemas for account-wide tier/platform-limit configuration, ported from
fs-agent-usage-limits' services/pydantic_models_owner_config.py.

owner_id is the genuine primary key here (documented exception — see
utils/models/usage_owner_config.py); every router endpoint still takes
`/v1/{knowledge_id}/...` and resolves owner_id internally before touching these
schemas, so no request ever carries a raw owner_id in its path.
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Literal, List


class PlatformLimits(BaseModel):
    max_agents: Optional[int] = Field(None, ge=0)
    max_team_members: Optional[int] = Field(None, ge=0)
    max_file_size_mb: Optional[int] = Field(None, gt=0)
    max_indexed_characters: Optional[int] = Field(None, ge=0)
    max_knowledge_items: Optional[int] = Field(None, ge=0, description="Standing cap on knowledge items (any ingested unit — file, URL, text, scraped/crawled page, synced page) held at once, account-wide. Not monthly.")
    max_conversations_per_month: Optional[int] = Field(None, ge=0)
    max_messages_per_month: Optional[int] = Field(None, ge=0)
    max_llm_tokens_per_month: Optional[int] = Field(None, ge=0)
    max_documents: Optional[int] = Field(None, ge=0, description="Standing cap on document rows with content_type='file' — a sub-cap of max_knowledge_items, not an additional total.")
    max_urls: Optional[int] = Field(None, ge=0, description="Standing cap on document rows with content_type='url' — one row per URL added, not per crawled child page. A sub-cap of max_knowledge_items.")
    max_messages_per_day: Optional[int] = Field(None, ge=0, description="A second, tighter window on the same activity max_messages_per_month gates. Resets at 00:00 UTC.")
    max_cost_usd_per_month: Optional[float] = Field(None, ge=0, description="Estimated cost ceiling, same figure the Usage screen's cost strip reports. Reporting-only — never enforced.")


class DefaultModels(BaseModel):
    llm: str = Field(..., min_length=1, max_length=100)
    embedding: str = Field(..., min_length=1, max_length=100)
    enrichment_llm: str = Field(..., min_length=1, max_length=100)


class DefaultModelsPartial(BaseModel):
    llm: Optional[str] = Field(None, min_length=1, max_length=100)
    embedding: Optional[str] = Field(None, min_length=1, max_length=100)
    enrichment_llm: Optional[str] = Field(None, min_length=1, max_length=100)


class ServiceFeatures(BaseModel):
    enabled: bool
    enabled_at: Optional[str] = None
    features: Dict[str, bool] = Field(default_factory=dict)


class OwnerConfigCreateRequest(BaseModel):
    tier_type: Literal["trial", "custom", "payg"]
    base_tier: Optional[Literal["trial", "payg"]] = None
    platform_limits: PlatformLimits
    default_models: DefaultModels
    enabled_services: Optional[Dict[str, ServiceFeatures]] = None

    @validator("base_tier")
    def validate_base_tier(cls, v, values):
        tier_type = values.get("tier_type")
        if tier_type == "custom" and v is None:
            raise ValueError("base_tier is required when tier_type is custom")
        if tier_type != "custom" and v is not None:
            raise ValueError("base_tier should only be set when tier_type is custom")
        return v


class OwnerConfigPatchRequest(BaseModel):
    tier_type: Optional[Literal["trial", "custom", "payg"]] = None
    base_tier: Optional[Literal["trial", "payg"]] = None
    platform_limits: Optional[PlatformLimits] = None
    default_models: Optional[DefaultModelsPartial] = None


class ServiceEnableRequest(BaseModel):
    service_name: str = Field(..., min_length=1, max_length=100)
    features: Optional[Dict[str, bool]] = None


class ServiceDisableRequest(BaseModel):
    service_name: str = Field(..., min_length=1, max_length=100)


class OwnerConfigResponse(BaseModel):
    owner_id: str
    tier_type: Literal["trial", "custom", "payg"]
    base_tier: Optional[Literal["trial", "payg"]] = None
    platform_limits: PlatformLimits
    default_models: DefaultModels
    enabled_services: Dict[str, ServiceFeatures] = Field(default_factory=dict)


class ServiceResponse(BaseModel):
    owner_id: str
    service_name: str
    enabled: bool
    enabled_at: Optional[str] = None
    features: Dict[str, bool] = Field(default_factory=dict)


class ServicesListResponse(BaseModel):
    owner_id: str
    services: Dict[str, ServiceFeatures]


class ServiceMetadata(BaseModel):
    name: str
    description: str
    default_enabled: bool


class ValidServicesResponse(BaseModel):
    services: List[ServiceMetadata]
