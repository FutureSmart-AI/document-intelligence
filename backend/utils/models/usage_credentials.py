"""Encrypted third-party API credential storage, ported from fs-agent-usage-limits'
services/database_models_credentials.py.

Scoping (revised 2026-08-19, migration 039 — see decisions.md's addendum): BYOK is an
owner-wide thing — an owner supplies one key per provider, used across every
knowledge base they have, not a separate key per knowledge base. `owner_id` +
`provider` is the primary key. `knowledge_id` is kept as an optional, informational
column (which knowledge base's request last added/touched this credential) — it is
no longer part of the row's identity.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import SQLModel, Field, Column, Index, UniqueConstraint
from sqlalchemy import String, Text, Boolean, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


class OwnerCredential(SQLModel, table=True):
    """One row per (owner_id, provider) encrypted API credential.

    Maps to the `owner_provider_keys` table (renamed by migration 039 from
    `knowledge_provider_keys`, which was keyed by knowledge_id + provider).
    """

    __tablename__ = "owner_provider_keys"
    __table_args__ = (
        UniqueConstraint("owner_id", "provider", name="uq_owner_provider_keys_owner_provider"),
        Index("idx_owner_provider_keys_owner_provider", "owner_id", "provider"),
    )

    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column("id", PG_UUID(as_uuid=True), primary_key=True, nullable=False),
        description="Unique identifier for the credential",
    )
    owner_id: str = Field(
        sa_column=Column("owner_id", String(255), nullable=False),
        description="Owner this credential belongs to — shared across all their knowledge bases",
    )
    # Informational only, resolved from the request at write time — not part of identity.
    knowledge_id: Optional[str] = Field(
        default=None,
        sa_column=Column("knowledge_id", String(255), nullable=True),
        description="Knowledge base whose request most recently added/updated this credential",
    )
    provider: str = Field(
        sa_column=Column("provider", String(50), nullable=False),
        description="Provider name: openai, anthropic, llamaparse, google, groq, firecrawl, deepseek, minimax",
    )
    encrypted_key: str = Field(sa_column=Column("encrypted_key", Text, nullable=False))
    key_hash: str = Field(
        sa_column=Column("key_hash", String(64), nullable=False),
        description="HMAC-SHA256 hash of the key for duplicate detection",
    )
    key_mask: str = Field(
        sa_column=Column("key_mask", String(10), nullable=False),
        description="First 3 + last 4 chars, e.g. 'sk-...48sA'",
    )
    # Live signal, not a write-once flag: a credential that the provider rejects with
    # an auth error (or a spent balance) is demoted to False, which is what makes
    # get_credential_for_owner fall back to the platform key on the NEXT request
    # without any call site needing to know. Migration 041 added the three columns
    # below that give the flag its provenance. A rate limit deliberately does NOT
    # demote a key — it is transient, and treating it as death would hand a busy
    # owner's traffic to the platform key for good.
    is_valid: bool = Field(default=True, sa_column=Column("is_valid", Boolean, nullable=False, server_default="TRUE"))
    last_error_type: Optional[str] = Field(
        default=None,
        sa_column=Column("last_error_type", String(50), nullable=True),
        description="Why the key was last rejected: auth_error, low_balance, provider_error, …",
    )
    last_error_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column("last_error_at", TIMESTAMP, nullable=True),
        description="When the provider last rejected this key",
    )
    last_validated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column("last_validated_at", TIMESTAMP, nullable=True),
        description="When this key last provably worked — drives lazy re-validation",
    )
    last_used_at: Optional[datetime] = Field(default=None, sa_column=Column("last_used_at", TIMESTAMP, nullable=True))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column("created_at", TIMESTAMP, nullable=False, server_default="NOW()"),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column("updated_at", TIMESTAMP, nullable=False, server_default="NOW()"),
    )
