"""Migration 056: per-document processing telemetry on the document table.

``document.processing_telemetry`` (JSONB, nullable) holds what the worker
measured while processing the document: the parse step (parser, mode, ms),
each extraction call group (the model that actually served it, fallback and
reason, tokens incl. cached, ms), embeddings (model, tokens, ms) and the total.
It is written raw -- no prices -- and the API prices it on read with
chat/pricing.py (utils/processing_telemetry.py), so there is one price table.
Rows processed before this column stay NULL.

Additive and idempotent. RUN BEFORE deploying an API or worker image that maps
the column (SQLModel selects every mapped column).

Run from the repo root:
    python migrations/056_add_document_processing_telemetry.py upgrade
    python migrations/056_add_document_processing_telemetry.py downgrade

Downgrade drops the column and the stored telemetry with it.
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
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE document ADD COLUMN IF NOT EXISTS processing_telemetry JSONB"
        ))

    await engine.dispose()
    print("Added document.processing_telemetry")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE document DROP COLUMN IF EXISTS processing_telemetry"))

    await engine.dispose()
    print("Removed document.processing_telemetry")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 056_add_document_processing_telemetry.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
