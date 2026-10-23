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
    """Create user_defined_field_manifest table.

    Persisted per-knowledge registry of user-defined (passthrough) metadata field names,
    so GET /enrichment can return the complete field set deterministically instead of
    sampling a handful of documents. Decoupled from field_configurations.
    """
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS user_defined_field_manifest (
                id            SERIAL PRIMARY KEY,
                knowledge_id  VARCHAR(255) NOT NULL,
                field_name    VARCHAR(512) NOT NULL,
                field_type    VARCHAR(64)  NOT NULL DEFAULT 'str',
                sample_value  JSONB,
                is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
                first_seen_at TIMESTAMPTZ  DEFAULT CURRENT_TIMESTAMP,
                last_seen_at  TIMESTAMPTZ  DEFAULT CURRENT_TIMESTAMP,
                created_at    TIMESTAMPTZ  DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMPTZ  DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_udfm_knowledge_field UNIQUE (knowledge_id, field_name)
            )
            """
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_udfm_knowledge_id "
            "ON user_defined_field_manifest(knowledge_id)"
        ))
    await engine.dispose()
    print("Created user_defined_field_manifest table")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop user_defined_field_manifest table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "DROP INDEX IF EXISTS ix_udfm_knowledge_id"
        ))
        await conn.execute(text(
            "DROP TABLE IF EXISTS user_defined_field_manifest"
        ))
    await engine.dispose()
    print("Dropped user_defined_field_manifest table")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 013_create_user_defined_field_manifest.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
