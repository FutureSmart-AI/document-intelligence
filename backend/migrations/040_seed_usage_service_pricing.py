"""Migration 040: Seed the usage_service_pricing catalog from config/usage/model_prices.yaml.

WHY: migration 035 created usage_service_pricing as DDL only — zero rows. With no
pricing rows, CostCalculator.get_pricing() (utils/usage/cost_calculator.py) returns
None for every lookup, so every event's cost computes to $0.00 and the Usage
dashboard shows $0.00 across the board. The source service fs-agent-usage-limits
never seeded pricing in code either — those rows lived only in its production DB,
mirrored by config/model_prices.yaml. This migration restores that baseline the same
way: it loads the YAML (copied verbatim into config/usage/) as an open-ended price row
(effective_to = NULL) backdated to 2020-01-01 (SEED_EFFECTIVE_FROM) so it also applies
retroactively to every pre-existing usage_events row, not just future ones.

See docs/plan/usage-and-limits/archive/design-cost-pricing-seed.md.

Idempotent, and this is also the price-update mechanism (docs/plan/enrichment-analytics/
decisions.md notes prices are updated manually: edit the YAML, re-run this script):
for each row, if an OPEN row (effective_to IS NULL) already exists for its
(service_name, provider, service_type) key:
  - same pricing_model + pricing_details as the YAML -> skip, nothing changed.
  - different -> SUPERSEDE: close the existing row (effective_to = now) and insert
    the new figures as a fresh open row (effective_from = now). This is what makes
    re-running the script after editing the YAML actually take effect — the earlier
    version of this script only ever checked "does an open row exist", so an edited
    price would silently never apply; CostCalculator would keep pricing every new
    event, historical or not, off the stale row forever.
No open row yet for that key -> initial seed, backdated to SEED_EFFECTIVE_FROM so it
also prices pre-existing usage_events rows, not just future ones.

Reversible only for the initial seed: downgrade removes rows with
change_reason = 'initial seed from model_prices.yaml'. It does NOT reopen rows closed
by a supersede — undoing a price update means restoring the old figure in the YAML
and re-running upgrade, which supersedes forward again; reopening a closed row would
misprice every event billed against the closed price in between.

Run from the repo root:
    python migrations/040_seed_usage_service_pricing.py upgrade
    python migrations/040_seed_usage_service_pricing.py downgrade
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
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

PRICES_YAML = ROOT / "config" / "usage" / "model_prices.yaml"
SEED_REASON = "initial seed from model_prices.yaml"
SUPERSEDE_REASON = "price update from model_prices.yaml"

# Backdated, not "now": CostCalculator.get_pricing() requires effective_from <= event.timestamp.
# Seeding with effective_from = now would leave every pre-existing usage_events row unpriced
# (still $0.00) since its timestamp predates the price row. Backdating to well before any real
# usage makes this seed apply retroactively to historical events too, not just future ones.
SEED_EFFECTIVE_FROM = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _load_price_rows() -> list[dict]:
    """Read model_prices.yaml -> list of pricing rows, validating required keys."""
    with open(PRICES_YAML, "r") as fh:
        data = yaml.safe_load(fh) or {}
    models = data.get("models", [])
    rows = []
    required = ("service_name", "provider", "service_type", "pricing_model", "pricing_details")
    for i, m in enumerate(models):
        missing = [k for k in required if not m.get(k)]
        if missing:
            raise ValueError(f"model_prices.yaml row {i} missing required key(s) {missing}: {m!r}")
        rows.append(m)
    return rows


def _details_match(existing_details, existing_model: str, new_details: dict, new_model: str) -> bool:
    """Whether an existing open row already reflects this YAML row's figures.

    asyncpg decodes JSONB via SQLAlchemy's registered codec, so `existing_details` is
    normally already a dict — but a raw text() query on some driver paths can hand back
    the JSONB column as its raw string instead, so parse defensively either way.
    """
    if isinstance(existing_details, str):
        existing_details = json.loads(existing_details)
    return existing_model == new_model and existing_details == new_details


async def upgrade() -> None:
    rows = _load_price_rows()
    engine = create_async_engine(DATABASE_URL, echo=False)
    now = datetime.now(timezone.utc)
    inserted = superseded = skipped = 0
    async with engine.begin() as conn:
        for m in rows:
            existing = await conn.execute(
                text("""
                    SELECT pricing_id, pricing_model, pricing_details
                    FROM usage_service_pricing
                    WHERE service_name = :service_name AND provider = :provider
                      AND service_type = :service_type AND effective_to IS NULL
                    LIMIT 1
                """),
                {
                    "service_name": m["service_name"],
                    "provider": m["provider"],
                    "service_type": m["service_type"],
                },
            )
            row = existing.mappings().one_or_none()

            if row is not None and _details_match(
                row["pricing_details"], row["pricing_model"], m["pricing_details"], m["pricing_model"]
            ):
                skipped += 1
                continue

            previous_pricing_id = None
            if row is not None:
                # Supersede: close the stale row before the new one opens, so no two
                # open rows ever exist for the same key at once (get_pricing()'s
                # "effective_to IS NULL" lookup assumes exactly one).
                #
                # The PK of this table is `pricing_id`, NOT `id` (migration 035).
                # An earlier revision of this block named `id` in both the SELECT and
                # this UPDATE, which meant the supersede path raised
                # UndefinedColumnError the first time a price actually changed —
                # i.e. exactly the case the supersede logic was added for. The
                # initial-seed path never touched the column, so the script looked
                # healthy for as long as every YAML row was new.
                await conn.execute(
                    text("UPDATE usage_service_pricing SET effective_to = :now, "
                         "updated_at = :now WHERE pricing_id = :pricing_id"),
                    {"now": now, "pricing_id": row["pricing_id"]},
                )
                # SCD-2 lineage. The schema declares
                # previous_pricing_id REFERENCES usage_service_pricing(pricing_id)
                # precisely so a price history is walkable; leaving it NULL threw
                # that away and made two rows for the same key look unrelated.
                previous_pricing_id = row["pricing_id"]
                effective_from = now
                change_reason = SUPERSEDE_REASON
                superseded += 1
            else:
                # No prior row for this key at all: seed it, backdated so it also
                # prices pre-existing usage_events rows, not just future ones.
                effective_from = SEED_EFFECTIVE_FROM
                change_reason = SEED_REASON
                inserted += 1

            await conn.execute(
                text("""
                    INSERT INTO usage_service_pricing
                        (service_name, service_type, provider, pricing_model,
                         effective_from, effective_to, pricing_details, change_reason,
                         previous_pricing_id, created_by)
                    VALUES
                        (:service_name, :service_type, :provider, :pricing_model,
                         :effective_from, NULL, CAST(:pricing_details AS JSONB), :change_reason,
                         :previous_pricing_id, :created_by)
                """),
                {
                    "service_name": m["service_name"],
                    "service_type": m["service_type"],
                    "provider": m["provider"],
                    "pricing_model": m["pricing_model"],
                    "effective_from": effective_from,
                    "pricing_details": json.dumps(m["pricing_details"]),
                    "change_reason": change_reason,
                    # NULL on an initial seed, the closed row's pricing_id on a supersede.
                    "previous_pricing_id": previous_pricing_id,
                    "created_by": "migration_040",
                },
            )
    await engine.dispose()
    print(f"Migration 040 upgrade completed: inserted {inserted} new, superseded {superseded} "
          f"changed, skipped {skipped} unchanged. Total in YAML: {len(rows)}.")


async def downgrade() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM usage_service_pricing WHERE change_reason = :reason"),
            {"reason": SEED_REASON},
        )
        deleted = result.rowcount if result.rowcount is not None else "?"
    await engine.dispose()
    print(f"Migration 040 downgrade completed: deleted {deleted} seeded pricing row(s) "
          f"(change_reason = {SEED_REASON!r}).")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 040_seed_usage_service_pricing.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
