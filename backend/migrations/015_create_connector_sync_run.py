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
    """Create connector_sync_run table.

    One row per sync run for UI progress polling and an audit trail. Stats are
    folded from the crawl outcomes (indexed/skipped/errors) and reconcile (deleted).
    status: queued -> running -> completed | failed.
    """
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS connector_sync_run (
                id            SERIAL PRIMARY KEY,
                run_id        VARCHAR(64)  NOT NULL,
                knowledge_id  VARCHAR(255) NOT NULL,
                source        VARCHAR(64)  NOT NULL DEFAULT 'clickup',
                status        VARCHAR(32)  NOT NULL DEFAULT 'queued',
                trigger       VARCHAR(32)  NOT NULL DEFAULT 'manual',
                indexed       INTEGER      NOT NULL DEFAULT 0,
                skipped       INTEGER      NOT NULL DEFAULT 0,
                errors        INTEGER      NOT NULL DEFAULT 0,
                deleted       INTEGER      NOT NULL DEFAULT 0,
                error_detail  TEXT,
                started_at    TIMESTAMPTZ,
                completed_at  TIMESTAMPTZ,
                created_at    TIMESTAMPTZ  DEFAULT CURRENT_TIMESTAMP
            )
            """
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_connector_sync_run_run_id "
            "ON connector_sync_run(run_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_connector_sync_run_knowledge "
            "ON connector_sync_run(knowledge_id, source)"
        ))
    await engine.dispose()
    print("Created connector_sync_run table")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop connector_sync_run table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP INDEX IF EXISTS ix_connector_sync_run_knowledge"))
        await conn.execute(text("DROP INDEX IF EXISTS ix_connector_sync_run_run_id"))
        await conn.execute(text("DROP TABLE IF EXISTS connector_sync_run"))
    await engine.dispose()
    print("Dropped connector_sync_run table")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 015_create_connector_sync_run.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
