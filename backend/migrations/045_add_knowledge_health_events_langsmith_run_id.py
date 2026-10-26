"""Migration 045: add langsmith_run_id to knowledge_health_events.

WHY: this service's chat cost has always been the LOCAL pricing.py estimate,
frozen onto the row at emit time. In the donor service (fs-agent-chat) that
inline number is only a PLACEHOLDER — see agents/run.py's "the inline numbers
above are the local estimate" — which a fire-and-forget finalizer then replaces
with LangSmith's own priced figures, LangSmith being the declared source of
truth for both cost and tokens (chat_utils/langsmith_cost.py's module docstring).
The analytics-and-health migration ported the placeholder and dropped the
finalizer, so knowledge's cost has been permanently an estimate off a hardcoded
price map that goes stale silently.

This column is the missing join key. It stores the LangSmith run id of the
turn's root `KnowledgeChat` run (chat/graph.py), which lets us read the turn's
real cost back by a DIRECT `read_run` — no LANGCHAIN_PROJECT and no metadata
filter needed. DISTINCT from the existing `trace_id` column, which is an
OpenTelemetry/Tempo span id and is not resolvable against LangSmith at all
(the donor makes the same distinction explicitly in
`agents/run.py::_current_langsmith_run_id`).

Nullable with no default: rows written before this ships, and any turn where
LangSmith tracing is inactive, simply carry NULL and are skipped by the
run-id backfill (the metadata-strategy backfill covers those instead).

Indexed partially (WHERE langsmith_run_id IS NOT NULL) because the only query
that touches this column looks for rows that HAVE one and still need pricing —
a full index would mostly store NULLs for no benefit.

See docs/plan/analytics-and-health/ and docs/api/agent-health.md.

Run from the repo root:
    python migrations/045_add_knowledge_health_events_langsmith_run_id.py upgrade
    python migrations/045_add_knowledge_health_events_langsmith_run_id.py downgrade
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
    """Add langsmith_run_id + its partial index. Idempotent."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE knowledge_health_events "
            "ADD COLUMN IF NOT EXISTS langsmith_run_id TEXT"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_health_events_langsmith_run_id "
            "ON knowledge_health_events (langsmith_run_id) "
            "WHERE langsmith_run_id IS NOT NULL"
        ))
    await engine.dispose()
    print("Added knowledge_health_events.langsmith_run_id column + partial index")
    print("Migration 045 upgrade completed.")


async def downgrade() -> None:
    """Drop the index and the column."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "DROP INDEX IF EXISTS ix_knowledge_health_events_langsmith_run_id"
        ))
        await conn.execute(text(
            "ALTER TABLE knowledge_health_events DROP COLUMN IF EXISTS langsmith_run_id"
        ))
    await engine.dispose()
    print("Dropped knowledge_health_events.langsmith_run_id column + index")
    print("Migration 045 downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 045_add_knowledge_health_events_langsmith_run_id.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
