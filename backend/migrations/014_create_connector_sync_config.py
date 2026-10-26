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


async def upgrade() -> None:
    """Create connector_sync_config table.

    Per-knowledge, per-source configuration for self-serve external indexing
    (ClickUp first; source-agnostic). Stores the encrypted token + workspace,
    the user's space/folder selection, deletion behaviour, and a simple recurring
    interval. The knowledge-side beat scans (enabled, next_sync_at) to enqueue runs.
    """
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS connector_sync_config (
                id                  SERIAL PRIMARY KEY,
                knowledge_id        VARCHAR(255) NOT NULL,
                source              VARCHAR(64)  NOT NULL DEFAULT 'clickup',
                enabled             BOOLEAN      NOT NULL DEFAULT FALSE,
                encrypted_token     TEXT,
                selection           JSONB,
                interval_minutes    INTEGER,
                last_sync_at        TIMESTAMPTZ,
                next_sync_at        TIMESTAMPTZ,
                created_at          TIMESTAMPTZ  DEFAULT CURRENT_TIMESTAMP,
                updated_at          TIMESTAMPTZ  DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_connector_knowledge_source UNIQUE (knowledge_id, source)
            )
            """
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_connector_sync_config_knowledge_id "
            "ON connector_sync_config(knowledge_id)"
        ))
        # Supports the beat's "enabled AND next_sync_at <= now()" due-scan.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_connector_sync_config_due "
            "ON connector_sync_config(enabled, next_sync_at)"
        ))
    await engine.dispose()
    print("Created connector_sync_config table")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop connector_sync_config table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP INDEX IF EXISTS ix_connector_sync_config_due"))
        await conn.execute(text("DROP INDEX IF EXISTS ix_connector_sync_config_knowledge_id"))
        await conn.execute(text("DROP TABLE IF EXISTS connector_sync_config"))
    await engine.dispose()
    print("Dropped connector_sync_config table")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 014_create_connector_sync_config.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
