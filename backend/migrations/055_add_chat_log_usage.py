"""Migration 055: per-turn usage telemetry on knowledge_chat_logs.

``knowledge_chat_logs.usage`` (JSONB, nullable) stores the turn's ``usage`` block
exactly as the chat API returned/streamed it (chat/run_usage.py): server latency,
per-operation provider/model/tokens/latency, estimated cost, cost_complete,
unpriced_models and the price-table version. ``GET /v1/{knowledge_id}/sessions/
{session_id}`` returns it on each assistant message so a reloaded thread shows
what every step took and cost. Rows written before this column stay NULL.

Additive and idempotent. RUN BEFORE deploying an API image that maps the column
(SQLModel selects every mapped column, so an image ahead of the schema fails
every chat-log read).

Run from the repo root:
    python migrations/055_add_chat_log_usage.py upgrade
    python migrations/055_add_chat_log_usage.py downgrade

Downgrade drops the column and the stored usage blocks with it.
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
            "ALTER TABLE knowledge_chat_logs ADD COLUMN IF NOT EXISTS usage JSONB"
        ))

    await engine.dispose()
    print("Added knowledge_chat_logs.usage")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE knowledge_chat_logs DROP COLUMN IF EXISTS usage"))

    await engine.dispose()
    print("Removed knowledge_chat_logs.usage")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 055_add_chat_log_usage.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
