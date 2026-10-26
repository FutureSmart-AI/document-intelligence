"""Owner-level tier/platform-limit configuration, ported from fs-agent-usage-limits'
services/database_models_owner_config.py.

TERMINOLOGY EXCEPTION (documented per docs/plan/usage-and-limits/decisions.md):
tier/platform limits are a genuinely cross-knowledge, account-wide billing concept —
one owner (email) can hold many knowledge bases, and their tier/quota applies across
all of them, not to one knowledge_id. Per the plan's explicit carve-out for concepts
with "no knowledge_id" being flagged rather than force-fit, these tables keep
`owner_id` as their primary key, unchanged from the source service. Every router
that touches this table still takes `/v1/{knowledge_id}/...` in its path (so auth /
audit_access stays consistent with every other router) and resolves `owner_id`
internally via utils/db/knowledge_ops.get_knowledge_service(knowledge_id)["owner_id"]
before touching these tables — see routers/usage_owner_config.py.
"""

from datetime import datetime
from typing import Optional, List

from sqlalchemy import ForeignKeyConstraint
from sqlmodel import SQLModel, Field, Relationship, CheckConstraint, Index


class OwnerConfiguration(SQLModel, table=True):
    __tablename__ = "owner_configurations"
    __table_args__ = (
        CheckConstraint("tier_type IN ('trial', 'custom', 'payg')", name="check_valid_tier_type"),
        CheckConstraint("tier_type != 'custom' OR base_tier IS NOT NULL", name="check_custom_tier_has_base"),
        Index("idx_owner_config_tier", "tier_type"),
    )

    owner_id: str = Field(primary_key=True, index=True, description="Email address (PK)")
    tier_type: str = Field(nullable=False, description="trial, custom, payg")
    base_tier: Optional[str] = Field(default=None, description="trial, payg; required if tier_type is custom")
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    platform_limits: Optional["OwnerPlatformLimits"] = Relationship(back_populates="owner_configuration")
    default_models: Optional["OwnerDefaultModels"] = Relationship(back_populates="owner_configuration")
    enabled_services: List["OwnerEnabledServices"] = Relationship(back_populates="owner_configuration")


class OwnerPlatformLimits(SQLModel, table=True):
    __tablename__ = "owner_platform_limits"
    __table_args__ = (
        CheckConstraint("max_agents IS NULL OR max_agents >= 0"),
        CheckConstraint("max_team_members IS NULL OR max_team_members >= 0"),
        CheckConstraint("max_file_size_mb IS NULL OR max_file_size_mb > 0"),
        CheckConstraint("max_indexed_characters IS NULL OR max_indexed_characters >= 0"),
        CheckConstraint("max_knowledge_items IS NULL OR max_knowledge_items >= 0"),
        CheckConstraint("max_conversations_per_month IS NULL OR max_conversations_per_month >= 0"),
        CheckConstraint("max_messages_per_month IS NULL OR max_messages_per_month >= 0"),
        CheckConstraint("max_llm_tokens_per_month IS NULL OR max_llm_tokens_per_month >= 0"),
        CheckConstraint("max_documents IS NULL OR max_documents >= 0"),
        CheckConstraint("max_urls IS NULL OR max_urls >= 0"),
        CheckConstraint("max_messages_per_day IS NULL OR max_messages_per_day >= 0"),
        CheckConstraint("max_cost_usd_per_month IS NULL OR max_cost_usd_per_month >= 0"),
    )

    owner_id: str = Field(foreign_key="owner_configurations.owner_id", primary_key=True)
    max_agents: Optional[int] = Field(default=None)
    max_team_members: Optional[int] = Field(default=None)
    max_file_size_mb: Optional[int] = Field(default=None)
    max_indexed_characters: Optional[int] = Field(default=None)
    # Standing cap on how many knowledge items an account may hold at once, counted
    # across every knowledge base it owns. A "knowledge item" is any ingested unit —
    # an uploaded file, a file-by-URL, a pasted text, a scraped weblink, a crawled
    # child page, or a synced connector page — i.e. one row in the `document` table,
    # whatever its source. Named `knowledge_items`, not `documents`, because
    # "documents" reads as "uploaded files" and undercounts the other four sources.
    # Deliberately NOT `_per_month`: "any knowledge ingest" includes scheduled
    # connector syncs, and a monthly allowance would be re-consumed on every sync
    # run, making the number measure sync frequency rather than anything the user
    # chose. Delete an item and the slot comes back.
    max_knowledge_items: Optional[int] = Field(default=None)
    max_conversations_per_month: Optional[int] = Field(default=None)
    max_messages_per_month: Optional[int] = Field(default=None)
    max_llm_tokens_per_month: Optional[int] = Field(default=None)
    # Standing sub-caps of max_knowledge_items, not additional totals — the account's
    # combined ingest ceiling above still applies regardless. `max_documents` counts
    # Document rows with content_type='file' (uploads, including a ClickUp-synced page,
    # which is posted through the same path — see _derive_document_source in
    # utils/db/knowledge_ops.py); `max_urls` counts content_type='url' (weblink adds,
    # one row per URL added, not per crawled child page — that finer count is
    # `processed_page_count`, a usage metric, not a document row). Pasted text has no
    # cap here; nothing was asked for one.
    max_documents: Optional[int] = Field(default=None)
    max_urls: Optional[int] = Field(default=None)
    # A second, tighter window on the same activity max_messages_per_month already
    # gates — both apply, whichever trips first. Resets at 00:00 UTC, not the
    # calendar-month boundary the sibling field uses.
    max_messages_per_day: Optional[int] = Field(default=None)
    # Estimated, not billed — same figure the Usage screen's cost strip reports,
    # summed over the calendar month. Reporting-only: nothing calls enforce_operation
    # for this key (estimating cost ahead of an LLM call would mean guessing the model
    # and token count before the call runs), so it can appear in /limits/status and
    # /usage/limits/screen but never trips a 429. See GATED_LIMIT_KEYS in
    # utils/usage/limit_checking.py.
    max_cost_usd_per_month: Optional[float] = Field(default=None)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    owner_configuration: Optional[OwnerConfiguration] = Relationship(back_populates="platform_limits")


