"""Migration 034: Create knowledge_health_events table.

Ported from fs-agent-chat's Agent Health / Analytics feature (event
emission in agent_health_service.py/run.py, storage+admin API in
agent_health_router.py — see docs/plan/analytics-and-health/events.md for
the full migration design and decisions.md for what was confirmed).

Re-keyed for this service: every `agent_id` in the source becomes
`knowledge_id` (this service's identity unit — see
docs/overview/glossary.md). `chat_id`, `session_id`, `owner_id`, `user_id`
already mean the same thing in both services, so those carry over unchanged.

This is a separate table from `knowledge_chat_logs`, not an extension of
it, even though both are one-row-per-turn and share several columns
(chat_id, session_id, knowledge_id, user_id, query, refined_query, model,
is_answered). Mirrors the source's own separation of its chat/conversation
log from its analytics event log:
  - `knowledge_chat_logs` is the conversation record (stores the actual
    response text and retrieval evidence) — read on the conversation
    history/transcript path.
  - `knowledge_health_events` is the analytics record (stores outcome,
    performance, and cost facts, never response content) — read on the
    aggregation/admin path, which needs to scan far more rows per query
    (a `trends` call may scan a full month) without touching response text.
Decoupling them means the health-event write is a narrow, append-only
insert independent of the chat_logs write, and analytics queries never pay
for scanning large `response`/`service_data` columns.

`cost_usd` + `cost_complete` + `cost_breakdown` replace the source's flat
`cost_usd` per the confirmed multi-model-cost fix (docs/13-multi-model-cost.md
in fs-agent-chat): a query fans out across several independently-configured
models (refiner, router, RAG sub-agent, answer node, ...), so cost must be
priced per (provider, model) subtotal and summed, never off one model's
rate. `cost_breakdown` carries the per-model lines; `cost_complete=false`
flags that at least one model in the breakdown had no known price (that
line's cost is `null`, tokens stay visible, and the summed `cost_usd` is a
documented lower bound rather than a silent zero).

`key_type` records which of this service's auth modes
(docs/modules/auth-and-rbac.md) authenticated the request:
`master_key` | `user_api_key` | `session` | `public` — replacing the
source's `owner-key`/`platform-key` pair, which doesn't map onto this
service's auth model.

`chat_id` is the dedupe key (UNIQUE), matching the source's idempotency
design: one event per chat turn, safe to re-insert on retry via
`ON CONFLICT DO NOTHING` at the call site.

Run from the repo root:
    python migrations/034_create_knowledge_health_events.py upgrade
    python migrations/034_create_knowledge_health_events.py downgrade
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
    """Create the knowledge_health_events table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS knowledge_health_events (
                id SERIAL PRIMARY KEY,
                knowledge_id TEXT NOT NULL,
                owner_id TEXT,
                user_id TEXT,
                session_id TEXT,
                chat_id UUID NOT NULL UNIQUE,
                trace_id TEXT,

                query TEXT NOT NULL,
                refined_query TEXT NOT NULL DEFAULT '',
                route TEXT,
                services_used JSONB NOT NULL DEFAULT '[]',

                status TEXT NOT NULL,
                is_answered BOOLEAN NOT NULL,
                failure_category TEXT,
                failed_stage TEXT,
                failure_detail TEXT,
                confidence TEXT,
                stop_reason TEXT,

                provider TEXT NOT NULL DEFAULT 'openai',
                model TEXT NOT NULL,
                from_widget BOOLEAN NOT NULL DEFAULT false,
                key_type TEXT NOT NULL,

                latency_ms INTEGER NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                retry_count INTEGER NOT NULL DEFAULT 0,

                cost_usd NUMERIC,
                cost_complete BOOLEAN NOT NULL DEFAULT true,
                cost_breakdown JSONB NOT NULL DEFAULT '[]',

                event_type TEXT NOT NULL DEFAULT 'knowledge_query',
                schema_version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ))
        # knowledge_id + created_at: every read endpoint (scorecard, trends,
        # failures, queries) filters by knowledge_id and a time window.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_health_events_knowledge_id_created_at "
            "ON knowledge_health_events (knowledge_id, created_at)"
        ))
        # status: failures/queries endpoints filter on outcome.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_health_events_status "
            "ON knowledge_health_events (status)"
        ))
    await engine.dispose()
    print(
        "Created knowledge_health_events table with "
        "idx_knowledge_health_events_knowledge_id_created_at / "
        "idx_knowledge_health_events_status indexes"
    )
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop the knowledge_health_events table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS knowledge_health_events"))
    await engine.dispose()
    print("Dropped knowledge_health_events table")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 034_create_knowledge_health_events.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
