"""Replace an enrichment attempt's estimated cost with what the provider billed.

WHY
---
Chat cost is finalized from LangSmith; enrichment cost was not. That asymmetry
had no reason behind it but ordering — the LangSmith finalizer arrived on this
service after the enrichment analytics feature was already built, so enrichment
kept pricing itself at READ time from `usage_service_pricing` via
`CostCalculator` and nobody went back.

The estimate is wrong in a way that does not announce itself. It is only as
current as `config/usage/model_prices.yaml`, and a model missing from that file
prices at NOTHING rather than raising: `gemini-3.1-flash-lite` was absent, so
every document it enriched reported `cost_usd: null` while sitting in the
model-comparison table as the cheapest option available. DeepSeek's figures are
~3x stale for the same reason. Where the same comparison could be made on the
chat side, the local estimate ran 2.9x high on a real turn (0.0286 against
0.00980544).

LangSmith prices from what the provider actually charged, so it is correct by
construction rather than correct until somebody edits a YAML file.

FIRE-AND-FORGET, NOT A BUTTON
-----------------------------
`schedule_enrichment_finalize` is launched the moment an enrichment event is
recorded, mirroring what `routers/chat.py` does per chat turn. Nobody should have
to press sync to make a cost figure true. The manual endpoint and the periodic
sweeper are the safety nets for when this attempt loses — LangSmith prices runs
asynchronously, so an immediate read returns tokens but no cost, and the retry
ladder below is what waits it out.

WHAT IT WRITES, AND WHAT IT REFUSES TO OVERWRITE
------------------------------------------------
It sets `cost_usd` and `cost_complete` and leaves the worker-reported token
columns alone. Overwriting those would destroy the only record of what the local
estimate claimed, and the gap between the two is the entire evidence that
finalizing is worth doing.

On the last attempt with the run still unpriced it stamps `cost_complete = False`
and leaves `cost_usd` NULL rather than writing a zero. A 0.0 would make the
document look free and win every cost comparison it appeared in — the same trap
the unpriced-model case already demonstrated.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import and_, select, update

from utils.db.session import async_session
from utils.logger import logger
from utils.models.db_tables import KnowledgeEnrichmentEvent as EV
from utils.setup_env import (
    CHAT_COST_FINALIZE_DELAYS,
    ENRICHMENT_COST_FINALIZE_ENABLED,
)


async def _persist(event_key: str, cost_usd: Optional[float], complete: bool) -> None:
    """Write the finalized figures onto one attempt.

    A targeted UPDATE naming only these columns, so a cost pass structurally
    cannot disturb the enrichment measurements on the same row.
    """
    async with async_session() as session:
        await session.execute(
            update(EV.__table__)
            .where(EV.__table__.c.event_key == event_key)
            .values(cost_usd=cost_usd, cost_complete=complete)
        )
        await session.commit()


async def finalize_enrichment_cost(*, event_key: str, run_id: Optional[str]) -> bool:
    """Price one enrichment attempt from LangSmith. Returns True once complete.

    Retries on the same ladder the chat finalizer uses
    (`CHAT_COST_FINALIZE_DELAYS`, 20/40/90s) because the thing being waited on is
    identical — LangSmith's asynchronous pricing of a run, not anything specific
    to chat.

    Never raises: this runs detached from the request that triggered it, so an
    exception here has nowhere to be handled and must not become an unretrieved
    task exception.
    """
    if not ENRICHMENT_COST_FINALIZE_ENABLED or not event_key or not run_id:
        return False
    try:
        from chat.langsmith_cost import fetch_run_usage

        last: Dict[str, Any] = {}
        for i, delay in enumerate(CHAT_COST_FINALIZE_DELAYS):
            await asyncio.sleep(delay)
            # The LangSmith SDK client is synchronous; off-thread so this never
            # blocks the event loop.
            last = await asyncio.to_thread(fetch_run_usage, run_id) or {}
            cost = last.get("cost_usd")
            if cost is not None:
                await _persist(event_key, float(cost), True)
                logger.info(
                    f"[enrichment-cost] event_key={event_key} finalized cost_usd={cost} "
                    f"(attempt {i + 1}/{len(CHAT_COST_FINALIZE_DELAYS)})"
                )
                return True

        # Ladder exhausted with the run still unpriced. Record the outcome rather
        # than silently leaving the row looking un-attempted, but do NOT write a
        # zero -- see the module docstring.
        await _persist(event_key, None, False)
        logger.info(
            f"[enrichment-cost] event_key={event_key} still unpriced after "
            f"{len(CHAT_COST_FINALIZE_DELAYS)} attempts; left to the sweeper"
        )
        return False
    except Exception as e:
        logger.error(
            f"[enrichment-cost] event_key={event_key} failed: {type(e).__name__}: {e}",
            exc_info=True,
        )
        return False


def schedule_enrichment_finalize(*, event_key: str, run_id: Optional[str]) -> None:
    """Launch finalization in the background and return immediately.

    Called from the internal event endpoint so the worker's POST is not held open
    for ~150s of retry ladder. The task reference is deliberately not retained:
    there is nothing to await it, and the coroutine swallows its own exceptions.
    """
    if not ENRICHMENT_COST_FINALIZE_ENABLED or not run_id:
        return
    try:
        asyncio.create_task(finalize_enrichment_cost(event_key=event_key, run_id=run_id))
    except RuntimeError:
        # No running loop (a sync context, or shutdown in progress). The sweeper
        # will pick the row up; losing the eager attempt is not worth an error.
        logger.warning(
            f"[enrichment-cost] no running loop to schedule event_key={event_key}"
        )


async def backfill_enrichment_costs(
    knowledge_id: str,
    since: datetime,
    until: datetime,
    *,
    only_incomplete: bool = True,
    limit: int = 200,
) -> Dict[str, Any]:
    """Price a window's attempts by direct `read_run` on each stored run id.

    Used by the manual endpoint and by the periodic sweeper. Reads each run
    ONCE with no retry ladder — a caller running on a timer gets its retry for
    free by running again later, and sleeping 150s per row inside a sweep would
    make a backlog take hours.

    Rows with no run id are counted as `skipped`, never as failures: they were
    written before migration 047 or with tracing off, and no amount of retrying
    creates a run id after the fact.
    """
    filters = [
        EV.knowledge_id == knowledge_id,
        EV.created_at >= since,
        EV.created_at < until,
    ]
    if only_incomplete:
        filters.append(EV.cost_complete.is_(False))

    out = {"matched": 0, "updated": 0, "unpriced": 0, "skipped_no_run_id": 0, "errors": 0}
    try:
        from chat.langsmith_cost import fetch_run_usage
    except Exception as e:
        logger.error(f"[enrichment-cost] LangSmith unavailable: {type(e).__name__}: {e}")
        out["errors"] += 1
        return out

    async with async_session() as session:
        rows = (await session.execute(
            select(EV).where(and_(*filters)).order_by(EV.created_at.desc()).limit(limit)
        )).scalars().all()

    out["matched"] = len(rows)
    for ev in rows:
        if not ev.langsmith_run_id:
            out["skipped_no_run_id"] += 1
            continue
        try:
            usage = await asyncio.to_thread(fetch_run_usage, ev.langsmith_run_id) or {}
            cost = usage.get("cost_usd")
            if cost is None:
                out["unpriced"] += 1
                continue
            await _persist(str(ev.event_key), float(cost), True)
            out["updated"] += 1
        except Exception as e:
            out["errors"] += 1
            logger.error(
                f"[enrichment-cost] event_key={ev.event_key} backfill failed: "
                f"{type(e).__name__}: {e}"
            )
    logger.info(f"[enrichment-cost] backfill knowledge_id={knowledge_id}: {out}")
    return out
