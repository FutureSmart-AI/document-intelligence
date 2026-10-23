"""Migration 050: backfill collection_name from the linked agent's agent_name.

WHY THIS EXISTS
---------------
The knowledge feature used to live inside fs-agent-chat's Agent entity. Before the
split, an agent's display name (`agent_params.agent_name`, owned by fs-agent-chat)
was, in effect, the name of what is now a standalone knowledge collection. Every
collection created since the split can be named at creation or via
PUT /v1/{knowledge_id}/config, but nothing ever backfilled the ones that predate
that — they sit with collection_name IS NULL, and (confirmed live, 2026-08-29,
against one production owner's account) the Document Intelligence home
screen silently drops any card whose collection has no name, even when the
collection is fully indexed and queryable. Two named collections out of nine
owned ones rendered; the other seven, all fully functional, simply never
appeared.

GATE: "one of the agent's services is knowledge" — not "the only one"
-----------------------------------------------------------------------
An earlier, stricter design required knowledge to be an agent's *sole* enabled
service, to avoid importing a whole-product name (e.g. a multi-service agent's
name) onto a collection it only partly describes. Tested against real data
(two owners, six knowledge-linked agents, live queries against fs-agent-chat's
`GET /v1/agents/{agent_id}`), every single agent that had a `knowledge` service
also had `integration` enabled — the strict gate matched zero agents anywhere
it was tested. Decision (2026-08-29, explicit): relax the gate to "the agent has
an enabled `knowledge` service_link at all" — `integration` (the near-universal
ClickUp-connector companion service) does not disqualify an agent from this
import. This does mean an agent with several distinct capabilities (e.g. also
`nl2sql` or `connected_agent`) can still have its name imported onto its
knowledge collection; that's an accepted, explicit tradeoff of this relaxed
gate, not an oversight.

WHAT THIS TOUCHES
-----------------
- Reads: fs-agent-chat's `service_links` + `agents_registry` + `agent_params`
  (a SEPARATE database from this one, same RDS cluster, different POSTGRES_DB_NAME
  — see CHAT_ENV_PATH below).
- Writes: THIS database's `knowledge_config.collection_name`, only where it is
  currently NULL (never overwrites a name someone already set), plus a new
  `knowledge_collection_name_backfill_log` table recording exactly what was
  written, so `downgrade` can revert precisely instead of guessing.

Idempotent: safe to run more than once. A collection already named (by this
migration or anyone else) is never touched again.

Run from the repo root, from an environment with network access to the RDS
cluster (a cloud/routine session could not reach it — confirmed by a direct
socket-connect timeout — so this must run from a machine, bastion, or VPN that
can):

    python migrations/050_backfill_collection_name_from_agent_name.py upgrade
    python migrations/050_backfill_collection_name_from_agent_name.py downgrade

Needs a second env file for fs-agent-chat's own database credentials, since
that's a separate database this repo has no other reason to connect to. Default
assumes a sibling checkout (`../fs-agent-chat/.env.<mode>`, same MODE as this
repo's own env selection); override with CHAT_ENV_PATH if that layout doesn't
match where you're running this from.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODE = os.getenv("MODE", "DEV").upper()
ENV_FILE = ".env.prod" if MODE == "PROD" else (".env.dev" if MODE == "DEV" else ".env.local")

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(Path(ROOT, ENV_FILE), override=True)
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

# fs-agent-chat's own database — a second connection, not this repo's DB. Same
# MODE, resolved the same way, just from the other repo's env file.
CHAT_ENV_PATH = Path(os.getenv("CHAT_ENV_PATH", ROOT.parent / "fs-agent-chat" / ENV_FILE)).resolve()


def _chat_database_url() -> str:
    if not CHAT_ENV_PATH.exists():
        sys.exit(
            f"CHAT_ENV_PATH not found: {CHAT_ENV_PATH}\n"
            "Set CHAT_ENV_PATH to fs-agent-chat's env file for this MODE "
            "(default assumes a sibling checkout: ../fs-agent-chat/<same env file>)."
        )
    from dotenv import dotenv_values
    env = dotenv_values(CHAT_ENV_PATH)
    host, db = env.get("POSTGRES_HOST"), env.get("POSTGRES_DB_NAME")
    if not host or not db:
        sys.exit(f"{CHAT_ENV_PATH}: POSTGRES_HOST/POSTGRES_DB_NAME missing")
    print(f"chat DB:      host={host} db={db}")
    return (
        f"postgresql+asyncpg://{env.get('POSTGRES_USER')}:{env.get('POSTGRES_PASSWORD')}"
        f"@{host}:{env.get('POSTGRES_PORT', 5432)}/{db}"
    )


CREATE_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS knowledge_collection_name_backfill_log (
    knowledge_id        TEXT PRIMARY KEY,
    agent_id            TEXT NOT NULL,
    agent_name_written  TEXT NOT NULL,
    applied_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

# Relaxed gate: knowledge is *a* service of this agent, not necessarily its only
# one (see module docstring for why). agents_registry.deleted is still checked
# so a soft-deleted agent's leftover name is never imported.
CANDIDATE_AGENTS_SQL = """
SELECT sl.service_id AS knowledge_id, sl.agent_id, ap.agent_name
FROM service_links sl
JOIN agents_registry a ON a.agent_id = sl.agent_id AND a.deleted = FALSE
JOIN agent_params ap ON ap.agent_id = sl.agent_id
WHERE sl.service_type = 'knowledge'
  AND sl.status = 'enabled'
  AND sl.deleted_at IS NULL
  AND ap.agent_name IS NOT NULL
  AND btrim(ap.agent_name) <> '';
