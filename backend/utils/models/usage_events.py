"""SQLModel tables for usage event recording, ported from fs-agent-usage-limits'
services/database_models_usage_events.py (see docs/plan/usage-and-limits/).

Scoping (revised 2026-08-19, migration 039 — see decisions.md's addendum): `owner_id`
is the required, primary attribution column — usage is reported and limited at the
owner level, not per knowledge base, since one owner can have many knowledge bases
and the home-screen/limits view needs one total across all of them. `owner_id` is
resolved internally via utils/db/knowledge_ops.py, never taken from the request path
directly. `knowledge_id` stays as an optional secondary column — useful for a
per-knowledge-base breakdown, but no longer the identity a row is keyed on.

Python field `event_metadata` maps to DB column `metadata` (reserved in SQLAlchemy).
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Dict, Any
from uuid import UUID, uuid4

from sqlmodel import SQLModel, Field
from sqlalchemy import Column, String, TIMESTAMP, text, ForeignKey, Index, BigInteger
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB, NUMERIC


class UsageEvent(SQLModel, table=True):
    """One row per recorded usage event (LLM call, embedding, ingestion, etc.)."""

    __tablename__ = "usage_events"

    event_id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column("event_id", PG_UUID(as_uuid=True), primary_key=True, nullable=False),
    )

    # Secondary, optional — which knowledge base this event belongs to, for
    # per-knowledge-base breakdowns. No FK constraint: knowledge_config rows can be
    # deleted while historical usage/billing data for that knowledge_id must be retained.
    knowledge_id: Optional[str] = Field(
        default=None,
        sa_column=Column("knowledge_id", String(255), nullable=True),
    )

    # Primary attribution column, resolved internally from knowledge_id — never a
    # request path parameter directly. Required: an event with no resolvable owner
    # has nowhere to live under this model (see migration 039).
    owner_id: str = Field(
        sa_column=Column("owner_id", String(255), nullable=False),
    )

    # 'platform-key' or 'owner-key' — which credential paid for this call.
    key_type: Optional[str] = Field(
        default=None,
        sa_column=Column("key_type", String(50), nullable=True),
    )

    service_type: str = Field(sa_column=Column("service_type", String(100), nullable=False))
    operation: str = Field(sa_column=Column("operation", String(100), nullable=False))
    label: Optional[str] = Field(default=None, sa_column=Column("label", String(100), nullable=True))

    session_id: Optional[str] = Field(default=None, sa_column=Column("session_id", String(255), nullable=True))
    request_id: Optional[str] = Field(default=None, sa_column=Column("request_id", String(255), nullable=True))
    operation_id: Optional[str] = Field(default=None, sa_column=Column("operation_id", String(255), nullable=True))

    timestamp: datetime = Field(sa_column=Column("timestamp", TIMESTAMP(timezone=True), nullable=False))

    event_metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        sa_column=Column("metadata", JSONB),
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(
            "created_at", TIMESTAMP(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
        ),
    )

    __table_args__ = (
        Index("idx_usage_events_knowledge_time", "knowledge_id", "timestamp", postgresql_where=text("knowledge_id IS NOT NULL")),
        Index("idx_usage_events_owner_time", "owner_id", "timestamp", postgresql_where=text("owner_id IS NOT NULL")),
        Index("idx_usage_events_key_type_time", "key_type", "timestamp", postgresql_where=text("key_type IS NOT NULL")),
        Index("idx_usage_events_service_op", "service_type", "operation", "timestamp"),
        Index("idx_usage_events_session", "session_id", "timestamp", postgresql_where=text("session_id IS NOT NULL")),
        Index("idx_usage_events_request", "request_id", "timestamp", postgresql_where=text("request_id IS NOT NULL")),
        Index("idx_usage_events_timestamp", "timestamp"),
        Index("idx_usage_events_metadata_gin", "metadata", postgresql_using="gin"),
    )


class UsageMetric(SQLModel, table=True):
    """One row per (event, metric_name) measurement — e.g. input_tokens, char_count."""

    __tablename__ = "usage_metrics"

    metric_id: Optional[int] = Field(
        default=None,
        sa_column=Column("metric_id", BigInteger, primary_key=True, autoincrement=True, nullable=False),
    )
    event_id: UUID = Field(
        sa_column=Column(
            "event_id", PG_UUID(as_uuid=True), ForeignKey("usage_events.event_id", ondelete="CASCADE"), nullable=False
        )
    )
    metric_name: str = Field(sa_column=Column("metric_name", String(100), nullable=False))
    metric_value: Decimal = Field(sa_column=Column("metric_value", NUMERIC(20, 4), nullable=False))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(
            "created_at", TIMESTAMP(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
        ),
    )

    __table_args__ = (
        Index("idx_usage_metrics_event", "event_id"),
        Index("idx_usage_metrics_name", "metric_name"),
        Index("idx_usage_metrics_name_value", "metric_name", "metric_value"),
    )
