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
        # Rename columns if present
        await conn.execute(text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'field_configurations' AND column_name = 'is_enabled'
                ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'field_configurations' AND column_name = 'is_active'
                ) THEN
                    ALTER TABLE field_configurations RENAME COLUMN is_enabled TO is_active;
                END IF;
            END$$;
            """
        ))

        await conn.execute(text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'field_configurations' AND column_name = 'is_system_locked'
                ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'field_configurations' AND column_name = 'is_required'
                ) THEN
                    ALTER TABLE field_configurations RENAME COLUMN is_system_locked TO is_required;
                END IF;
            END$$;
            """
        ))

        # Add new columns if missing
        await conn.execute(text(
            "ALTER TABLE field_configurations ADD COLUMN IF NOT EXISTS level VARCHAR(32) DEFAULT 'document'"
        ))
        await conn.execute(text(
            "ALTER TABLE field_configurations ADD COLUMN IF NOT EXISTS deprecated_at TIMESTAMPTZ NULL"
        ))

    await engine.dispose()


async def downgrade() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        # Drop v2 columns if present
        await conn.execute(text(
            "ALTER TABLE field_configurations DROP COLUMN IF EXISTS level"
        ))
        await conn.execute(text(
            "ALTER TABLE field_configurations DROP COLUMN IF EXISTS deprecated_at"
        ))

        # Rename columns back if appropriate
        await conn.execute(text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'field_configurations' AND column_name = 'is_active'
                ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'field_configurations' AND column_name = 'is_enabled'
                ) THEN
                    ALTER TABLE field_configurations RENAME COLUMN is_active TO is_enabled;
                END IF;
            END$$;
            """
        ))

        await conn.execute(text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'field_configurations' AND column_name = 'is_required'
                ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'field_configurations' AND column_name = 'is_system_locked'
                ) THEN
                    ALTER TABLE field_configurations RENAME COLUMN is_required TO is_system_locked;
                END IF;
            END$$;
            """
        ))

    await engine.dispose()


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 001_field_configurations_v2.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
        print("Migration upgrade completed.")
    else:
        asyncio.run(downgrade())
        print("Migration downgrade completed.")


if __name__ == "__main__":
    main()


