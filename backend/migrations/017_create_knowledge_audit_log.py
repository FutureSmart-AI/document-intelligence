"""Migration 017: Universal access-audit logging for the knowledge service.

Creates `knowledge_audit_log` — one standardized row per dashboard-initiated
request (user_id actor, owner_id, knowledge_id, action, method, path, created_at),
written by the `audit_access` dependency attached at every router mount in main.py.

Every endpoint is audited, including the high-volume `search` route — coverage was
chosen over noise (the table is append-only and indexed).

Additive only: creates one new table, touches no existing data.

Run from the repo root:
    python migrations/017_create_knowledge_audit_log.py upgrade
    python migrations/017_create_knowledge_audit_log.py downgrade
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
    """Create knowledge_audit_log table + indexes."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS knowledge_audit_log (
                id            SERIAL PRIMARY KEY,
                user_id       TEXT,            -- authenticated actor; "master_api_key" for master-key calls; NULL if unauthenticated
                owner_id      TEXT,            -- resource owner the request concerns (distinct from actor)
                knowledge_id  TEXT,            -- knowledge base the request targets (same id space as usage service's service_id)
                action        TEXT NOT NULL,   -- operation = route handler name (HTTP verb is in `method`)
                method        TEXT,
                path          TEXT,
                created_at    TIMESTAMPTZ DEFAULT now()   -- timezone-aware, matching every other table in this DB
            )
            """
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_audit_user_id ON knowledge_audit_log(user_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_audit_owner_id ON knowledge_audit_log(owner_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_audit_knowledge_id ON knowledge_audit_log(knowledge_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_audit_created_at ON knowledge_audit_log(created_at)"
        ))
    await engine.dispose()
    print("Created knowledge_audit_log table")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop knowledge_audit_log table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP INDEX IF EXISTS ix_knowledge_audit_created_at"))
        await conn.execute(text("DROP INDEX IF EXISTS ix_knowledge_audit_knowledge_id"))
        await conn.execute(text("DROP INDEX IF EXISTS ix_knowledge_audit_owner_id"))
        await conn.execute(text("DROP INDEX IF EXISTS ix_knowledge_audit_user_id"))
        await conn.execute(text("DROP TABLE IF EXISTS knowledge_audit_log"))
    await engine.dispose()
    print("Dropped knowledge_audit_log table")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 017_create_knowledge_audit_log.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
