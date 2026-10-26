"""Migration 020: Chat persistence tables for the Knowledge Service's own
chat endpoint (see claude-logs/plan/knowledge-owned-chat.md).

Creates `knowledge_chat_sessions` and `knowledge_chat_logs` — fresh tables,
fresh sessions. No migration of the Orchestrator's `chat_sessions` /
`chat_logs` rows (decided: new conversations only).

`knowledge_chat_logs.service_data` carries the grouped retrieval evidence
for the turn, read back next turn by chat/db/chat_logs.py::get_recent_evidence
as service_data["knowledge_evidence"] — this table is knowledge-only, so no
nested "knowledge" key is needed (unlike the Orchestrator's multi-service
chat_logs.service_data).

Additive only: creates two new tables, touches no existing data.

Run from the repo root:
    python migrations/020_create_knowledge_chat_tables.py upgrade
    python migrations/020_create_knowledge_chat_tables.py downgrade
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
    """Create knowledge_chat_sessions + knowledge_chat_logs tables + indexes."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS knowledge_chat_sessions (
                session_id     TEXT PRIMARY KEY,
                knowledge_id   TEXT NOT NULL,
                user_id        TEXT,
                session_title  TEXT,
                deleted        BOOLEAN NOT NULL DEFAULT FALSE,
                created_at     TIMESTAMP DEFAULT now(),
                updated_at     TIMESTAMP   -- NULL until first update after creation
            )
            """
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chat_sessions_knowledge_id "
            "ON knowledge_chat_sessions(knowledge_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chat_sessions_user_id "
            "ON knowledge_chat_sessions(user_id)"
        ))

        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS knowledge_chat_logs (
                id             SERIAL PRIMARY KEY,
                session_id     TEXT NOT NULL,
                knowledge_id   TEXT NOT NULL,
                user_id        TEXT,
                query          TEXT,
                response       TEXT,
                service_data   JSONB,       -- grouped retrieval evidence for this turn
                refined_query  TEXT,
                llm_provider   TEXT,
                model          TEXT,
                no_of_chunks   INTEGER DEFAULT 4,
                created_at     TIMESTAMP DEFAULT now()
            )
            """
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chat_logs_session_id "
            "ON knowledge_chat_logs(session_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chat_logs_knowledge_id "
            "ON knowledge_chat_logs(knowledge_id)"
        ))
    await engine.dispose()
    print("Created knowledge_chat_sessions and knowledge_chat_logs tables")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop knowledge_chat_logs + knowledge_chat_sessions tables."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP INDEX IF EXISTS ix_knowledge_chat_logs_knowledge_id"))
        await conn.execute(text("DROP INDEX IF EXISTS ix_knowledge_chat_logs_session_id"))
        await conn.execute(text("DROP TABLE IF EXISTS knowledge_chat_logs"))

        await conn.execute(text("DROP INDEX IF EXISTS ix_knowledge_chat_sessions_user_id"))
        await conn.execute(text("DROP INDEX IF EXISTS ix_knowledge_chat_sessions_knowledge_id"))
        await conn.execute(text("DROP TABLE IF EXISTS knowledge_chat_sessions"))
    await engine.dispose()
    print("Dropped knowledge_chat_logs and knowledge_chat_sessions tables")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 020_create_knowledge_chat_tables.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
