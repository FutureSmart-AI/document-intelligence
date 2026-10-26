"""Migration 030: Add max_tokens / include_charts / show_raw_response to
knowledge_chat_config, and max_tokens to knowledge_chat_node_config.

Closes the three Settings controls from the "what answers can include" mock
that had no backing field: Max tokens, "Include charts when useful", "Show
raw response".

- max_tokens: knowledge-level default (mirrors temperature's column) plus a
  per-node override on knowledge_chat_node_config. Unlike temperature (G11,
  see docs/product/screen-api-flow-document-intelligence.md), this one is
  actually threaded into generate_rag_answer's get_llm call
  (chat/rag_agent/answer_node.py) rather than left inert — introducing a new
  field with the same accepted-but-ignored defect G11 already warns about
  would just grow that defect class instead of avoiding it.
- include_charts: boolean, default TRUE. Threaded into the generate_answer
  node's system prompt as a behavioral instruction (chat/rag_node.py), not a
  new structured ChatResponse field — the structured chart_output shape is
  still the open product gap G16 (docs/product/screen-api-flow-document-
  intelligence.md), unchanged by this migration.
- show_raw_response: boolean, default TRUE. Gates whether debug_info is
  included in the client-facing chat response, same mechanism show_sources
  already uses for relevant_sources/summaries/detailed_chunks
  (routers/chat.py::_build_response_payload). Defaults TRUE (not the mock's
  unchecked state) so existing callers keep receiving debug_info unless a
  collection owner opts out — the mock's unchecked default is a UI/product
  choice, not something this migration should silently turn into a breaking
  change for every existing integration.

Additive only: three new columns on knowledge_chat_config, one new column on
knowledge_chat_node_config, no backfill needed.

Run from the repo root:
    python migrations/030_add_max_tokens_charts_raw_response.py upgrade
    python migrations/030_add_max_tokens_charts_raw_response.py downgrade
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
    """Add max_tokens/include_charts/show_raw_response columns."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            ALTER TABLE knowledge_chat_config
                ADD COLUMN IF NOT EXISTS max_tokens INTEGER DEFAULT 1024,
                ADD COLUMN IF NOT EXISTS include_charts BOOLEAN NOT NULL DEFAULT TRUE,
                ADD COLUMN IF NOT EXISTS show_raw_response BOOLEAN NOT NULL DEFAULT TRUE
            """
        ))
        await conn.execute(text(
            """
            ALTER TABLE knowledge_chat_node_config
                ADD COLUMN IF NOT EXISTS max_tokens INTEGER
            """
        ))
    await engine.dispose()
    print("Added knowledge_chat_config.max_tokens / include_charts / show_raw_response columns")
    print("Added knowledge_chat_node_config.max_tokens column")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop the four columns."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            ALTER TABLE knowledge_chat_config
                DROP COLUMN IF EXISTS max_tokens,
                DROP COLUMN IF EXISTS include_charts,
                DROP COLUMN IF EXISTS show_raw_response
            """
        ))
        await conn.execute(text(
            """
            ALTER TABLE knowledge_chat_node_config
                DROP COLUMN IF EXISTS max_tokens
            """
        ))
    await engine.dispose()
    print("Dropped knowledge_chat_config.max_tokens / include_charts / show_raw_response columns")
    print("Dropped knowledge_chat_node_config.max_tokens column")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 030_add_max_tokens_charts_raw_response.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
