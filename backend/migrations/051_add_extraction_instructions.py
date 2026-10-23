"""Migration 051: add extraction_instructions column to knowledge_config.

Persists the free-text intent a user types when asking the LLM field generator
(utils/config/field_generation.py) to propose an enrichment schema — e.g. "I
need to filter by vendor, contract value and renewal date". Distinct from
service_description ("what are these documents about") the same way
collection_name is distinct from service_description (migration 009): one
column per distinct piece of user intent, never overloading one free-text
field for two purposes.

Stored rather than transient so a later regeneration or a corpus-grounded
refinement pass (docs/plan/field-and-retrieval-precision/active-plan.md,
Phase 1b) can re-read what the user originally asked for, and so a generated
schema is explainable after the fact instead of a black box.

Run from the repo root:
    python migrations/051_add_extraction_instructions.py upgrade
    python migrations/051_add_extraction_instructions.py downgrade
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Ensure project root is importable when running directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Try to use app's env setup; fallback to dotenv/env directly if unavailable
try:
    from utils.setup_env import (
        POSTGRES_USER,
        POSTGRES_PASSWORD,
        POSTGRES_HOST,
        POSTGRES_PORT,
        POSTGRES_DB_NAME,
    )
except Exception:
    import os
    try:
        from dotenv import load_dotenv  # type: ignore
        mode = os.getenv("MODE", "LOCAL").upper()
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
    """Add extraction_instructions column to knowledge_config table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE knowledge_config ADD COLUMN IF NOT EXISTS extraction_instructions TEXT NULL"
        ))

    await engine.dispose()
    print("Added extraction_instructions column to knowledge_config table")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Remove extraction_instructions column from knowledge_config table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE knowledge_config DROP COLUMN IF EXISTS extraction_instructions"
        ))

    await engine.dispose()
    print("Removed extraction_instructions column from knowledge_config table")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 051_add_extraction_instructions.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
