"""Migration 044: add origin + caller-identity columns to ``knowledge_chat_sessions``.

Share -> Activity (Document Intelligence) has to answer two questions about every
conversation somebody else had with a shared collection: **where did it come from**
(the website bubble, the dashboard, or the raw API) and **who had it**. Neither fact
was on the session row, so the frontend was inferring both:

  * origin, by reading one page of ``GET .../health/queries`` (which does carry
    ``from_widget`` per *turn*) and treating "no user_id" as "widget". A signed-in
    bubble conversation whose turns fell out of that last-100 window landed on the
    wrong tab, undetectably.
  * identity, by rendering the account uuid as ``User c45844e8``, because
    ``knowledge_chat_sessions`` has no email and this service does not own the
    ``users`` table (``utils/db/knowledge_ops.py::get_user_id_from_email`` is
    stubbed to return ``None``).

Three nullable columns, no backfill:

``source``      ``widget`` | ``dashboard`` | ``api`` — the caller surface, resolved at
                write time from the request's auth mode (see
                ``routers/chat.py::_resolve_session_source``). Deliberately *not*
                four-valued: whether a dashboard conversation belongs to the owner or
                to an invited person is a question about who owns the collection
                *right now*, so it is derived at read time by comparing
                ``user_email`` against ``knowledge_config.owner_id`` rather than
                frozen into this column.
``user_email``  the caller's address, captured from their session at write time.
                Indexed — the "not the owner's own threads" filter and the
                shared-vs-dashboard split both key on it.
``user_name``   their display name from the same session, when the identity provider
                supplies one.

No backfill is possible: the addresses of already-recorded sessions live in the auth
service, not here, and there is no lookup for them in this database. Pre-existing rows
therefore read as "origin unknown, person unknown", which is honest. They do heal
opportunistically — ``chat/db/sessions.py::get_or_create_session`` fills any column
still NULL on the session's next turn.

Run from the repo root:
    MODE=PROD python migrations/044_add_session_origin_and_identity.py upgrade
    MODE=PROD python migrations/044_add_session_origin_and_identity.py downgrade

Downgrade drops all three columns and the index; captured emails/names are lost.
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
    # Same env-selection convention as migrations 039-043 — MODE unset defaults to
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
            "ALTER TABLE knowledge_chat_sessions "
            "ADD COLUMN IF NOT EXISTS source VARCHAR(16)"
        ))
        await conn.execute(text(
            "ALTER TABLE knowledge_chat_sessions "
            "ADD COLUMN IF NOT EXISTS user_email VARCHAR(320)"
        ))
        await conn.execute(text(
            "ALTER TABLE knowledge_chat_sessions "
            "ADD COLUMN IF NOT EXISTS user_name VARCHAR(255)"
        ))
        # Both new filters are always scoped to one collection first, so the index
        # leads with knowledge_id and the email only narrows within it.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_chat_sessions_kb_email "
            "ON knowledge_chat_sessions (knowledge_id, user_email)"
        ))
        print("Added knowledge_chat_sessions.source / user_email / user_name.")
    await engine.dispose()


async def downgrade() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "DROP INDEX IF EXISTS idx_knowledge_chat_sessions_kb_email"
        ))
        for column in ("user_name", "user_email", "source"):
            await conn.execute(text(
                f"ALTER TABLE knowledge_chat_sessions DROP COLUMN IF EXISTS {column}"
            ))
        print("Dropped the three knowledge_chat_sessions origin/identity columns.")
    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"upgrade", "downgrade"}:
        print("usage: python migrations/044_add_session_origin_and_identity.py [upgrade|downgrade]")
        sys.exit(2)
    asyncio.run(upgrade() if sys.argv[1] == "upgrade" else downgrade())
