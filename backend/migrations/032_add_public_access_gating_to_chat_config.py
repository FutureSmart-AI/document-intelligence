"""Migration 032: Public-access gating for the widget and full-screen chat
surfaces, on knowledge_chat_config.

Adds four columns, ported from fs-agent-chat's agent_params (see
docs/plan/access-and-sharing/archive/design-widget-fullscreen.md, Piece 2):

- is_public / allowed_domains: gate an anonymous widget chat call.
- fullscreen_is_public / fullscreen_allowed_domains: gate an anonymous
  full-screen shareable chat call. Fully independent of the widget pair —
  neither inherits the other's toggle or allowlist.

Both default to NOT public / no allowlist (empty = "any origin" once public),
so every existing knowledge base stays exactly as un-embeddable as before
this migration until an owner opts in explicitly. This differs from
fs-agent-chat's own migration, which backfilled fullscreen_is_public from the
pre-existing is_public for already-public agents — there is no equivalent
"already public" state here since this gating did not exist in this repo
before now.

Additive only: four new columns, no backfill needed.

Run from the repo root:
    python migrations/032_add_public_access_gating_to_chat_config.py upgrade
    python migrations/032_add_public_access_gating_to_chat_config.py downgrade
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
    """Add is_public/allowed_domains/fullscreen_is_public/fullscreen_allowed_domains."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            ALTER TABLE knowledge_chat_config
                ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS allowed_domains JSONB,
                ADD COLUMN IF NOT EXISTS fullscreen_is_public BOOLEAN NOT NULL DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS fullscreen_allowed_domains JSONB
            """
        ))
    await engine.dispose()
    print("Added knowledge_chat_config.is_public / allowed_domains / fullscreen_is_public / fullscreen_allowed_domains columns")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop the four columns."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            ALTER TABLE knowledge_chat_config
                DROP COLUMN IF EXISTS is_public,
                DROP COLUMN IF EXISTS allowed_domains,
                DROP COLUMN IF EXISTS fullscreen_is_public,
                DROP COLUMN IF EXISTS fullscreen_allowed_domains
            """
        ))
    await engine.dispose()
    print("Dropped knowledge_chat_config public-access gating columns")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 032_add_public_access_gating_to_chat_config.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
