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
        # Add deleted column if not exists
        await conn.execute(text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'field_configurations' AND column_name = 'deleted'
                ) THEN
                    ALTER TABLE field_configurations 
                    ADD COLUMN deleted BOOLEAN NOT NULL DEFAULT FALSE;
                END IF;
            END$$;
            """
        ))

        # Add deleted_at column if not exists
        await conn.execute(text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'field_configurations' AND column_name = 'deleted_at'
                ) THEN
                    ALTER TABLE field_configurations 
                    ADD COLUMN deleted_at TIMESTAMPTZ NULL;
                END IF;
            END$$;
            """
        ))

        # Create index on deleted column if not exists
        await conn.execute(text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_indexes
                    WHERE tablename = 'field_configurations' AND indexname = 'ix_field_configurations_deleted'
                ) THEN
                    CREATE INDEX ix_field_configurations_deleted ON field_configurations(deleted);
                END IF;
            END$$;
            """
        ))

        # Set deleted = FALSE for any existing rows that might have NULL (shouldn't happen but safe)
        await conn.execute(text(
            "UPDATE field_configurations SET deleted = FALSE WHERE deleted IS NULL"
        ))

    await engine.dispose()


async def downgrade() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        # Drop index if exists
        await conn.execute(text(
            "DROP INDEX IF EXISTS ix_field_configurations_deleted"
        ))

        # Drop deleted_at column if exists
        await conn.execute(text(
            "ALTER TABLE field_configurations DROP COLUMN IF EXISTS deleted_at"
        ))

        # Drop deleted column if exists
        await conn.execute(text(
            "ALTER TABLE field_configurations DROP COLUMN IF EXISTS deleted"
        ))

    await engine.dispose()


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 003_field_configurations_soft_delete.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
        print("Migration upgrade completed.")
    else:
        asyncio.run(downgrade())
        print("Migration downgrade completed.")


if __name__ == "__main__":
    main()

