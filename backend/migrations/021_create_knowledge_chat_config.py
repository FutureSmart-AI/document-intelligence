"""Migration 021: Per-knowledge_id chat config for the Knowledge Service's
own chat endpoint (see claude-logs/plan/knowledge-owned-chat.md's Config
section, Phase 3d).

Creates `knowledge_chat_config` — one row per knowledge_id, keyed by
knowledge_id, holding system_prompt/llm_provider/llm_model/temperature/
show_sources/chat_enabled. This is level 2 of the 4-level resolution chain
(request body field > this row > env default > hardcoded default), read by
chat/config.py::resolve_chat_config. Absence of a row for a given
knowledge_id is the expected default case, not an error — the chain falls
through to env/hardcoded defaults.

Additive only: creates one new table, touches no existing data.

Run from the repo root:
    python migrations/021_create_knowledge_chat_config.py upgrade
    python migrations/021_create_knowledge_chat_config.py downgrade
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
    """Create knowledge_chat_config table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS knowledge_chat_config (
                knowledge_id   TEXT PRIMARY KEY,
                system_prompt  TEXT,
                llm_provider   TEXT DEFAULT 'openai',
                llm_model      TEXT DEFAULT 'gpt-5.4-mini',
                temperature    FLOAT DEFAULT 0.0,
                show_sources   BOOLEAN NOT NULL DEFAULT TRUE,
                chat_enabled   BOOLEAN NOT NULL DEFAULT TRUE,
                created_at     TIMESTAMP DEFAULT now(),
                updated_at     TIMESTAMP   -- NULL until first update after creation
            )
            """
        ))
    await engine.dispose()
    print("Created knowledge_chat_config table")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop knowledge_chat_config table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS knowledge_chat_config"))
    await engine.dispose()
    print("Dropped knowledge_chat_config table")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 021_create_knowledge_chat_config.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
