"""Service pricing table, ported from fs-agent-usage-limits' services/database_models_pricing.py.

TERMINOLOGY EXCEPTION: pricing is a global, service-wide table (e.g. "gpt-4o costs
$X per 1M input tokens as of date Y") with no owner or knowledge_id dimension at
all — every knowledge base and every owner is priced from the same rows. Left
unscoped, matching the source service exactly. See docs/plan/usage-and-limits/decisions.md.
"""

from datetime import datetime
from typing import Optional, Dict, Any

from sqlmodel import SQLModel, Field
from sqlalchemy import Column, String, TIMESTAMP, Text, BigInteger, ForeignKey, text
from sqlalchemy.dialects.postgresql import JSONB


class ServicePricing(SQLModel, table=True):
    """Versioned pricing for AI services (LLM, embedding, document parsing, transcription, web scraping)."""

    __tablename__ = "usage_service_pricing"

    pricing_id: Optional[int] = Field(
        default=None,
        sa_column=Column("pricing_id", BigInteger, primary_key=True, autoincrement=True, nullable=False),
    )
    service_name: str = Field(sa_column=Column("service_name", String(100), nullable=False))
    service_type: str = Field(sa_column=Column("service_type", String(50), nullable=False))
    provider: str = Field(sa_column=Column("provider", String(100), nullable=False))
    pricing_model: str = Field(sa_column=Column("pricing_model", String(50), nullable=False))
    effective_from: datetime = Field(sa_column=Column("effective_from", TIMESTAMP(timezone=True), nullable=False))
    effective_to: Optional[datetime] = Field(
        default=None, sa_column=Column("effective_to", TIMESTAMP(timezone=True), nullable=True)
    )
    pricing_details: Dict[str, Any] = Field(sa_column=Column("pricing_details", JSONB, nullable=False))
    change_reason: Optional[str] = Field(default=None, sa_column=Column("change_reason", Text, nullable=True))
    previous_pricing_id: Optional[int] = Field(
        default=None,
        sa_column=Column("previous_pricing_id", BigInteger, ForeignKey("usage_service_pricing.pricing_id"), nullable=True),
    )
    created_by: Optional[str] = Field(default=None, sa_column=Column("created_by", String(100), nullable=True))
    created_at: Optional[datetime] = Field(
        default=None, sa_column=Column("created_at", TIMESTAMP(timezone=True), server_default=text("now()"), nullable=True)
    )
    updated_at: Optional[datetime] = Field(
        default=None, sa_column=Column("updated_at", TIMESTAMP(timezone=True), server_default=text("now()"), nullable=True)
    )
