"""Migration 047: add langsmith_run_id to knowledge_enrichment_events.

WHY: enrichment cost is currently a LOCAL estimate and chat cost is not, and
there is no principled reason for the asymmetry — only history. The LangSmith
cost finalizer landed on this service after the enrichment analytics feature was
already built (both today), so enrichment kept pricing itself from
`usage_service_pricing` via CostCalculator at read time and nobody revisited it.

That estimate is wrong in two ways this session demonstrated:

  * It is only as fresh as config/usage/model_prices.yaml, which goes stale
    SILENTLY. `gemini-3.1-flash-lite` was absent from the catalog entirely, so
    every document it enriched reported cost_usd NULL while appearing in the
    by_model block as the cheapest option on offer. DeepSeek's figures are ~3x
    stale for the same reason.
  * Where the same comparison could be made on the chat side, the local estimate
    ran 2.9x high on a real turn (0.0286 estimated against 0.00980544 actual).
    Enrichment has no equivalent correction at all.

LangSmith prices from what the provider actually billed, so it is right by
construction rather than right until a price changes.

This column is the join key that makes that possible, exactly as migration 045
did for knowledge_health_events. The worker ALREADY has the run tree in hand —
`enrichment_utils.get_langsmith_run_tree()` — and sends only `trace_id`, which is
the trace, not the run: `read_run` needs the run id. So the plumbing was one
field short, not absent.

DISTINCT from the existing `trace_id` column on this table, and the distinction
is the same trap migration 045 documented: a trace id is not resolvable against
LangSmith's `read_run`. Both are kept because they answer different questions —
trace_id groups an attempt with the rest of its trace, langsmith_run_id prices
it.

Nullable, no default, no backfill: rows written before this ships, and any
attempt made with tracing off, carry NULL and are skipped by the run-id
finalizer rather than being guessed at.

Indexed partially (WHERE langsmith_run_id IS NOT NULL) for the same reason as
045's: the only query that reads this column wants rows that HAVE one and still
need pricing, so indexing the NULLs would store an entry per historical row for
no query that reads it.

Run from the repo root:
    python migrations/047_add_enrichment_langsmith_run_id.py upgrade
    python migrations/047_add_enrichment_langsmith_run_id.py downgrade

On Windows cmd.exe, `MODE=LOCAL python ...` is not valid syntax and fails with
"Invalid parameter - =LOCAL". Use:
    set "MODE=LOCAL" && python migration\\047_add_enrichment_langsmith_run_id.py upgrade
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

TABLE = "knowledge_enrichment_events"
INDEX = "ix_knowledge_enrichment_events_langsmith_run_id"


async def upgrade() -> None:
    """Add langsmith_run_id + its partial index, and cost_complete. Idempotent."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS langsmith_run_id TEXT"
        ))
        print(f"  + {TABLE}.langsmith_run_id TEXT")

        # Enrichment has had no notion of "this cost is final" because its cost was
        # always computed fresh at read time. Once LangSmith becomes the source of
        # truth the distinction matters: an unfinalized row is showing an estimate,
        # and the analytics layer has to be able to say so rather than presenting
        # every figure with equal confidence. Defaults FALSE, not NULL — every
        # existing row genuinely is un-finalized, which is a fact rather than an
        # absence.
        await conn.execute(text(
            f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
            f"cost_complete BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        print(f"  + {TABLE}.cost_complete BOOLEAN NOT NULL DEFAULT FALSE")

        # The finalized figures themselves. Kept separate from the token columns the
        # worker reports: overwriting those would destroy the only record of what the
        # local estimate said, and that difference is the whole evidence that
        # finalization is worth doing.
        await conn.execute(text(
            f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(12, 6)"
        ))
        print(f"  + {TABLE}.cost_usd NUMERIC(12, 6)")

        await conn.execute(text(
            f"CREATE INDEX IF NOT EXISTS {INDEX} ON {TABLE} (langsmith_run_id) "
            f"WHERE langsmith_run_id IS NOT NULL"
        ))
        print(f"  + {INDEX} (partial, WHERE langsmith_run_id IS NOT NULL)")
    await engine.dispose()
    print("Migration 047 upgrade completed.")


async def downgrade() -> None:
    """Drop the index and the three columns."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP INDEX IF EXISTS {INDEX}"))
        for col in ("langsmith_run_id", "cost_complete", "cost_usd"):
            await conn.execute(text(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS {col}"))
            print(f"  - {TABLE}.{col}")
    await engine.dispose()
    print("Migration 047 downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 047_add_enrichment_langsmith_run_id.py [upgrade|downgrade]")
        sys.exit(1)
    print(f"MODE={os.getenv('MODE', 'DEV')}  host={POSTGRES_HOST}  db={POSTGRES_DB_NAME}")
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
