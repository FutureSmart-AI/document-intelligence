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
    """Add batch_id column to document table for batch document processing."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        # Add batch_id column if it doesn't exist
        await conn.execute(text(
            "ALTER TABLE document ADD COLUMN IF NOT EXISTS batch_id VARCHAR(255) NULL"
        ))
        
        # Add index on batch_id if it doesn't exist
        await conn.execute(text(
            """
            CREATE INDEX IF NOT EXISTS ix_document_batch_id 
            ON document(batch_id)
            WHERE batch_id IS NOT NULL
            """
        ))
    
    await engine.dispose()
    print("Added batch_id column to document table")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Remove batch_id column from document table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        # Drop index on batch_id
        await conn.execute(text(
            "DROP INDEX IF EXISTS ix_document_batch_id"
        ))
        
        # Remove batch_id column
        await conn.execute(text(
            "ALTER TABLE document DROP COLUMN IF EXISTS batch_id"
        ))
    
    await engine.dispose()
    print("Removed batch_id column from document table")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 006_add_batch_document_fields.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()

