"""Migration 035: Create all tables for the usage-service migration
(fs-agent-usage-limits -> in-process, see docs/plan/usage-and-limits/archive/design-service-migration.md).

Tables created (see utils/models/usage_*.py for the SQLModel definitions the
application code actually uses):
  - usage_events / usage_metrics          — event recording + analytics, scoped by knowledge_id
  - knowledge_provider_keys               — encrypted BYOK credentials, scoped by knowledge_id
  - owner_configurations / owner_platform_limits / owner_default_models /
    owner_enabled_services / usage_service_features
                                           — account-wide tier/quota config, scoped by owner_id
                                             (documented exception — see decisions.md)
  - usage_service_pricing                 — global pricing table, unscoped (documented exception)

Run from the repo root:
    python migrations/035_create_usage_service_tables.py upgrade
    python migrations/035_create_usage_service_tables.py downgrade
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import os
try:
    from dotenv import load_dotenv  # type: ignore
    mode = os.getenv("MODE", "DEV").upper()
    env_file = ".env.prod" if mode == "PROD" else (".env.dev" if mode == "DEV" else ".env.local")
    load_dotenv(Path(ROOT, env_file), override=True)
except Exception:
    pass

POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_PORT = os.getenv("POSTGRES_PORT")
POSTGRES_DB_NAME = os.getenv("POSTGRES_DB_NAME")

DATABASE_URL = (
    f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB_NAME}"
)

UPGRADE_STATEMENTS = [
    # --- owner_configurations (created first — usage_events/knowledge_provider_keys don't FK to
    #     it since knowledge bases and their events must outlive an owner config row) ---
    """
    CREATE TABLE IF NOT EXISTS owner_configurations (
        owner_id TEXT PRIMARY KEY,
        tier_type TEXT NOT NULL,
        base_tier TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT check_valid_tier_type CHECK (tier_type IN ('trial', 'custom', 'payg')),
        CONSTRAINT check_custom_tier_has_base CHECK (tier_type != 'custom' OR base_tier IS NOT NULL)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_owner_config_tier ON owner_configurations (tier_type)",
    """
    CREATE TABLE IF NOT EXISTS owner_platform_limits (
        owner_id TEXT PRIMARY KEY REFERENCES owner_configurations(owner_id) ON DELETE CASCADE,
        max_agents INTEGER,
        max_team_members INTEGER,
        max_file_size_mb INTEGER,
        max_indexed_characters BIGINT,
        max_conversations_per_month INTEGER,
        max_messages_per_month INTEGER,
        max_llm_tokens_per_month BIGINT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS owner_default_models (
        owner_id TEXT PRIMARY KEY REFERENCES owner_configurations(owner_id) ON DELETE CASCADE,
        llm TEXT NOT NULL,
        embedding TEXT NOT NULL,
        enrichment_llm TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS owner_enabled_services (
        owner_id TEXT NOT NULL REFERENCES owner_configurations(owner_id) ON DELETE CASCADE,
        service_name TEXT NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        enabled_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (owner_id, service_name)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_enabled_services_owner ON owner_enabled_services (owner_id, enabled)",
    """
    CREATE TABLE IF NOT EXISTS usage_service_features (
        owner_id TEXT NOT NULL,
        service_name TEXT NOT NULL,
        feature_name TEXT NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (owner_id, service_name, feature_name),
        FOREIGN KEY (owner_id, service_name) REFERENCES owner_enabled_services(owner_id, service_name) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_usage_service_features_lookup ON usage_service_features (owner_id, service_name, enabled)",
    # --- usage_events / usage_metrics — scoped by knowledge_id ---
    """
    CREATE TABLE IF NOT EXISTS usage_events (
        event_id UUID PRIMARY KEY,
        knowledge_id TEXT,
        owner_id TEXT,
        key_type TEXT,
        service_type TEXT NOT NULL,
        operation TEXT NOT NULL,
        label TEXT,
        session_id TEXT,
        request_id TEXT,
        operation_id TEXT,
        timestamp TIMESTAMPTZ NOT NULL,
        metadata JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_usage_events_knowledge_time ON usage_events (knowledge_id, timestamp) WHERE knowledge_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_owner_time ON usage_events (owner_id, timestamp) WHERE owner_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_key_type_time ON usage_events (key_type, timestamp) WHERE key_type IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_service_op ON usage_events (service_type, operation, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_session ON usage_events (session_id, timestamp) WHERE session_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_request ON usage_events (request_id, timestamp) WHERE request_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_timestamp ON usage_events (timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_metadata_gin ON usage_events USING gin (metadata)",
    """
    CREATE TABLE IF NOT EXISTS usage_metrics (
        metric_id BIGSERIAL PRIMARY KEY,
        event_id UUID NOT NULL REFERENCES usage_events(event_id) ON DELETE CASCADE,
        metric_name TEXT NOT NULL,
        metric_value NUMERIC(20, 4) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_usage_metrics_event ON usage_metrics (event_id)",
    "CREATE INDEX IF NOT EXISTS idx_usage_metrics_name ON usage_metrics (metric_name)",
    "CREATE INDEX IF NOT EXISTS idx_usage_metrics_name_value ON usage_metrics (metric_name, metric_value)",
    # --- knowledge_provider_keys — scoped by knowledge_id ---
    """
    CREATE TABLE IF NOT EXISTS knowledge_provider_keys (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        knowledge_id TEXT NOT NULL,
        owner_id TEXT,
        provider TEXT NOT NULL,
        encrypted_key TEXT NOT NULL,
        key_hash VARCHAR(64) NOT NULL,
        key_mask VARCHAR(10) NOT NULL,
        is_valid BOOLEAN NOT NULL DEFAULT TRUE,
        last_used_at TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_knowledge_provider_keys_knowledge_provider UNIQUE (knowledge_id, provider)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_knowledge_provider_keys_knowledge_provider ON knowledge_provider_keys (knowledge_id, provider)",
    # --- usage_service_pricing — global, unscoped ---
    """
    CREATE TABLE IF NOT EXISTS usage_service_pricing (
        pricing_id BIGSERIAL PRIMARY KEY,
        service_name VARCHAR(100) NOT NULL,
        service_type VARCHAR(50) NOT NULL,
        provider VARCHAR(100) NOT NULL,
        pricing_model VARCHAR(50) NOT NULL,
        effective_from TIMESTAMPTZ NOT NULL,
        effective_to TIMESTAMPTZ,
        pricing_details JSONB NOT NULL,
        change_reason TEXT,
        previous_pricing_id BIGINT REFERENCES usage_service_pricing(pricing_id),
        created_by VARCHAR(100),
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_usage_service_pricing_lookup ON usage_service_pricing (service_name, provider, service_type, effective_from)",
]

DOWNGRADE_STATEMENTS = [
    "DROP TABLE IF EXISTS usage_metrics",
    "DROP TABLE IF EXISTS usage_events",
    "DROP TABLE IF EXISTS knowledge_provider_keys",
    "DROP TABLE IF EXISTS usage_service_pricing",
    "DROP TABLE IF EXISTS usage_service_features",
    "DROP TABLE IF EXISTS owner_enabled_services",
    "DROP TABLE IF EXISTS owner_default_models",
    "DROP TABLE IF EXISTS owner_platform_limits",
    "DROP TABLE IF EXISTS owner_configurations",
]


async def upgrade() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        for stmt in UPGRADE_STATEMENTS:
            await conn.execute(text(stmt))
    await engine.dispose()
    print("Created usage-service tables (usage_events, usage_metrics, knowledge_provider_keys, "
          "owner_configurations + related, usage_service_pricing).")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        for stmt in DOWNGRADE_STATEMENTS:
            await conn.execute(text(stmt))
    await engine.dispose()
    print("Dropped usage-service tables.")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 035_create_usage_service_tables.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