class OwnerDefaultModels(SQLModel, table=True):
    __tablename__ = "owner_default_models"
    owner_id: str = Field(foreign_key="owner_configurations.owner_id", primary_key=True)
    llm: str = Field(nullable=False)
    embedding: str = Field(nullable=False)
    enrichment_llm: str = Field(nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    owner_configuration: Optional[OwnerConfiguration] = Relationship(back_populates="default_models")


class OwnerEnabledServices(SQLModel, table=True):
    __tablename__ = "owner_enabled_services"
    __table_args__ = (Index("idx_enabled_services_owner", "owner_id", "enabled"),)

    owner_id: str = Field(foreign_key="owner_configurations.owner_id", primary_key=True)
    service_name: str = Field(primary_key=True)
    enabled: bool = Field(default=True, nullable=False)
    enabled_at: Optional[datetime] = Field(default=None)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    owner_configuration: Optional[OwnerConfiguration] = Relationship(back_populates="enabled_services")
    service_features: List["ServiceFeatures"] = Relationship(
        back_populates="owner_enabled_service",
        sa_relationship_kwargs={
            "primaryjoin": "and_(OwnerEnabledServices.owner_id==ServiceFeatures.owner_id, "
            "OwnerEnabledServices.service_name==ServiceFeatures.service_name)",
            "foreign_keys": "[ServiceFeatures.owner_id, ServiceFeatures.service_name]",
        },
    )


class ServiceFeatures(SQLModel, table=True):
    __tablename__ = "usage_service_features"
    __table_args__ = (
        ForeignKeyConstraint(["owner_id", "service_name"], ["owner_enabled_services.owner_id", "owner_enabled_services.service_name"]),
        Index("idx_usage_service_features_lookup", "owner_id", "service_name", "enabled"),
    )

    owner_id: str = Field(primary_key=True)
    service_name: str = Field(primary_key=True)
    feature_name: str = Field(primary_key=True)
    enabled: bool = Field(default=True, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    owner_enabled_service: Optional[OwnerEnabledServices] = Relationship(
        back_populates="service_features",
        sa_relationship_kwargs={
            "primaryjoin": "and_(ServiceFeatures.owner_id==OwnerEnabledServices.owner_id, "
            "ServiceFeatures.service_name==OwnerEnabledServices.service_name)",
            "foreign_keys": "[ServiceFeatures.owner_id, ServiceFeatures.service_name]",
        },
    )
