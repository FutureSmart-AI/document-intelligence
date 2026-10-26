"""Migration 031: Widget branding settings, per knowledge_id.

Creates `knowledge_widget_settings` — one row per knowledge_id, holding the
embeddable chat widget's branding: heading/sub_heading/greet_message/color/
show_sources/widget_icon/widget_avatar. Ported from fs-agent-dashboard's
`widgets_settings` table (there keyed by chatbot_id) — see
docs/plan/access-and-sharing/archive/design-widget-fullscreen.md (Piece 1). Read/written by
routers/widget_config.py via utils/db/widget_settings_ops.py.

Absence of a row for a given knowledge_id is the expected default case (the
widget falls back to hardcoded defaults), not an error.

Additive only: creates one new table, touches no existing data.

Run from the repo root:
    python migrations/031_create_knowledge_widget_settings.py upgrade
    python migrations/031_create_knowledge_widget_settings.py downgrade
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
    """Create knowledge_widget_settings table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS knowledge_widget_settings (
                knowledge_id   TEXT PRIMARY KEY,
                heading        TEXT DEFAULT 'Welcome',
                sub_heading    TEXT DEFAULT 'Got a Question? Just Ask!',
                greet_message  TEXT DEFAULT 'Hello! What''s up?',
                color          TEXT DEFAULT '#3a5fe3',
                show_sources   BOOLEAN NOT NULL DEFAULT FALSE,
                widget_icon    TEXT,
                widget_avatar  TEXT,
                created_at     TIMESTAMP DEFAULT now(),
                updated_at     TIMESTAMP,  -- NULL until first update after creation
                deleted        BOOLEAN NOT NULL DEFAULT FALSE
            )
            """
        ))
    await engine.dispose()
    print("Created knowledge_widget_settings table")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop knowledge_widget_settings table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS knowledge_widget_settings"))
    await engine.dispose()
    print("Dropped knowledge_widget_settings table")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 031_create_knowledge_widget_settings.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
