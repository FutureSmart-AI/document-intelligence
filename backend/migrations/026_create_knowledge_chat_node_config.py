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
    """Create knowledge_chat_node_config table.

    Per-node LLM config for one knowledge_id — the DB tier between the
    request body's node_configs and the flat knowledge_chat_config row (see
    claude-logs/plan/chat-migration/knowledge-owned-chat.md's "Per-node model
    + prompt config" section).

    Every override column is NULLABLE WITH NO DEFAULT on purpose: null means
    "inherit from knowledge_chat_config / env / hardcoded", so a row that sets
    only `prompt` still picks up provider and model from the level below. A
    column default here would turn every inherit into a silent override.
    """
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS knowledge_chat_node_config (
                knowledge_id  TEXT NOT NULL,
                node_name     TEXT NOT NULL,
                provider      TEXT,
                model         TEXT,
                temperature   DOUBLE PRECISION,
                prompt        TEXT,
                enabled       BOOLEAN NOT NULL DEFAULT true,
                created_at    TIMESTAMP DEFAULT now(),
                updated_at    TIMESTAMP,
                PRIMARY KEY (knowledge_id, node_name)
            )
            """
        ))
        # Every read is "all nodes for this knowledge_id" (the resolver loads
        # the whole set once per turn rather than one query per node), so the
        # PK's leading column already serves it — this index is for the
        # occasional cross-knowledge sweep by node, e.g. "which knowledge
        # bases pin rag_agent to a specific model".
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_chat_node_config_node_name "
            "ON knowledge_chat_node_config (node_name)"
        ))
    await engine.dispose()
    print("Created knowledge_chat_node_config table")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop knowledge_chat_node_config table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS knowledge_chat_node_config"))
    await engine.dispose()
    print("Dropped knowledge_chat_node_config table")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 026_create_knowledge_chat_node_config.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
