"""Migration 042: record on each document *how* it was asked to be processed.

Until now the only trace a document kept of its processing intent was the legacy
boolean ``document.llama_parse``. That boolean predates
``DocumentProcessingMode`` (``utils/models/enums.py``) and can only say "plain" or
"fancy" — it cannot distinguish a scan (OCR) from a table-heavy layout from a
chart-heavy page, even though those now take genuinely different paths in the
worker and cost different amounts. The mode the caller chose was resolved in the
router, handed to the worker over SQS, and then forgotten: nothing in this
service could answer "what mode was this document processed in?".

``document_processing_mode`` is that missing memory. It stores the *requested*
mode — the enum value the API resolved for the request — which is what the
document listing surfaces.

Backfill, and why this one is safe: every pre-existing row is written from
``llama_parse`` using exactly the mapping the live resolver already applies to
legacy callers (``utils/files/processing_modes.py``): ``TRUE`` →
``layout_aware``, ``FALSE`` → ``text_based``. So the backfill does not invent
history — it restates, in the new vocabulary, the same thing the boolean already
meant. A row that predates modes therefore reads the same way through the API as
a legacy caller's upload does today, instead of showing an empty cell.

The column stays nullable rather than ``NOT NULL DEFAULT 'text_based'``: a null
after this migration means an insert path forgot to set it, which is a bug worth
being able to see, not something to paper over with a default.

Run from the repo root:
    MODE=PROD python migrations/042_add_document_processing_mode.py upgrade
    MODE=PROD python migrations/042_add_document_processing_mode.py downgrade

Downgrade drops the column. ``llama_parse`` is untouched (it predates this
migration and is still written), so nothing that reads the old boolean breaks —
but the finer-grained mode of every document is lost.
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
    # Same env-selection convention as migrations 039-041 — MODE unset defaults to
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
        # IF NOT EXISTS — a re-run after a partial failure is a no-op.
        await conn.execute(text(
            "ALTER TABLE document "
            "ADD COLUMN IF NOT EXISTS document_processing_mode VARCHAR(32)"
        ))
        # WHERE ... IS NULL keeps the backfill idempotent and, on a re-run, stops it
        # from overwriting modes written by the application since the first run.
        result = await conn.execute(text(
            "UPDATE document "
            "SET document_processing_mode = "
            "  CASE WHEN llama_parse THEN 'layout_aware' ELSE 'text_based' END "
            "WHERE document_processing_mode IS NULL"
        ))
        print(f"Added document.document_processing_mode; backfilled {result.rowcount} rows "
              f"from llama_parse.")
    await engine.dispose()


async def downgrade() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE document DROP COLUMN IF EXISTS document_processing_mode"
        ))
        print("Dropped document.document_processing_mode.")
    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"upgrade", "downgrade"}:
        print("usage: python migrations/042_add_document_processing_mode.py [upgrade|downgrade]")
        sys.exit(2)
    asyncio.run(upgrade() if sys.argv[1] == "upgrade" else downgrade())
