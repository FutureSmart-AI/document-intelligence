"""Migration 040: add ``owner_platform_limits.max_knowledge_items``.

Adds one new nullable integer column to the account-wide platform-limits table plus
its non-negative CHECK constraint. Named ``max_knowledge_items``, not
``max_documents``, because a "knowledge item" is any ingested unit — an uploaded
file, a file-by-URL, a pasted text, a scraped weblink, a crawled child page, or a
synced connector page — i.e. one row in the ``document`` table whatever its source.
"documents" reads as "uploaded files" and would undercount the other five.

Deliberately NOT ``_per_month``: "any knowledge ingest" includes scheduled connector
syncs, so a monthly allowance would be re-consumed on every sync run and the number
would measure sync frequency rather than a user choice. See
``docs/plan/usage-and-limits/decisions.md``.

Existing rows keep ``max_knowledge_items = NULL``, which the limit-checker already
treats as unlimited (``utils/usage/limit_checking.py::check_operation_allowed`` skips
a limit whose ceiling is ``None``). So this migration alone changes nothing observable
about live traffic — every account stays unlimited until an operator writes a real
value into a row. That is intentional: this is the schema half of the rollout, not the
enforcement half.

New accounts still pick up the default from ``utils/config/usage/owner_defaults.yaml``
(``max_knowledge_items: 500``) when their row is seeded via
``utils/usage/owner_config.py``.

Run from the repo root:
    MODE=PROD python migrations/040_add_max_knowledge_items_limit.py upgrade
    MODE=PROD python migrations/040_add_max_knowledge_items_limit.py downgrade

Downgrade drops the column — any values written into it are lost. Standard.
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
    # Same env-selection convention as migration 039 — MODE unset defaults to DEV
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


async def upgrade() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        # IF NOT EXISTS on both — running the migration twice is a no-op, which is
        # how a re-run after a partial failure stays safe.
        await conn.execute(text(
            "ALTER TABLE owner_platform_limits "
            "ADD COLUMN IF NOT EXISTS max_knowledge_items INTEGER"
        ))
        # Named constraint so an accidental duplicate ADD would fail cleanly rather
        # than silently accrete a second copy of the check.
        await conn.execute(text(
            "ALTER TABLE owner_platform_limits "
            "ADD CONSTRAINT ck_owner_platform_limits_max_knowledge_items_nonneg "
            "CHECK (max_knowledge_items IS NULL OR max_knowledge_items >= 0)"
        ))
        print("Added owner_platform_limits.max_knowledge_items (nullable, non-negative).")
    await engine.dispose()


async def downgrade() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE owner_platform_limits "
            "DROP CONSTRAINT IF EXISTS ck_owner_platform_limits_max_knowledge_items_nonneg"
        ))
        await conn.execute(text(
            "ALTER TABLE owner_platform_limits DROP COLUMN IF EXISTS max_knowledge_items"
        ))
        print("Dropped owner_platform_limits.max_knowledge_items.")
    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"upgrade", "downgrade"}:
        print("usage: python migrations/040_add_max_knowledge_items_limit.py [upgrade|downgrade]")
        sys.exit(2)
    asyncio.run(upgrade() if sys.argv[1] == "upgrade" else downgrade())
