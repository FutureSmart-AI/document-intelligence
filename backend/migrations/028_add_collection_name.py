"""Migration 028: Add knowledge_config.collection_name (G1).

Closes G1 in docs/screen-api-flow-document-intelligence.md — Home cards and
Create Collection have no name field to render/send today; only
service_description exists, which is a longer free-text description, not a
short label. `create_knowledge_service` already had a dead `service_name`
parameter (commented out at the one call site) hinting this was started once
and abandoned; this finishes it under the name the flow doc and dashboard use.

Additive only: one nullable column, no backfill needed — an existing
collection simply has no name until its owner sets one via PUT /config.

Run from the repo root:
    python migrations/028_add_collection_name.py upgrade
    python migrations/028_add_collection_name.py downgrade
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


async def upgrade() -> None:
    """Add knowledge_config.collection_name column."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE knowledge_config ADD COLUMN IF NOT EXISTS collection_name TEXT"
        ))
    await engine.dispose()
    print("Added knowledge_config.collection_name column")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop knowledge_config.collection_name column."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE knowledge_config DROP COLUMN IF EXISTS collection_name"
        ))
    await engine.dispose()
    print("Dropped knowledge_config.collection_name column")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 028_add_collection_name.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
