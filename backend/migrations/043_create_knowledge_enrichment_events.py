"""Migration 043: Create knowledge_enrichment_events table.

The enrichment-side counterpart to `knowledge_health_events` (migration 034).
One row per enrichment *attempt* on one document. See
docs/plan/enrichment-analytics/plan.md for the design and
docs/modules/analytics-data-sources.md for how this table relates to the two
that already exist.

Why a new table rather than reading `usage_events`
--------------------------------------------------
`usage_events` already carries enrichment duration, tokens, provider and model.
It cannot carry the other half of an analytics screen — outcome. All three LLM
enrichment strategies raise before they report usage
(`fs-agent-workers/celery_utils/enrichment/enrichment_strategies.py`), so a
failed enrichment produces no usage event at all. Any per-model comparison
built on that table reports zero failures forever, which flatters exactly the
models that fail most. `usage_events` is a metering table: `(operation, label,
metadata)` plus numeric metrics, by design, with nowhere to put a failure
category or a per-field breakdown.

Why not extend `knowledge_health_events`
----------------------------------------
That table is keyed by `chat_id NOT NULL UNIQUE` and written in-process by
`chat/db/health_events.py` from the chat graph's own state. Enrichment runs in
a different process, in a different repository (`fs-agent-workers`), and
reaches this service only over HTTP. Its unit of work is a document, not a
turn: no query, no session, no user. Sharing the table would mean making half
its NOT NULL columns nullable and teaching every existing health query to
exclude the new rows.

Deliberate differences from `knowledge_health_events`
----------------------------------------------------
1. **No cost columns.** Chat freezes `cost_usd` onto the row at write time,
   priced by a hardcoded dict in `chat/pricing.py`. This table stores tokens
   and the model, and cost is computed at read time from the versioned
   `usage_service_pricing` table (migration 035/040) at the rate effective on
   the day the row was written. A rate that turns out wrong can then be
   corrected once, in one place, and every historical number follows — instead
   of being wrong on every row already written. See
   docs/plan/enrichment-analytics/decisions.md.
2. **`field_data` instead of `node_data`.** Chat's per-node breakdown answers
   "what did each stage cost". Enrichment's equivalent question is "which
   configured fields actually came back populated" — the standard completeness
   measure for a structured-extraction pipeline, and the only quality signal
   available without a judge model.
3. **`requested_*` alongside the actual model.** `invoke_with_retry` may serve
   a document on the fallback model. Attributing that document to the requested
   model would make a failing model look like whatever the fallback costs, so
   both are recorded and `used_fallback` marks the divergence.

`event_key` is the dedupe key (UNIQUE), matching 034's use of `chat_id`: the
worker generates it per attempt so a retried POST is idempotent via
`ON CONFLICT DO NOTHING` at the call site.

Run from the repo root:
    python migrations/043_create_knowledge_enrichment_events.py upgrade
    python migrations/043_create_knowledge_enrichment_events.py downgrade
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
    """Create the knowledge_enrichment_events table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS knowledge_enrichment_events (
                id SERIAL PRIMARY KEY,
                knowledge_id TEXT NOT NULL,
                owner_id TEXT,
                object_id TEXT,
                event_key UUID NOT NULL UNIQUE,
                trace_id TEXT,

                -- Input size. Enrichment latency and token count both scale with
                -- document length, so a model comparison that ignores it compares
                -- the documents each model happened to get, not the models.
                document_name TEXT,
                char_count INTEGER NOT NULL DEFAULT 0,
                chunk_count INTEGER NOT NULL DEFAULT 0,

                status TEXT NOT NULL,
                failure_category TEXT,
                failure_detail TEXT,

                enrichment_strategy INTEGER,
                enrichment_strategy_name TEXT,

                -- What was asked for vs what actually served the document.
                requested_provider TEXT,
                requested_model TEXT,
                provider TEXT,
                model TEXT,
                used_fallback BOOLEAN NOT NULL DEFAULT false,
                fallback_reason TEXT,

                duration_ms INTEGER NOT NULL DEFAULT 0,
                retry_count INTEGER NOT NULL DEFAULT 0,

                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,

                -- Completeness: how much of the configured schema came back filled.
                fields_configured INTEGER NOT NULL DEFAULT 0,
                fields_populated INTEGER NOT NULL DEFAULT 0,
                field_data JSONB NOT NULL DEFAULT '[]',

                key_type TEXT,
                event_type TEXT NOT NULL DEFAULT 'document_enrichment',
                schema_version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ))
        # Every read endpoint filters by knowledge_id and a time window — same
        # access pattern as 034's primary index.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_enrichment_events_kid_created_at "
            "ON knowledge_enrichment_events (knowledge_id, created_at)"
        ))
        # status: the failures endpoint and the scorecard's quality block filter
        # on outcome.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_enrichment_events_status "
            "ON knowledge_enrichment_events (status)"
        ))
        # The model-comparison panel groups by (provider, model) inside a window;
        # without this it is a full scan of the window per read.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_enrichment_events_model "
            "ON knowledge_enrichment_events (knowledge_id, provider, model, created_at)"
        ))
        # The per-document drill-down looks up every attempt on one document,
        # including the failed ones that preceded a success.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_enrichment_events_object_id "
            "ON knowledge_enrichment_events (object_id)"
        ))
    await engine.dispose()
    print(
        "Created knowledge_enrichment_events table with kid_created_at / status / "
        "model / object_id indexes"
    )
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop the knowledge_enrichment_events table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS knowledge_enrichment_events"))
    await engine.dispose()
    print("Dropped knowledge_enrichment_events table")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 043_create_knowledge_enrichment_events.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
