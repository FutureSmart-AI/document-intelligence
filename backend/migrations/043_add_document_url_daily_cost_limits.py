"""Migration 043: add four platform-limit columns to ``owner_platform_limits``.

Four new nullable ceilings, all additive/idempotent (``IF NOT EXISTS`` +
named CHECK constraints), same shape as migration 040:

- ``max_documents`` — standing cap, counts ``document`` rows with
  ``content_type='file'`` (uploads, ClickUp-synced pages included — see
  ``_derive_document_source`` in ``utils/db/knowledge_ops.py``).
- ``max_urls`` — standing cap, counts ``document`` rows with
  ``content_type='url'`` (one row per weblink *added*, not per crawled child page).
- ``max_messages_per_day`` — a second, tighter window alongside the existing
  ``max_messages_per_month``; resets at 00:00 UTC.
- ``max_cost_usd_per_month`` — a dollar ceiling on the same estimated-cost figure
  the Usage screen already reports. Reporting-only: nothing calls
  ``enforce_operation`` for it (see the field's docstring on
  ``OwnerPlatformLimits``), so it can appear on the Limits tab without ever
  refusing a request.

``max_documents``/``max_urls`` are sub-caps of the existing ``max_knowledge_items``,
not new totals — that combined ceiling still applies regardless of these two.

Existing rows keep every new column ``NULL`` (unlimited) until an operator or the
defaults YAML (``utils/config/usage/owner_defaults.yaml``) writes a real value —
same rollout shape as migration 040: this is the schema half, not the
enforcement-goes-live half.

Run from the repo root:
    MODE=PROD python migrations/043_add_document_url_daily_cost_limits.py upgrade
    MODE=PROD python migrations/043_add_document_url_daily_cost_limits.py downgrade

Downgrade drops all four columns — any values written into them are lost. Standard.
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
    # Same env-selection convention as migration 039/040 — MODE unset defaults to DEV
    # (staging), never .env.local (which in this repo is a mislabeled copy of prod).
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

_COLUMNS = [
    ("max_documents", "INTEGER"),
    ("max_urls", "INTEGER"),
    ("max_messages_per_day", "INTEGER"),
    ("max_cost_usd_per_month", "DOUBLE PRECISION"),
]


async def upgrade() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        for name, sql_type in _COLUMNS:
            await conn.execute(text(
                f"ALTER TABLE owner_platform_limits ADD COLUMN IF NOT EXISTS {name} {sql_type}"
            ))
            await conn.execute(text(
                f"ALTER TABLE owner_platform_limits "
                f"ADD CONSTRAINT ck_owner_platform_limits_{name}_nonneg "
                f"CHECK ({name} IS NULL OR {name} >= 0)"
            ))
            print(f"Added owner_platform_limits.{name} (nullable, non-negative).")
    await engine.dispose()


async def downgrade() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        for name, _ in _COLUMNS:
            await conn.execute(text(
                f"ALTER TABLE owner_platform_limits DROP CONSTRAINT IF EXISTS ck_owner_platform_limits_{name}_nonneg"
            ))
            await conn.execute(text(
                f"ALTER TABLE owner_platform_limits DROP COLUMN IF EXISTS {name}"
            ))
            print(f"Dropped owner_platform_limits.{name}.")
    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"upgrade", "downgrade"}:
        print("usage: python migrations/043_add_document_url_daily_cost_limits.py [upgrade|downgrade]")
        sys.exit(2)
    asyncio.run(upgrade() if sys.argv[1] == "upgrade" else downgrade())
