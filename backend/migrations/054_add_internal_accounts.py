"""Migration 054: internal accounts.

``internal_accounts`` holds one row per account that has signed in with a VERIFIED
email on an internal domain (futuresmart.ai, aidemos.com). ``get_owner_limits``
reads it: such an account has no trial ceilings. Rows are written only by
``utils/usage/internal_accounts.record_verified_session`` from the auth service's
validated claims, never from a request body. Usage events are untouched: internal
usage is still recorded, and reported separately by GET /v1/usage/internal-accounts.

Additive and idempotent; an image without the reader simply never looks at it.

Run from the repo root:
    python migrations/054_add_internal_accounts.py upgrade
    python migrations/054_add_internal_accounts.py downgrade

Downgrade drops the table: every internal account returns to its normal limits.
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
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS internal_accounts (
                owner_id VARCHAR(320) PRIMARY KEY,
                first_verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_verified_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ))

    await engine.dispose()
    print("Added internal_accounts")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS internal_accounts"))

    await engine.dispose()
    print("Removed internal_accounts")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 054_add_internal_accounts.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
