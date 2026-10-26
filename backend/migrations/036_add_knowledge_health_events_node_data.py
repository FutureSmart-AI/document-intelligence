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
    """Add node_data to knowledge_health_events (per-node Agent Health
    breakdown — docs/plan/analytics-and-health/events.md's "Known gaps"
    table, `perf.by_node`/`cost.by_node`).

    One entry per node that ran during the turn: {node_name, provider, model,
    calls, errors, latency_ms, input_tokens, output_tokens, total_tokens,
    cost_usd} — built at emit time (chat/db/health_events.py) from the
    graph's already-tagged `service_calls` (see chat/rag_agent/
    agent_execution.py and chat/chart_node.py, which now tag each LLM call
    with the node that made it: refiner / rag_agent / generate_answer /
    chart). Stored per-turn so both the scorecard aggregation and the
    per-query drill-down read from the same column — no second write path.
    """
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE knowledge_health_events "
            "ADD COLUMN IF NOT EXISTS node_data JSONB NOT NULL DEFAULT '[]'"
        ))
    await engine.dispose()
    print("Added knowledge_health_events.node_data column")
    print("Migration upgrade completed.")


async def downgrade() -> None:
    """Drop node_data column."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE knowledge_health_events DROP COLUMN IF EXISTS node_data"
        ))
    await engine.dispose()
    print("Dropped knowledge_health_events.node_data column")
    print("Migration downgrade completed.")


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").lower()
    if action not in {"upgrade", "downgrade"}:
        print("Usage: python 036_add_knowledge_health_events_node_data.py [upgrade|downgrade]")
        sys.exit(1)
    if action == "upgrade":
        asyncio.run(upgrade())
    else:
        asyncio.run(downgrade())


if __name__ == "__main__":
    main()
