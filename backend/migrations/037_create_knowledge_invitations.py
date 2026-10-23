"""Migration 037: Create knowledge_invitations table.

Ported from fs-agent-chat's "Share agent" feature (role/permission matrix in
auth_lib/permissions.py, invitations CRUD in routers/agent_invitations_router.py
+ chat_utils/db/agent_invitations.py, backed by its agent_invitations table —
see docs/plan/access-and-sharing/archive/design-share-invitations.md for the full migration design and
decisions.md for what was confirmed).

Re-keyed for this service: `agent_id` in the source becomes `knowledge_id`
(this service's identity unit — see docs/overview/glossary.md).

This table is genuinely new here — before this migration, fs-agent-knowledge
had no sharing/role model of its own at all. Every non-owner permission
check delegated to fs-agent-chat over HTTP (`CHAT_AUTH_BASE`/`check-access`,
utils/auth/dependencies.py). This table + utils/db/knowledge_invitations.py +
utils/auth/permissions.py replace that call entirely — see
utils/auth/dependencies.py::require_permission's non-owner branch.

Schema differences vs. the source's `agent_invitations` (both are
consequences of this service's simpler ownership model, not omissions):
  - Integer SERIAL `id`, not a `gen_random_uuid()` UUID primary key — matches
    every other table added by this service's own prior migrations
    (api_keys, knowledge_health_events, ...), none of which use a UUID PK.
  - No `changed_fields` JSONB audit-trail column on role updates — this
    service already has a universal request-level audit log
    (`knowledge_audit_log`, migration 017) covering "who did what, when" for
    every mutating endpoint, so a second, table-specific trail would
    duplicate it without adding information.
  - No FK constraints on `invited_user_email`/`invited_by_email` (mirrors
    the source, which drops these at runtime too — see its
    `_ensure_invited_user_fk_dropped`/`_ensure_invited_by_fk_dropped`): this
    service has no local `users` table to reference in the first place.

Run from the repo root:
    python migrations/037_create_knowledge_invitations.py upgrade
    python migrations/037_create_knowledge_invitations.py downgrade
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
    """Create the knowledge_invitations table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS knowledge_invitations (
                id SERIAL PRIMARY KEY,
                knowledge_id TEXT NOT NULL,
                invited_user_email TEXT NOT NULL,
                invited_by_email TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',

                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_by TEXT,
                updated_at TIMESTAMPTZ,

                CONSTRAINT unique_knowledge_invitation UNIQUE (knowledge_id, invited_user_email)
            )
            """
        ))
        # knowledge_id: every read (list invitations) and the require_permission
        # role lookup filters by knowledge_id first.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_invitations_knowledge_id "
            "ON knowledge_invitations (knowledge_id)"
        ))
        # invited_user_email: require_permission's role lookup filters by
        # (knowledge_id, lower(invited_user_email)) on every non-owner request.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_invitations_invited_user_email "
            "ON knowledge_invitations (invited_user_email)"
        ))
    await engine.dispose()
    print(
        "Created knowledge_invitations table with "
        "idx_knowledge_invitations_knowledge_id / "
        "idx_knowledge_invitations_invited_user_email indexes and "
        "unique_knowledge_invitation constraint"
    )
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop the knowledge_invitations table."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS knowledge_invitations"))
    await engine.dispose()
    print("Dropped knowledge_invitations table")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 037_create_knowledge_invitations.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
