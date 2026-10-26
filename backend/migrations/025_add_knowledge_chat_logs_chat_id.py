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
    """Add chat_id to knowledge_chat_logs.

    The Orchestrator's ChatResponse carries a per-turn chat_id distinct from
    session_id (used for regenerate_response / turn-level addressing).
    knowledge_chat_logs never had one (deliberately dropped as an
    Orchestrator/billing column per the table's docstring) — this adds it
    back as a server-generated UUID so the new endpoint's response can
    return one without changing the existing int `id` PK.
    """
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE knowledge_chat_logs "
            "ADD COLUMN IF NOT EXISTS chat_id UUID NOT NULL DEFAULT gen_random_uuid()"
        ))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_knowledge_chat_logs_chat_id "
            "ON knowledge_chat_logs(chat_id)"
        ))
    await engine.dispose()
    print("Added knowledge_chat_logs.chat_id column")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop chat_id column."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP INDEX IF EXISTS ix_knowledge_chat_logs_chat_id"))
        await conn.execute(text("ALTER TABLE knowledge_chat_logs DROP COLUMN IF EXISTS chat_id"))
    await engine.dispose()
    print("Dropped knowledge_chat_logs.chat_id column")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 025_add_knowledge_chat_logs_chat_id.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
