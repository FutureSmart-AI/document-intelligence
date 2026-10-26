"""Migration 053: sample collections.

A sample collection is an ordinary knowledge base — documents, extracted fields,
vectors, chat config — that every signed-in account may open read-only. One
shared copy per sample, not one per user: extraction and embedding happen once
at provisioning, a visitor's questions only ever read it, and nobody but the
master key can change it (utils/auth/sample_access.py).

1. ``knowledge_config.sample_key`` — NULL for every normal collection. A
   non-NULL value ("resumes", "invoices") marks the collection as the live copy
   of that sample. Unique among non-deleted rows, so re-provisioning a sample
   has to retire the old copy first and the UI never sees two "resumes".

2. ``knowledge_sample_questions`` — one row per question a visitor asked a
   sample, written before the answer runs. It is the visitor's allowance (per
   day, per lifetime) and the global daily ceiling, and it is the adoption
   record: who tried which sample, when. Sample questions are charged to the
   sample's owner in the usage tables, so without this table they would be
   invisible per visitor.

Run from the repo root:
    python migrations/053_add_sample_collections.py upgrade
    python migrations/053_add_sample_collections.py downgrade
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
            "ALTER TABLE knowledge_config ADD COLUMN IF NOT EXISTS sample_key VARCHAR(64) NULL"
        ))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_config_sample_key "
            "ON knowledge_config (sample_key) WHERE sample_key IS NOT NULL AND deleted = false"
        ))
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS knowledge_sample_questions (
                id BIGSERIAL PRIMARY KEY,
                knowledge_id VARCHAR NOT NULL,
                user_email VARCHAR(320) NOT NULL,
                audience VARCHAR(16) NOT NULL,
                session_id VARCHAR NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_sample_questions_user "
            "ON knowledge_sample_questions (user_email, created_at)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_sample_questions_created "
            "ON knowledge_sample_questions (created_at)"
        ))

    await engine.dispose()
    print("Added knowledge_config.sample_key and knowledge_sample_questions")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS knowledge_sample_questions"))
        await conn.execute(text("DROP INDEX IF EXISTS uq_knowledge_config_sample_key"))
        await conn.execute(text("ALTER TABLE knowledge_config DROP COLUMN IF EXISTS sample_key"))

    await engine.dispose()
    print("Removed sample collections schema")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 053_add_sample_collections.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