"""

NULL_COLLECTIONS_SQL = """
SELECT knowledge_id
FROM knowledge_config
WHERE deleted = FALSE AND collection_name IS NULL;
"""

# collection_name IS NULL re-checked at write time: safe against a name being
# set between the read above and this UPDATE.
UPDATE_SQL = """
UPDATE knowledge_config
SET collection_name = :name, updated_at = now()
WHERE knowledge_id = :kid AND deleted = FALSE AND collection_name IS NULL;
"""

INSERT_LOG_SQL = """
INSERT INTO knowledge_collection_name_backfill_log
    (knowledge_id, agent_id, agent_name_written)
VALUES (:kid, :agent_id, :name)
ON CONFLICT (knowledge_id) DO NOTHING;
"""

# Only revert a row this migration actually wrote, and only if nobody has
# changed it since (still equal to what we recorded) - never blind-null a name
# someone gave the collection afterward.
DOWNGRADE_SQL = """
UPDATE knowledge_config kc
SET collection_name = NULL, updated_at = now()
FROM knowledge_collection_name_backfill_log log
WHERE kc.knowledge_id = log.knowledge_id
  AND kc.collection_name = log.agent_name_written;
"""


async def upgrade() -> None:
    print(f"knowledge DB: host={POSTGRES_HOST} db={POSTGRES_DB_NAME}")
    chat_url = _chat_database_url()

    knowledge_engine = create_async_engine(DATABASE_URL, echo=False)
    chat_engine = create_async_engine(chat_url, echo=False)
    try:
        async with knowledge_engine.begin() as conn:
            await conn.execute(text(CREATE_LOG_TABLE))
            null_rows = (await conn.execute(text(NULL_COLLECTIONS_SQL))).fetchall()
        null_kids = {r.knowledge_id for r in null_rows}

        async with chat_engine.connect() as conn:
            candidates = (await conn.execute(text(CANDIDATE_AGENTS_SQL))).fetchall()
        candidate_by_kid = {r.knowledge_id: r for r in candidates}

        matches = [candidate_by_kid[kid] for kid in null_kids if kid in candidate_by_kid]

        print(f"\nagents with an enabled knowledge service + a name: {len(candidates)}")
        print(f"collections with collection_name IS NULL:          {len(null_kids)}")
        print(f"matches (will backfill):                           {len(matches)}\n")

        if not matches:
            print("Nothing to do.")
            return

        applied, skipped = 0, 0
        async with knowledge_engine.begin() as conn:
            for row in matches:
                name = row.agent_name.strip()
                result = await conn.execute(
                    text(UPDATE_SQL), {"name": name, "kid": row.knowledge_id}
                )
                if result.rowcount == 1:
                    await conn.execute(
                        text(INSERT_LOG_SQL),
                        {"kid": row.knowledge_id, "agent_id": row.agent_id, "name": name},
                    )
                    applied += 1
                    print(f"  SET  {row.knowledge_id}  <-  {name!r}  (agent {row.agent_id})")
                else:
                    # Named (or deleted) between the read above and this write.
                    skipped += 1

        print(f"\nApplied: {applied}   Skipped (raced/already named): {skipped}")
        print("Migration 050 upgrade completed.")
    finally:
        await knowledge_engine.dispose()
        await chat_engine.dispose()


async def downgrade() -> None:
    """Revert only the rows this migration itself wrote and that still carry
    the exact value it wrote - never touches a name changed since."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(CREATE_LOG_TABLE))
            result = await conn.execute(text(DOWNGRADE_SQL))
            reverted = result.rowcount
            await conn.execute(text("DELETE FROM knowledge_collection_name_backfill_log"))
        print(f"Reverted {reverted} collection_name value(s) set by this migration.")
        print("Migration 050 downgrade completed.")
    finally:
        await engine.dispose()


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print(f"Usage: python {Path(__file__).name} [upgrade|downgrade]")
        sys.exit(1)
    started = datetime.now(timezone.utc).isoformat()
    print(f"MODE={MODE}  ->  {ENV_FILE}   ({started})")
    asyncio.run(upgrade() if action == "upgrade" else downgrade())


if __name__ == "__main__":
    main()
