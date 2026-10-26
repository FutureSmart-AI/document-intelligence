"""Migration 039: Re-scope usage tracking + BYOK credentials from knowledge_id-primary
to owner_id-primary (reverses the 2026-08-17 decision recorded in
docs/plan/usage-and-limits/decisions.md — see the 2026-08-19 addendum there
for why: usage needs to be reportable at the owner level, and a BYOK credential is
naturally an owner-wide thing, not one per knowledge base).

Changes:
  - usage_events: backfill owner_id from knowledge_config where missing, then make it
    the required column. Events with no resolvable owner_id (no knowledge_id, or a
    knowledge_id whose knowledge_config row no longer exists) are deleted — there is
    no owner to attribute them to under the new model. Count is printed, not silent.
  - knowledge_provider_keys -> renamed owner_provider_keys: backfill owner_id the same
    way, collapse to one row per (owner_id, provider) — the source table allowed one
    key per (knowledge_id, provider), so an owner with the same provider key entered
    under two knowledge bases now collides. The most-recently-updated row wins; the
    superseded ones are deleted and printed (knowledge_id, provider, owner_id) so the
    loss is visible, not silent. knowledge_id becomes an optional, informational
    column (which knowledge base last touched this credential), no longer part of
    identity.

No backward compatibility is preserved by design (explicit instruction, 2026-08-19)
- there is no dual-read/dual-write path and no fallback to knowledge_id-primary
  lookups once this lands.

Run from the repo root:
    python migrations/039_scope_usage_by_owner.py upgrade
    python migrations/039_scope_usage_by_owner.py downgrade

Downgrade renames the table back and restores the old constraint, but CANNOT
restore rows deleted during upgrade (unresolvable events, superseded credentials) —
those are gone. Take a snapshot/backup before running upgrade if that matters.
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
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        # --- usage_events: backfill, then drop what still can't resolve an owner ---
        await conn.execute(text("""
            UPDATE usage_events e
            SET owner_id = kc.owner_id
            FROM knowledge_config kc
            WHERE e.owner_id IS NULL AND e.knowledge_id = kc.knowledge_id AND kc.owner_id IS NOT NULL
        """))
        orphaned_events = await conn.execute(text("SELECT count(*) FROM usage_events WHERE owner_id IS NULL"))
        orphaned_count = orphaned_events.scalar_one()
        if orphaned_count:
            print(f"Deleting {orphaned_count} usage_events row(s) with no resolvable owner_id (no owner-level home for them under the new model).")
        await conn.execute(text("DELETE FROM usage_metrics WHERE event_id IN (SELECT event_id FROM usage_events WHERE owner_id IS NULL)"))
        await conn.execute(text("DELETE FROM usage_events WHERE owner_id IS NULL"))
        await conn.execute(text("ALTER TABLE usage_events ALTER COLUMN owner_id SET NOT NULL"))

        # --- knowledge_provider_keys: backfill owner_id ---
        await conn.execute(text("""
            UPDATE knowledge_provider_keys kpk
            SET owner_id = kc.owner_id
            FROM knowledge_config kc
            WHERE kpk.owner_id IS NULL AND kpk.knowledge_id = kc.knowledge_id AND kc.owner_id IS NOT NULL
        """))
        orphaned_creds = await conn.execute(text("SELECT count(*) FROM knowledge_provider_keys WHERE owner_id IS NULL"))
        orphaned_creds_count = orphaned_creds.scalar_one()
        if orphaned_creds_count:
            print(f"Deleting {orphaned_creds_count} knowledge_provider_keys row(s) with no resolvable owner_id.")
        await conn.execute(text("DELETE FROM knowledge_provider_keys WHERE owner_id IS NULL"))

        # --- collapse to one row per (owner_id, provider): most-recently-updated wins ---
        superseded = await conn.execute(text("""
            SELECT knowledge_id, provider, owner_id FROM knowledge_provider_keys kpk
            WHERE id NOT IN (
                SELECT DISTINCT ON (owner_id, provider) id FROM knowledge_provider_keys
                ORDER BY owner_id, provider, updated_at DESC, id DESC
            )
        """))
        superseded_rows = superseded.fetchall()
        if superseded_rows:
            print(f"Collapsing {len(superseded_rows)} duplicate (owner_id, provider) credential row(s) — superseded by a more recently updated key from a different knowledge base:")
            for row in superseded_rows:
                print(f"  dropped: knowledge_id={row.knowledge_id!r} provider={row.provider!r} owner_id={row.owner_id!r}")
        await conn.execute(text("""
            DELETE FROM knowledge_provider_keys
            WHERE id NOT IN (
                SELECT DISTINCT ON (owner_id, provider) id FROM knowledge_provider_keys
                ORDER BY owner_id, provider, updated_at DESC, id DESC
            )
        """))

        await conn.execute(text("ALTER TABLE knowledge_provider_keys DROP CONSTRAINT IF EXISTS uq_knowledge_provider_keys_knowledge_provider"))
        await conn.execute(text("DROP INDEX IF EXISTS idx_knowledge_provider_keys_knowledge_provider"))
        await conn.execute(text("ALTER TABLE knowledge_provider_keys ALTER COLUMN owner_id SET NOT NULL"))
        await conn.execute(text("ALTER TABLE knowledge_provider_keys ALTER COLUMN knowledge_id DROP NOT NULL"))
        await conn.execute(text("ALTER TABLE knowledge_provider_keys RENAME TO owner_provider_keys"))
        await conn.execute(text("ALTER TABLE owner_provider_keys ADD CONSTRAINT uq_owner_provider_keys_owner_provider UNIQUE (owner_id, provider)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_owner_provider_keys_owner_provider ON owner_provider_keys (owner_id, provider)"))

    await engine.dispose()
    print("Migration 039 upgrade completed: usage_events.owner_id is now required; "
          "knowledge_provider_keys is now owner_provider_keys, keyed by (owner_id, provider).")


async def downgrade() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE usage_events ALTER COLUMN owner_id DROP NOT NULL"))

        await conn.execute(text("ALTER TABLE owner_provider_keys DROP CONSTRAINT IF EXISTS uq_owner_provider_keys_owner_provider"))
        await conn.execute(text("DROP INDEX IF EXISTS idx_owner_provider_keys_owner_provider"))
        await conn.execute(text("ALTER TABLE owner_provider_keys ALTER COLUMN knowledge_id SET NOT NULL"))
        await conn.execute(text("ALTER TABLE owner_provider_keys RENAME TO knowledge_provider_keys"))
        await conn.execute(text("ALTER TABLE knowledge_provider_keys ADD CONSTRAINT uq_knowledge_provider_keys_knowledge_provider UNIQUE (knowledge_id, provider)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_knowledge_provider_keys_knowledge_provider ON knowledge_provider_keys (knowledge_id, provider)"))
    await engine.dispose()
    print("Migration 039 downgrade completed. NOTE: rows deleted during upgrade "
          "(unresolvable events, superseded credentials) were NOT restored — they're gone.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 039_scope_usage_by_owner.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
