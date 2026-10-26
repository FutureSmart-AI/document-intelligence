"""Migration 049: a generic score table, so the next metric is not a migration.

WHY THIS SHAPE
--------------
Both analytics tables carry a dedicated typed column per metric, and four of the
last six migrations in this area exist only to add one
(036 node_data, 045 langsmith_run_id, 046 seven validation columns, 047 the
enrichment run id). Every one is a production DDL change needing its own
go-ahead, and in this repo that go-ahead is the slowest step in the loop —
migration 043 sat unrun for exactly that reason. Meanwhile
docs/plan/quality-baselines/metrics.md lists six more metrics we want.

So this table takes the shape the tooling in this space converged on (Langfuse's
score model: name / value / data_type / source / comment, attached to exactly one
subject, plus a config table pinning each metric's type and range). A new metric
becomes a config row plus an insert.

WHAT GOES HERE AND WHAT STAYS A COLUMN
--------------------------------------
The split, from docs/plan/quality-baselines/storage.md, is not stylistic:

  * COLUMN   — every row has it and month-wide aggregations scan it on every
               read: latency_ms, cost_usd, is_answered, and the Tier-0
               structural counts from migration 048. A join per metric there
               would be a real cost.
  * ROW HERE — sampled, optional, judge-produced, or new: faithfulness,
               answer relevance, coherence, extraction groundedness, and every
               future one. These are sparse by nature — most turns are never
               judged — so a column per metric would be mostly NULL anyway,
               which is the shape a row-per-score table is for.

THREE DEVIATIONS FROM THE REFERENCE MODEL, EACH ONE LEARNED HERE
----------------------------------------------------------------
1. `observations` is ours, and it is not optional. A score without its
   denominator is unreadable: `1.000 over 5 observations` and
   `0.964 over 56 observations` were both being reported as extraction
   correctness until the validator started withholding the first kind. Langfuse
   has no field for this and a comment string will not do — you cannot aggregate
   a comment.

2. `grader_model` is its own column, not metadata. The self-grading question
   ("was this model asked to grade its own output") has to be answerable in a
   GROUP BY, which is exactly why knowledge_enrichment_events.validation_model
   exists as a column today.

3. A WITHHELD SCORE IS AN ABSENT ROW, never a row with value NULL. `value` is
   therefore NOT NULL for numeric scores. This is the whole reason to prefer a
   row per score: the rule "NULL means no opinion, 0.0 means everything checked
   was wrong" stops being a comment above a column and becomes structurally
   impossible to violate.

UNIQUENESS
----------
UNIQUE (subject_type, subject_id, name, source) makes re-scoring idempotent: a
re-run with an improved judge prompt updates the row rather than appending a
second opinion that every average would then double-count. Two different sources
(a judge and a human annotation) CAN coexist for the same metric on the same
subject, which is deliberate — disagreement between them is a signal worth
keeping, and `source` is what lets a query separate them.

subject_id is TEXT, not a typed FK: a subject may be a chat turn (chat_id, a
uuid), an enrichment event (a bigint id), or a benchmark run (a label string).
One text column keeps all three addressable without three nullable FKs, at the
cost of no referential integrity — accepted, because a score outliving its
subject is harmless and deleting scores in step with a purge is a scheduled job,
not a constraint.

See docs/plan/quality-baselines/storage.md for the full design discussion.

Run from the repo root:
    python migrations/049_create_knowledge_quality_scores.py upgrade
    python migrations/049_create_knowledge_quality_scores.py downgrade
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

CREATE_CONFIGS = """
CREATE TABLE IF NOT EXISTS knowledge_quality_score_configs (
    name          TEXT PRIMARY KEY,
    data_type     TEXT NOT NULL,
    min_value     DOUBLE PRECISION,
    max_value     DOUBLE PRECISION,
    categories    JSONB,
    description   TEXT NOT NULL,
    is_archived   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

CREATE_SCORES = """
CREATE TABLE IF NOT EXISTS knowledge_quality_scores (
    id            BIGSERIAL PRIMARY KEY,
    knowledge_id  TEXT NOT NULL,
    subject_type  TEXT NOT NULL,
    subject_id    TEXT NOT NULL,
    name          TEXT NOT NULL,
    value         DOUBLE PRECISION,
    string_value  TEXT,
    data_type     TEXT NOT NULL,
    source        TEXT NOT NULL,
    grader_model  TEXT,
    comment       TEXT,
    observations  INTEGER,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_knowledge_quality_scores_subject_name_source
        UNIQUE (subject_type, subject_id, name, source),
    -- A numeric score must carry a number; a categorical/text one must carry a
    -- string. This is what stops a "withheld" score being written as a row with
    -- a NULL value, which is the confusion the whole table exists to prevent.
    CONSTRAINT ck_knowledge_quality_scores_value_present
        CHECK (
            (data_type = 'NUMERIC'  AND value IS NOT NULL)
         OR (data_type = 'BOOLEAN'  AND value IS NOT NULL)
         OR (data_type IN ('CATEGORICAL', 'TEXT') AND string_value IS NOT NULL)
        )
)
"""

# The read pattern is "every score for this knowledge base over this window",
# mirroring how the scorecard already reads knowledge_health_events.
INDEXES = [
    ("ix_knowledge_quality_scores_kb_created",
     "CREATE INDEX IF NOT EXISTS ix_knowledge_quality_scores_kb_created "
     "ON knowledge_quality_scores (knowledge_id, created_at)"),
    # Drill-down from one turn to its scores, for the per-query view.
    ("ix_knowledge_quality_scores_subject",
     "CREATE INDEX IF NOT EXISTS ix_knowledge_quality_scores_subject "
     "ON knowledge_quality_scores (subject_type, subject_id)"),
    # "average faithfulness this month" scans by name within a window.
    ("ix_knowledge_quality_scores_name_created",
     "CREATE INDEX IF NOT EXISTS ix_knowledge_quality_scores_name_created "
     "ON knowledge_quality_scores (name, created_at)"),
]

# The charter, in the database. Seeded so the first insert has something to
# validate against; ON CONFLICT DO NOTHING keeps the migration re-runnable and
# never overwrites a description someone has since refined.
SEED_CONFIGS = [
    ("faithfulness", "NUMERIC", 0.0, 1.0,
     "Fraction of the answer's factual claims supported by the evidence it cited. "
     "Reference-free. Withheld (no row) when the turn cited no context."),
    ("answer_relevance", "NUMERIC", 0.0, 1.0,
     "Does the answer address the question actually asked. Scored against the "
     "USER's query, not the refined query, so a bad refinement stays visible."),
    ("coherence", "NUMERIC", 0.0, 1.0,
     "Is the answer internally consistent and readable — no self-contradiction, "
     "no truncation, no repeated boilerplate. Judges prose, not accuracy."),
]


async def upgrade() -> None:
    """Create both tables, their indexes, and seed the three chat score configs.
    Idempotent."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(CREATE_CONFIGS))
        await conn.execute(text(CREATE_SCORES))
        for _, sql in INDEXES:
            await conn.execute(text(sql))
        for name, data_type, lo, hi, description in SEED_CONFIGS:
            await conn.execute(
                text(
                    "INSERT INTO knowledge_quality_score_configs "
                    "(name, data_type, min_value, max_value, description) "
                    "VALUES (:name, :data_type, :lo, :hi, :description) "
                    "ON CONFLICT (name) DO NOTHING"
                ),
                {"name": name, "data_type": data_type, "lo": lo, "hi": hi,
                 "description": description},
            )
    await engine.dispose()
    print("Created knowledge_quality_scores + knowledge_quality_score_configs")
    print(f"Seeded {len(SEED_CONFIGS)} score configs; added {len(INDEXES)} indexes")
    print("Migration 049 upgrade completed.")


async def downgrade() -> None:
    """Drop both tables. Scores are derived data — they can be recomputed by
    re-running the eval, so dropping them loses no source of truth."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS knowledge_quality_scores"))
        await conn.execute(text("DROP TABLE IF EXISTS knowledge_quality_score_configs"))
    await engine.dispose()
    print("Dropped knowledge_quality_scores + knowledge_quality_score_configs")
    print("Migration 049 downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 049_create_knowledge_quality_scores.py [upgrade|downgrade]")
        sys.exit(1)
    asyncio.run(upgrade() if action == "upgrade" else downgrade())


if __name__ == "__main__":
    main()
