"""Migration 046: record HOW a document was read, and how good the extraction was.

Two additions to knowledge_enrichment_events, shipped together because they land
on the same row and a second migration over the same table buys nothing.

--------------------------------------------------------------------------
1. document_processing_mode  — the column that makes mode comparison possible
--------------------------------------------------------------------------
The mode a caller picks (text_based / scanned / layout_aware / visual / mixed)
is the single biggest lever on extraction quality, and until now it was NOT on
the analytics row at all. It was stored on the document
(`utils/models/db_tables.py` KnowledgeDocument.document_processing_mode) and
read by the worker to choose a parser, but `track_enrichment_health` never
passed it on — so `GET /health/enrichment/documents` returned mode=None for
every row and the screen could not answer "is layout_aware worth it".

Verified live 2026-08-27 on the same PDF ingested twice: text_based lost the
Withdrawal/Deposit column distinction entirely (44 of 48 amounts carried the
wrong sign against the statement's own balance column) while layout_aware got
47 of 49 right. That is a 11x accuracy difference which the analytics screen
reported as two identical 100%-coverage successes.

There is a second reason this column matters, beyond reporting. The mode also
SELECTS THE MODEL: `celery_utils/enrichment/llm_factory.py::get_enrichment_config`
level 2 substitutes a stronger model for any mode other than text_based. So
mode and model move together unless a knowledge base pins `enrichment_model`,
and without this column a model-comparison table cannot tell "this model is
better" from "this model only ever saw layout_aware input".

--------------------------------------------------------------------------
2. validation_*  — a quality signal that can actually FAIL
--------------------------------------------------------------------------
`field_data[].populated` and the `avg_field_coverage` built from it answer
exactly one question: is this field non-empty. That is not a quality metric,
and reading it as one is how the 11x difference above stayed invisible — both
runs populated 8 of 8 fields, so both scored 100%.

Coverage cannot be repaired into a correctness signal; non-emptiness is all it
can observe. So correctness gets its own columns rather than being folded into
coverage, and the two are reported side by side:

  coverage   = did the extractor return something        (deterministic, free)
  validation = is what it returned right and complete     (judged, costs money)

Columns:
  validation_status      pending | passed | failed | skipped | error
  validation_model       the JUDGE's model, deliberately separate from the
                         enriching model on the same row. A judge scoring its
                         own output has a self-preference bias, so the two must
                         be distinguishable after the fact -- otherwise a table
                         showing "model X validates well" may only be showing
                         that X was asked to grade itself.
  correctness_score      0..1, share of checked values supported by the source
  completeness_score     0..1, share of instances present in the source that
                         were actually extracted
  validation_data        JSONB per-field verdicts + evidence, same expand-by-
                         LATERAL shape as field_data so _by_field_coverage's
                         technique works on it unchanged
  validation_ms          judge latency, kept out of duration_ms so enrichment
                         perf stays comparable to rows recorded before this
  validation_cost_usd    judge cost, kept out of cost_usd for the same reason:
                         folding it in would make every post-046 document look
                         more expensive than its pre-046 equivalent for no
                         reason the reader could see.

All nullable, no defaults, no backfill: rows written before this ships carry
NULL and MUST read as "not validated", never as "validated and scored zero" --
0.0 is a real score meaning "everything checked was wrong", which is the
opposite of no opinion. The analytics layer has to keep that distinction.

Indexed on document_processing_mode only. It is the GROUP BY key for the mode
comparison and has at most five values, so it is a cheap, high-selectivity-for-
grouping index. The validation columns get no index: they are read as
aggregates over a window already narrowed by knowledge_id + created_at.

See docs/plan/enrichment-analytics/.

Run from the repo root:
    python migrations/046_add_enrichment_mode_and_validation.py upgrade
    python migrations/046_add_enrichment_mode_and_validation.py downgrade

On Windows cmd.exe, MODE=LOCAL python ... is not valid syntax and fails with
"Invalid parameter - =LOCAL". Use:
    set "MODE=LOCAL" && python migration\\046_add_enrichment_mode_and_validation.py upgrade
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

# (column, type) — every one nullable, see the module docstring on why there is
# no default and no backfill.
COLUMNS = [
    ("document_processing_mode", "TEXT"),
    ("validation_status", "TEXT"),
    ("validation_model", "TEXT"),
    ("correctness_score", "DOUBLE PRECISION"),
    ("completeness_score", "DOUBLE PRECISION"),
    ("validation_data", "JSONB"),
    ("validation_ms", "INTEGER"),
    ("validation_cost_usd", "NUMERIC(12, 6)"),
]

INDEX = "ix_knowledge_enrichment_events_processing_mode"


async def upgrade() -> None:
    """Add the mode column, the validation columns, and the mode index. Idempotent."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        for name, coltype in COLUMNS:
            await conn.execute(text(
                f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {name} {coltype}"
            ))
            print(f"  + {TABLE}.{name} {coltype}")

        # Partial index: rows written before this ships carry NULL, and the mode
        # comparison only ever groups rows that HAVE a mode. Indexing the NULLs
        # would store one entry per historical row for no query that reads them.
        await conn.execute(text(
            f"CREATE INDEX IF NOT EXISTS {INDEX} "
            f"ON {TABLE} (knowledge_id, document_processing_mode) "
            f"WHERE document_processing_mode IS NOT NULL"
        ))
        print(f"  + {INDEX} (partial, WHERE document_processing_mode IS NOT NULL)")
    await engine.dispose()
    print("Migration 046 upgrade completed.")


async def downgrade() -> None:
    """Drop the index and every column this migration added."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP INDEX IF EXISTS {INDEX}"))
        for name, _ in COLUMNS:
            await conn.execute(text(
                f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS {name}"
            ))
            print(f"  - {TABLE}.{name}")
    await engine.dispose()
    print("Migration 046 downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 046_add_enrichment_mode_and_validation.py [upgrade|downgrade]")
        sys.exit(1)
    print(f"MODE={os.getenv('MODE', 'DEV')}  host={POSTGRES_HOST}  db={POSTGRES_DB_NAME}")
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
