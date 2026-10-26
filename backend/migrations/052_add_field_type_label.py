"""Migration 052: add field_type_label column to field_configurations.

Stores the human-facing label for a field's type — "Table", "Date", "Number" —
alongside the stored `field_type`, which is a Python type annotation and the key
of the enrichment worker's type map (`List[dict]`, `str`, ...).

Why a stored column rather than deriving it from `field_type` on every read:

  1. **The map cannot be inverted.** `int` and `float` both read "Number", so a
     label does not identify a type. Deriving one direction is fine; the moment a
     user edits a label, only the LLM resolver can map it back.
  2. **A label a user has already seen should not change under them.** Editing
     the default map (utils/models/enums.py::FIELD_TYPE_LABELS) would otherwise
     retroactively relabel every existing field.
  3. **The label is user-editable data.** In the settings flow a user may reword
     it; that text is then what the resolver reads to re-derive `field_type`.
     Data a user can edit belongs in a column, not in a lookup.

NULL is meaningful and expected: every row predating this migration has no
stored label, and the response layer falls back to the default map for those.
The migration deliberately does NOT backfill — a backfilled label is
indistinguishable from one a user chose, and the fallback already renders those
rows correctly.

Run from the repo root:
    python migrations/052_add_field_type_label.py upgrade
    python migrations/052_add_field_type_label.py downgrade
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Ensure project root is importable when running directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Try to use app's env setup; fallback to dotenv/env directly if unavailable
try:
    from utils.setup_env import (
        POSTGRES_USER,
        POSTGRES_PASSWORD,
        POSTGRES_HOST,
        POSTGRES_PORT,
        POSTGRES_DB_NAME,
    )
except Exception:
    import os
    try:
        from dotenv import load_dotenv  # type: ignore
        mode = os.getenv("MODE", "LOCAL").upper()
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
    """Add field_type_label column to field_configurations table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE field_configurations ADD COLUMN IF NOT EXISTS field_type_label VARCHAR(64) NULL"
        ))

    await engine.dispose()
    print("Added field_type_label column to field_configurations table")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Remove field_type_label column from field_configurations table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE field_configurations DROP COLUMN IF EXISTS field_type_label"
        ))

    await engine.dispose()
    print("Removed field_type_label column from field_configurations table")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 052_add_field_type_label.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
