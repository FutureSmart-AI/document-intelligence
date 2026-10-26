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
    """Add has_attachment/attachment_url to knowledge_chat_logs.

    Vision option 1 (see claude-logs/plan/chat-migration/knowledge-owned-chat.md's
    "Vision input" section): the Knowledge Service never receives raw image
    bytes — the caller (Orchestrator) sends a description-enriched query when
    an image was attached to the turn upstream. These two columns just record
    that the turn HAD an attachment, for transcript completeness; they are
    never read back into the retrieval/answer path.
    """
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE knowledge_chat_logs ADD COLUMN IF NOT EXISTS has_attachment BOOLEAN DEFAULT false"
        ))
        await conn.execute(text(
            "ALTER TABLE knowledge_chat_logs ADD COLUMN IF NOT EXISTS attachment_url TEXT"
        ))
    await engine.dispose()
    print("Added knowledge_chat_logs.has_attachment, knowledge_chat_logs.attachment_url columns")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop has_attachment/attachment_url columns."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE knowledge_chat_logs DROP COLUMN IF EXISTS has_attachment"
        ))
        await conn.execute(text(
            "ALTER TABLE knowledge_chat_logs DROP COLUMN IF EXISTS attachment_url"
        ))
    await engine.dispose()
    print("Dropped knowledge_chat_logs.has_attachment, knowledge_chat_logs.attachment_url columns")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 022_add_knowledge_chat_logs_attachment.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
