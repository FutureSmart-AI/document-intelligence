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
    """Add feedback to knowledge_chat_logs.

    Per-turn 👍/👎 on an answer (G13 — the donor's ChatLogs.feedback was
    deliberately dropped when this table was ported, db_tables.py:307; the
    live dashboard already ships a `user_feedback` field, currently always
    null, docs/screen-api-flow-document-intelligence.md §8e). A CHECK
    constraint keeps the column to exactly the two values the API accepts
    (or NULL for "no feedback given") rather than an open string.
    """
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE knowledge_chat_logs ADD COLUMN IF NOT EXISTS feedback VARCHAR(10)"
        ))
        await conn.execute(text(
            "ALTER TABLE knowledge_chat_logs DROP CONSTRAINT IF EXISTS knowledge_chat_logs_feedback_check"
        ))
        await conn.execute(text(
            "ALTER TABLE knowledge_chat_logs ADD CONSTRAINT knowledge_chat_logs_feedback_check "
            "CHECK (feedback IS NULL OR feedback IN ('positive', 'negative'))"
        ))
    await engine.dispose()
    print("Added knowledge_chat_logs.feedback column")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop feedback column."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE knowledge_chat_logs DROP COLUMN IF EXISTS feedback"
        ))
    await engine.dispose()
    print("Dropped knowledge_chat_logs.feedback column")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 027_add_knowledge_chat_logs_feedback.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
