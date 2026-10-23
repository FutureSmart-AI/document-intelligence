"""Migration 038: Create Batch Query Reports tables.

Ported from fs-agent-dashboard's "Batch Query Reports" feature (Feature 8 of
the org-wide cross-service dependency review, an internal review) — see
docs/plan/batch-query-reports/schema.md for the full migration design
and decisions.md for what was confirmed. Source: `batch_query_reports/
pydantic_model_db.py` (SQLModel tables) + `migrations/versions/
d1f2a3b4c5e6_add_batch_query_reports_tables.py` (the source's own migration).

Re-keyed for this service: `agent_id` -> `knowledge_id` (this service's
identity unit — see docs/overview/glossary.md), `user_id` -> `owner_id`
(always the caller's email here, matching `KnowledgeConfig.owner_id` /
`knowledge_invitations.invited_by_email`).

Six tables (five from the design spec's §6 + `report_settings`, which the
donor's actual code added beyond its own migration file for a
runtime-configurable per-report query cap):
  - `reports`            — the definition/template.
  - `report_runs`        — one execution of a report.
  - `report_run_groups`  — pre-materialized query groups of a run (stable
                            `group_id`, referenced by the internal API).
  - `report_run_queries` — pre-materialized queries of a run (stable
                            `query_id`, referenced by the internal API).
  - `report_run_results` — per-query output, upserted on (`run_id`,
                            `query_id`) for Worker-retry idempotency.
  - `report_settings`    — per-knowledge_id runtime override of the
                            per-report query cap (env default otherwise).

Schema differences vs. the source (deliberate, not omissions — see
decisions.md):
  - `TIMESTAMPTZ`, not naive `TIMESTAMP` — matches this service's own
    `knowledge_invitations` convention (migration 037), so there is no need
    for the source's `_to_naive_utc` conversion dance in the DB layer.
  - Foreign keys within this feature's own tables (`report_runs.report_id`,
    `report_run_groups/queries/results.run_id`) with `ON DELETE CASCADE` —
    the source has none of these (no FK anywhere), but since all five/six
    tables are introduced together here, enforcing referential integrity
    within the feature costs nothing and catches bugs earlier. No FK to
    `knowledge_id` itself (mirrors `knowledge_invitations`, which also has
    none — knowledge_config is a pre-existing table this migration doesn't
    touch).
  - No `execution_details`/`relevance_score`/`retrieved_chunks` columns on
    `report_run_results` — dashboard-only additions for a SQL-agent use case
    this service's RAG-only chat graph doesn't have; the written spec (this
    task's authoritative source) doesn't include them either.

Run from the repo root:
    python migrations/038_create_batch_query_reports.py upgrade
    python migrations/038_create_batch_query_reports.py downgrade
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
    """Create the Batch Query Reports tables."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS report_settings (
                knowledge_id TEXT PRIMARY KEY,
                max_queries_per_report INTEGER,
                updated_at TIMESTAMPTZ,
                updated_by TEXT
            )
            """
        ))

        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS reports (
                report_id TEXT PRIMARY KEY,
                knowledge_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                name TEXT NOT NULL,
                report_instructions TEXT,
                query_groups JSONB,
                analysis_prompt TEXT,
                schedule JSONB,
                next_run_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ,
                deleted BOOLEAN NOT NULL DEFAULT false,
                updated_by TEXT,
                deleted_by TEXT,
                deleted_at TIMESTAMPTZ
            )
            """
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_reports_knowledge_id ON reports (knowledge_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_reports_knowledge_deleted ON reports (knowledge_id, deleted)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_reports_next_run_at ON reports (next_run_at)"
        ))

        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS report_runs (
                run_id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL REFERENCES reports(report_id) ON DELETE CASCADE,
                knowledge_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                trigger TEXT NOT NULL DEFAULT 'manual',
                started_at TIMESTAMPTZ,
                ended_at TIMESTAMPTZ,
                failure_reason TEXT,
                overall_summary TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ,
                updated_by TEXT
            )
            """
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_report_runs_report ON report_runs (report_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_report_runs_knowledge ON report_runs (knowledge_id)"
        ))

        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS report_run_groups (
                group_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES report_runs(run_id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                position INTEGER NOT NULL
            )
            """
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_run_groups_run ON report_run_groups (run_id)"
        ))

        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS report_run_queries (
                query_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES report_runs(run_id) ON DELETE CASCADE,
                group_id TEXT NOT NULL REFERENCES report_run_groups(group_id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                text TEXT NOT NULL
            )
            """
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_run_queries_run ON report_run_queries (run_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_run_queries_group ON report_run_queries (group_id)"
        ))

        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS report_run_results (
                id SERIAL PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES report_runs(run_id) ON DELETE CASCADE,
                group_id TEXT,
                query_id TEXT NOT NULL,
                session_id TEXT,
                answer TEXT,
                answer_ref TEXT,
                sources_used JSONB,
                retrieved_documents JSONB,
                response_time_ms INTEGER,
                token_usage INTEGER,
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ,
                CONSTRAINT uq_run_results_run_query UNIQUE (run_id, query_id)
            )
            """
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_run_results_run ON report_run_results (run_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_run_results_query_id ON report_run_results (query_id)"
        ))
    await engine.dispose()
    print(
        "Created reports / report_runs / report_run_groups / report_run_queries / "
        "report_run_results / report_settings tables with indexes and FK constraints."
    )
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop the Batch Query Reports tables (children first)."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS report_run_results"))
        await conn.execute(text("DROP TABLE IF EXISTS report_run_queries"))
        await conn.execute(text("DROP TABLE IF EXISTS report_run_groups"))
        await conn.execute(text("DROP TABLE IF EXISTS report_runs"))
        await conn.execute(text("DROP TABLE IF EXISTS reports"))
        await conn.execute(text("DROP TABLE IF EXISTS report_settings"))
    await engine.dispose()
    print("Dropped Batch Query Reports tables.")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 038_create_batch_query_reports.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
