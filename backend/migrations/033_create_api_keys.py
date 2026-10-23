"""Migration 033: Create api_keys table for per-user/per-knowledge-base API key generation.

Ported from fs-agent-dashboard's api_key_management/ (see
docs/plan/access-and-sharing/archive/design-api-keys.md for the full migration design),
hardened per that plan's confirmed decisions: upstream stored the raw key in
plaintext (`str(uuid.uuid4())` in a single `api_key` column); here only a
salted hash (`key_hash`) is stored, plus a short `key_prefix` (first 8 chars
of the raw key) so a key can be identified/displayed in a list UI without
ever re-exposing or re-deriving the raw value.

`id` is a plain autoincrement integer (SERIAL), matching every other table
in this DB (see migration/026-030 and utils/models/db_tables.py) rather than
a UUID primary key.

`key_hash` is UNIQUE (not `api_key`, which upstream had) since hash lookups
are how the new auth dependency (a later step in this task) will resolve an
incoming `x-api-key` header to a row.

Run from the repo root:
    python migrations/033_create_api_keys.py upgrade
    python migrations/033_create_api_keys.py downgrade
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
    """Create the api_keys table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                knowledge_id TEXT,
                key_prefix VARCHAR(8) NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                revoked_at TIMESTAMPTZ
            )
            """
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys (user_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys (key_hash)"
        ))
    await engine.dispose()
    print("Created api_keys table with idx_api_keys_user_id / idx_api_keys_key_hash indexes")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop the api_keys table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS api_keys"))
    await engine.dispose()
    print("Dropped api_keys table")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 033_create_api_keys.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
