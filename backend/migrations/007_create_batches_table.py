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
    """Create batches table to store batch metadata."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        # Create batches table if it doesn't exist
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS batches (
                batch_id VARCHAR(255) PRIMARY KEY,
                knowledge_id VARCHAR(255) NOT NULL,
                user_id VARCHAR(255),
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                status VARCHAR(50) DEFAULT 'processing',
                total_documents INTEGER DEFAULT 0
            )
            """
        ))
        
        # Create indexes
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_batches_knowledge_id ON batches(knowledge_id)"
        ))
        
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_batches_user_id ON batches(user_id)"
        ))
        
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_batches_status ON batches(status)"
        ))
        
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_batches_created_at ON batches(created_at)"
        ))
    
    await engine.dispose()
    print("Created batches table")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop batches table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        # Drop indexes
        await conn.execute(text(
            "DROP INDEX IF EXISTS ix_batches_created_at"
        ))
        
        await conn.execute(text(
            "DROP INDEX IF EXISTS ix_batches_status"
        ))
        
        await conn.execute(text(
            "DROP INDEX IF EXISTS ix_batches_user_id"
        ))
        
        await conn.execute(text(
            "DROP INDEX IF EXISTS ix_batches_knowledge_id"
        ))
        
        # Drop table
        await conn.execute(text(
            "DROP TABLE IF EXISTS batches"
        ))
    
    await engine.dispose()
    print("Dropped batches table")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 007_create_batches_table.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()

