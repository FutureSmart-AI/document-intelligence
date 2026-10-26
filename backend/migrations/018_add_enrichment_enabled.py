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
    """Add enrichment_enabled to knowledge_config.

    When False, a knowledge base with document_level_metadata=True still indexes the
    caller-provided extra_metadata into the filterable document point (so metadata
    filtering, source_id dedup and per-document retrieval work) but SKIPS the LLM
    enrichment call (worker Strategy 6b). Defaults to True so existing bases are
    unchanged.
    """
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE knowledge_config ADD COLUMN IF NOT EXISTS enrichment_enabled BOOLEAN DEFAULT true"
        ))
    await engine.dispose()
    print("Added knowledge_config.enrichment_enabled column")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop enrichment_enabled column."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE knowledge_config DROP COLUMN IF EXISTS enrichment_enabled"
        ))
    await engine.dispose()
    print("Dropped knowledge_config.enrichment_enabled column")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 018_add_enrichment_enabled.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
