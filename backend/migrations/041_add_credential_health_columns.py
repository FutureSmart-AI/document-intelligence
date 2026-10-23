"""Migration 041: add credential-health columns to ``owner_provider_keys``.

Until now a BYOK row carried ``is_valid``, written ``TRUE`` once at insert
(``utils/usage/credentials.py::add_or_update_credential``) and never updated by
anything. So a key that was revoked or expired at the provider still read as valid
forever, and the only code that actually tested a key against the provider
(``routers/usage_llm_alerts.py``) deliberately discarded its own result. The flag
existed but carried no information.

These three columns are what turn ``is_valid`` into a live signal:

``last_error_type``   why the key was last rejected — ``auth_error``, ``low_balance``,
                      ``rate_limit``, ``provider_error``… The distinction matters
                      because only ``auth_error`` and ``low_balance`` mean "this key
                      cannot be used"; a rate limit is transient and must NOT
                      permanently demote the key.
``last_error_at``     when that happened, so a dashboard can say "failed 2 hours ago"
                      rather than just "broken".
``last_validated_at`` when the key last provably worked. This is what makes lazy
                      re-validation possible: a read can decide the key is stale and
                      re-check it in the background instead of every request paying
                      for a live provider call.

All three are nullable with no backfill. Existing rows therefore read as "never
validated, never failed", which is honest — we genuinely do not know whether those
stored keys still work, and pretending otherwise by backfilling ``last_validated_at
= NOW()`` would suppress the first re-validation for every pre-existing key.

Run from the repo root:
    MODE=PROD python migrations/041_add_credential_health_columns.py upgrade
    MODE=PROD python migrations/041_add_credential_health_columns.py downgrade

Downgrade drops the three columns; ``is_valid`` survives (it predates this
migration) but goes back to being write-once. Any recorded failure reasons are lost.
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
    # Same env-selection convention as migrations 039/040 — MODE unset defaults to
    # DEV (staging), never .env.local (a mislabeled copy of prod in this repo).
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
        # IF NOT EXISTS on each — a re-run after a partial failure is a no-op.
        await conn.execute(text(
            "ALTER TABLE owner_provider_keys "
            "ADD COLUMN IF NOT EXISTS last_error_type VARCHAR(50)"
        ))
        await conn.execute(text(
            "ALTER TABLE owner_provider_keys "
            "ADD COLUMN IF NOT EXISTS last_error_at TIMESTAMP"
        ))
        await conn.execute(text(
            "ALTER TABLE owner_provider_keys "
            "ADD COLUMN IF NOT EXISTS last_validated_at TIMESTAMP"
        ))
        # Partial index: the re-validation sweep only ever looks for keys that are
        # currently usable, so indexing the invalid ones would be dead weight.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_owner_provider_keys_revalidation "
            "ON owner_provider_keys (last_validated_at) WHERE is_valid = TRUE"
        ))
        print("Added owner_provider_keys.last_error_type / last_error_at / last_validated_at.")
    await engine.dispose()


async def downgrade() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "DROP INDEX IF EXISTS idx_owner_provider_keys_revalidation"
        ))
        for column in ("last_validated_at", "last_error_at", "last_error_type"):
            await conn.execute(text(
                f"ALTER TABLE owner_provider_keys DROP COLUMN IF EXISTS {column}"
            ))
        print("Dropped the three owner_provider_keys credential-health columns.")
    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"upgrade", "downgrade"}:
        print("usage: python migrations/041_add_credential_health_columns.py [upgrade|downgrade]")
        sys.exit(2)
    asyncio.run(upgrade() if sys.argv[1] == "upgrade" else downgrade())
