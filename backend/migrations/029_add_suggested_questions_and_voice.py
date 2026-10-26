"""Migration 029: Add suggested_questions / voice toggles to
knowledge_chat_config (G10).

Closes G10 in docs/screen-api-flow-document-intelligence.md for two of its
three Settings controls with no backing field.

- suggested_questions: a JSONB array of strings the collection owner writes
  directly (starter prompts shown on an empty conversation). No generation
  logic involved — storing and returning editorial content the caller wrote
  is a complete feature on its own, unlike a "generate this for me" button.
- voice_input_enabled / voice_output_enabled: pure client-facing
  preferences. This service has NO speech-to-text or text-to-speech
  pipeline anywhere — these two gate nothing server-side. They only tell
  the dashboard whether to render mic/speaker controls, the same posture as
  a theme toggle. Storing a UI preference is not the accepted-but-ignored
  defect class (G11) that this file's sibling gaps warn about: G11 is a
  field that looks like it changes generation behavior and doesn't. These
  two never claimed to.

temperature (G11's actual field) is deliberately NOT touched here — that
gap needs the rag_node.py threading fix, not a schema change, and is out of
scope for this migration.

Additive only: three new columns on an existing table, no backfill needed —
suggested_questions defaults to an empty array, the two voice flags default
false, matching "off" as the safe default for a capability the dashboard
should not silently claim to support.

Run from the repo root:
    python migrations/029_add_suggested_questions_and_voice.py upgrade
    python migrations/029_add_suggested_questions_and_voice.py downgrade
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
    """Add suggested_questions / voice_input_enabled / voice_output_enabled columns."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            ALTER TABLE knowledge_chat_config
                ADD COLUMN IF NOT EXISTS suggested_questions JSONB NOT NULL DEFAULT '[]'::jsonb,
                ADD COLUMN IF NOT EXISTS voice_input_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS voice_output_enabled BOOLEAN NOT NULL DEFAULT FALSE
            """
        ))
    await engine.dispose()
    print("Added knowledge_chat_config.suggested_questions / voice_input_enabled / voice_output_enabled columns")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop the three columns."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            ALTER TABLE knowledge_chat_config
                DROP COLUMN IF EXISTS suggested_questions,
                DROP COLUMN IF EXISTS voice_input_enabled,
                DROP COLUMN IF EXISTS voice_output_enabled
            """
        ))
    await engine.dispose()
    print("Dropped knowledge_chat_config.suggested_questions / voice_input_enabled / voice_output_enabled columns")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 029_add_suggested_questions_and_voice.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
