"""Migration 048: store the RAG signals the chat pipeline already computes.

WHY
---
docs/plan/quality-baselines/coverage.md audited eval coverage stage by stage and
found measurement exists only at the two ENDS of the chat pipeline — extraction
mid-ingest, and answer generation at the end. Everything between the query
arriving and the answer being written computes quality signals and stores none.

Four of them are computed at RUNTIME today and discarded:

  * the retrieval verifier's sufficiency verdict — used for a branch decision in
    chat/rag_agent/agent_execution.py, then dropped
  * cited vs retrieved evidence — chat/rag_node.py::_filter_to_cited computes
    both sets and returns the filtered ones, never the ratio
  * the cross-turn evidence gate's kept/dropped counts — a logger.info line
  * the retrieval loop's attempt count — loop-local

This is the same pattern as confidence/stop_reason, which sat as NULL columns for
months while the agent produced both on every turn (fixed 2026-08-27). Every
column here is already-paid-for computation: no LLM call, no added latency, no
corpus, no judge.

WHY COLUMNS HERE, BUT A SEPARATE TABLE FOR JUDGE SCORES
------------------------------------------------------
docs/plan/quality-baselines/storage.md sets the rule: a metric stays a typed
column when every row has it and the scorecard aggregates it on every read;
sampled or judge-produced metrics become rows in a generic score table instead.
These are structural counts present on every answered turn and scanned by
month-wide aggregations, so they are columns. The reference-free judge scores
land in knowledge_quality_scores (migration 049) instead.

NULL SEMANTICS, WHICH ARE LOAD-BEARING
--------------------------------------
Every column is nullable with no default, and NULL means "not recorded" — a row
written before this migration, a turn whose owner is not in RAG_EVAL_OWNER_IDS,
or the error path where no retrieval happened. It never means zero.

That distinction matters most for the count columns. A turn with
retrieved_chunk_count = 0 genuinely retrieved nothing (a real and interesting
outcome); a turn with NULL was never measured. Rendering NULL as 0 would invent
a fleet of no-retrieval turns and drag any utilization average toward zero.

verifier_verdict carries a third state for the same reason: 'not_run'. The
verifier is deliberately skipped on the final attempt (its only job is deciding
whether to retry, and on the last attempt that is already decided), so a
single-attempt turn legitimately has no verdict — which is different from NULL
meaning the column predates this change.

NO INDEXES
----------
Deliberate. These columns are read as aggregates over a window already narrowed
by the existing (knowledge_id, created_at) index, never as a lookup key. An
index per count column would add write cost on every chat turn for no read
benefit.

See docs/plan/quality-baselines/rag-metrics.md for what each is used for.

Run from the repo root:
    python migrations/048_add_health_events_rag_eval_tier0.py upgrade
    python migrations/048_add_health_events_rag_eval_tier0.py downgrade
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

COLUMNS = [
    ("retrieved_chunk_count", "INTEGER"),
    ("retrieved_doc_count", "INTEGER"),
    ("cited_chunk_count", "INTEGER"),
    ("cited_doc_count", "INTEGER"),
    ("retrieval_attempts", "INTEGER"),
    ("verifier_verdict", "TEXT"),
    ("verifier_missing", "TEXT"),
    ("evidence_groups_loaded", "INTEGER"),
    ("evidence_groups_kept", "INTEGER"),
]


async def upgrade() -> None:
    """Add the nine Tier-0 columns. Idempotent."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        for name, sql_type in COLUMNS:
            await conn.execute(text(
                f"ALTER TABLE knowledge_health_events "
                f"ADD COLUMN IF NOT EXISTS {name} {sql_type}"
            ))
    await engine.dispose()
    print(f"Added {len(COLUMNS)} RAG eval Tier-0 columns to knowledge_health_events")
    print("Migration 048 upgrade completed.")


async def downgrade() -> None:
    """Drop the nine columns."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        for name, _ in COLUMNS:
            await conn.execute(text(
                f"ALTER TABLE knowledge_health_events DROP COLUMN IF EXISTS {name}"
            ))
    await engine.dispose()
    print(f"Dropped {len(COLUMNS)} RAG eval Tier-0 columns from knowledge_health_events")
    print("Migration 048 downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 048_add_health_events_rag_eval_tier0.py [upgrade|downgrade]")
        sys.exit(1)
    asyncio.run(upgrade() if action == "upgrade" else downgrade())


if __name__ == "__main__":
    main()
