"""Replace each chat turn's ESTIMATED cost with what LangSmith actually charged.

`chat/pricing.py` prices a turn inline at emit time from a hardcoded per-1K
price map. That number is a placeholder, not an answer: the map covers ~23
models, goes stale silently whenever a provider changes a published price, and
contributes nothing at all for a model it has never heard of. This module is the
half of the donor service's design (fs-agent-chat) that the analytics-and-health
migration left behind — see docs/plan/analytics-and-health/decisions.md.

Two ways in, both idempotent and both keyed on `chat_id`:

* `finalize_query_cost` — fire-and-forget, one call per turn, launched from the
  chat router. This is the path that matters: it means cost is correct without
  anyone asking. Re-reads with backoff because LangSmith prices runs
  ASYNCHRONOUSLY, so the run exists (with tokens) well before it has a cost.
* `backfill_costs_by_run_id` / `backfill_costs_from_langsmith` — window-scoped
  repair for turns the finalizer missed (a restart mid-backoff, rows written
  before migration 045, tracing switched off for a while). Exposed as
  `POST /v1/{knowledge_id}/health/sync-costs`.

**Tokens and cost are written on different conditions.** Tokens land as soon as
LangSmith reports them; `cost_usd` is only written once actually priced, and
`cost_complete` records whether the figure is final or a lower bound. Writing a
0.0 for "not priced yet" would overwrite a real estimate with a confident lie —
the same "never silently zero" rule chat/pricing.py::price_breakdown follows.

Nothing here raises. Cost analytics is not worth a failed chat turn or a 500 on
an endpoint that is otherwise fine.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, select, update

from utils.db.session import async_session
from utils.logger import logger
from utils.models.db_tables import KnowledgeHealthEvent
from utils.setup_env import (
    CHAT_COST_FINALIZE_CONCURRENCY,
    CHAT_COST_FINALIZE_DELAYS,
    CHAT_COST_FINALIZE_ENABLED,
)

_E = KnowledgeHealthEvent

# Bounds concurrent LangSmith reads process-wide. The SDK client is synchronous,
# so every read occupies a thread in the loop's shared default executor — an
# unbounded fan-out across simultaneous turns would starve every other
# to_thread caller in the service, not just this module.
_FINALIZE_SEM = asyncio.Semaphore(CHAT_COST_FINALIZE_CONCURRENCY)


# ── row writers ───────────────────────────────────────────────────────────────

def _as_uuid(chat_id: Any):
    """chat_id is a PG UUID column; a str from the router must be coerced or the
    comparison silently matches nothing on asyncpg."""
    import uuid as _uuid
    return chat_id if isinstance(chat_id, _uuid.UUID) else _uuid.UUID(str(chat_id))


async def update_row_usage_from_langsmith(
    chat_id: str, usage: Dict[str, Any], cost_complete: Optional[bool] = None,
) -> bool:
    """Write ONE row's tokens + cost from a LangSmith usage result.

    Tokens are written whenever present; `cost_usd` only when present.
    `cost_complete` defaults to True when a cost is written — pass False for a
    partial/unpriced total so the dashboard keeps flagging it as a lower bound.
    Idempotent. Returns True iff a row was updated; never raises.
    """
    if not chat_id or not usage:
        return False
    try:
        vals = _usage_to_values(usage)
        # cost_complete is set independently of whether a cost was written. The
        # case that matters is giving up on an unpriced run: we keep the local
        # estimate (nulling a usable number would lose information LangSmith
        # never contradicted) but stamp cost_complete=False so the figure reads
        # as unverified rather than silently keeping the estimate's own
        # "complete" claim.
        if cost_complete is not None:
            vals["cost_complete"] = bool(cost_complete)
        if not vals:
            return False
        async with async_session() as session:
            res = await session.execute(
                update(_E).where(_E.chat_id == _as_uuid(chat_id)).values(**vals)
            )
            await session.commit()
        return bool(res.rowcount)
    except Exception as e:
        logger.error(f"[cost_finalizer] update_row_usage_from_langsmith error: {e}", exc_info=True)
        return False


async def update_row_node_data(chat_id: str, node_data: List[Dict[str, Any]]) -> bool:
    """Replace the row's per-node breakdown with the LangSmith-derived one.

    chat/langsmith_cost.py::fetch_node_data deliberately emits this repo's
    node_data shape (`cost_usd` as the cost key, node names matching
    chat/utils/service_calls.py's tags), so the by_node aggregation keeps
    working unchanged across the swap. Idempotent; never raises.
    """
    if not chat_id or not node_data:
        return False
    try:
        async with async_session() as session:
            res = await session.execute(
                update(_E).where(_E.chat_id == _as_uuid(chat_id)).values(node_data=node_data)
            )
            await session.commit()
        return bool(res.rowcount)
    except Exception as e:
        logger.error(f"[cost_finalizer] update_row_node_data error: {e}", exc_info=True)
        return False


def _usage_to_values(usage: Dict[str, Any]) -> Dict[str, Any]:
    """The column values one LangSmith usage result implies, or {} for none.

    Shared by the single-row writer and the batched backfill writer so both
    apply exactly the same tokens-always / cost-only-when-priced rule.
    """
    vals: Dict[str, Any] = {}
    it, ot = usage.get("input_tokens"), usage.get("output_tokens")
    if it is not None or ot is not None:
        it, ot = int(it or 0), int(ot or 0)
        vals.update(input_tokens=it, output_tokens=ot, total_tokens=it + ot)
    c = usage.get("cost_usd")
    if c is not None:
        vals.update(cost_usd=float(c), cost_complete=True)
    return vals


async def _apply_usages(pairs: List[tuple]) -> tuple:
    """Apply many (chat_id, usage) updates in ONE transaction.

    The backfills can span hundreds of rows in a window; a session per row would
    make the endpoint's cost dominated by transaction round-trips rather than by
    the LangSmith reads it actually exists to do. Returns (rows_updated, total_cost).
    """
    updated = 0
    total = 0.0
    if not pairs:
        return 0, 0.0
    try:
        async with async_session() as session:
            for chat_id, usage in pairs:
                vals = _usage_to_values(usage)
                if not vals:
                    continue  # neither tokens nor cost reported — leave the row alone
                try:
                    where_id = _as_uuid(chat_id)
                except Exception:
                    logger.warning(f"[cost_finalizer] skipping unparseable chat_id={chat_id!r}")
                    continue
                res = await session.execute(update(_E).where(_E.chat_id == where_id).values(**vals))
                updated += res.rowcount or 0
                if "cost_usd" in vals:
                    total += vals["cost_usd"]
            await session.commit()
    except Exception as e:
        logger.error(f"[cost_finalizer] _apply_usages error: {e}", exc_info=True)
        return updated, round(total, 6)
    return updated, round(total, 6)


# ── per-turn finalizer ────────────────────────────────────────────────────────

async def finalize_query_cost(*, chat_id: str, run_id: Optional[str]) -> bool:
    """Attach LangSmith's cost + tokens + per-node breakdown to one turn's row.

    Launched fire-and-forget per turn so cost becomes accurate with no manual
    sync. Returns True once a COMPLETE (priced) cost is written; on the final
    attempt it still persists whatever tokens/partial cost exist, marked
    `cost_complete=False`, rather than leaving the row untouched.

    Unlike the donor's version there is no multi-run summation: this service's
    turns never fan out to separately-traced sub-services (`services_used` is
    always `["knowledge"]`), so one root run covers the whole turn.
    """
    if not CHAT_COST_FINALIZE_ENABLED or not chat_id or not run_id:
        return False
    try:
        from chat.langsmith_cost import fetch_node_data, fetch_run_usage

        for i, delay in enumerate(CHAT_COST_FINALIZE_DELAYS):
            await asyncio.sleep(delay)
            async with _FINALIZE_SEM:
                usage = await asyncio.to_thread(fetch_run_usage, run_id)
            if not usage:
                continue  # read failed or run not visible yet — try again
            priced = usage.get("cost_usd") is not None
            last = i == len(CHAT_COST_FINALIZE_DELAYS) - 1
            if not (priced or last):
                continue  # tokens are in but no cost yet; wait for pricing
            await update_row_usage_from_langsmith(chat_id, usage, cost_complete=priced)
            # Only rebuild node_data from a PRICED trace. Doing it unpriced would
            # replace the local per-node estimates with a set of null costs —
            # strictly less useful than what the row already had.
            if priced:
                async with _FINALIZE_SEM:
                    nd = await asyncio.to_thread(fetch_node_data, run_id)
                if nd:
                    await update_row_node_data(chat_id, nd)
            else:
                nd = None
            logger.info(
                f"[cost_finalizer] finalized chat_id={chat_id} priced={priced} "
                f"cost={usage.get('cost_usd')} nodes={len(nd or [])} "
                f"attempt={i + 1}/{len(CHAT_COST_FINALIZE_DELAYS)}"
            )
            return priced
        logger.info(f"[cost_finalizer] gave up on chat_id={chat_id} — no LangSmith usage returned")
        return False
    except Exception as e:
        logger.error(f"[cost_finalizer] finalize_query_cost error: {e}", exc_info=True)
        return False


# ── window backfills (the sync-costs endpoint) ────────────────────────────────

async def backfill_costs_by_run_id(
    knowledge_id: str, period: str = "week",
    start: Optional[datetime] = None, end: Optional[datetime] = None,
    only_incomplete: bool = True,
) -> Dict[str, Any]:
    """Price a window's rows by a DIRECT LangSmith `read_run` on each row's
    stored `langsmith_run_id`.

    The robust strategy: no `LANGCHAIN_PROJECT` and no metadata filter, so it
    works regardless of project configuration. Only covers rows that captured a
    run id — rows predating migration 045, or written with tracing off, are
    skipped and need the metadata strategy instead.

    `only_incomplete` (default True) limits work to rows still lacking a
    complete cost; pass False to re-price everything in the window.
    """
    from utils.knowledge_health.analytics import _resolve_window
    from chat.langsmith_cost import fetch_run_usage

    since, until, _prev = _resolve_window(period, start, end)

    conds = [_E.knowledge_id == knowledge_id, _E.created_at >= since, _E.created_at < until,
             _E.langsmith_run_id.isnot(None)]
    if only_incomplete:
        conds.append(_E.cost_complete.is_(False))

    # Short read-only session first — do NOT hold a DB connection open across the
    # network reads below.
    async with async_session() as session:
        rows = (await session.execute(
            select(_E.chat_id, _E.langsmith_run_id).where(and_(*conds))
        )).all()

    candidates = [(r.chat_id, r.langsmith_run_id) for r in rows]
    if not candidates:
        logger.info(f"[cost_finalizer] run-id backfill knowledge_id={knowledge_id}: no candidates")
        return {"knowledge_id": knowledge_id, "period": period, "source": "langsmith_run_id",
                "candidates": 0, "rows_updated": 0, "rows_fetch_failed": 0, "total_cost": 0.0}

    sem = asyncio.Semaphore(CHAT_COST_FINALIZE_CONCURRENCY)

    async def _read(run_id: str) -> Dict[str, Any]:
        async with sem:
            try:
                return await asyncio.to_thread(fetch_run_usage, run_id)
            except Exception:  # fetch_run_usage already guards; belt and braces
                return {}

    usages = await asyncio.gather(*[_read(rid) for (_cid, rid) in candidates])

    fetch_failed = sum(1 for u in usages if not u)
    pairs = [(cid, u) for (cid, _rid), u in zip(candidates, usages) if u]
    updated, total = await _apply_usages(pairs)

    # A silent run that "succeeds" while leaving most rows unpriced is the failure
    # mode worth shouting about (expired key, wrong workspace, rate limit).
    if fetch_failed > len(candidates) * 0.1:
        logger.warning(
            f"[cost_finalizer] run-id backfill knowledge_id={knowledge_id}: "
            f"{fetch_failed}/{len(candidates)} LangSmith reads returned nothing "
            f"(check LANGSMITH_API_KEY / workspace / run ids)"
        )
    logger.info(
        f"[cost_finalizer] run-id backfill knowledge_id={knowledge_id} period={period} "
        f"candidates={len(candidates)} updated={updated} fetch_failed={fetch_failed} "
        f"total=${total:.4f}"
    )
    return {"knowledge_id": knowledge_id, "period": period, "source": "langsmith_run_id",
            "candidates": len(candidates), "rows_updated": updated,
            "rows_fetch_failed": fetch_failed, "total_cost": round(total, 6)}


async def backfill_costs_from_langsmith(
    knowledge_id: str, period: str = "week",
    start: Optional[datetime] = None, end: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Price a window's rows via ONE server-side LangSmith metadata query.

    Cheaper than a read per row for a wide window, and it reaches turns whose
    row has no `langsmith_run_id`. In exchange it needs `LANGCHAIN_PROJECT` set
    and needs the root run to carry `knowledge_id`/`chat_id` metadata — turns
    that ran before that tagging shipped are invisible to it, which is exactly
    the gap `backfill_costs_by_run_id` covers. Use `strategy=both` to run each
    where it is strong.
    """
    from utils.knowledge_health.analytics import _resolve_window
    from chat.langsmith_cost import fetch_knowledge_costs

    since, until, _prev = _resolve_window(period, start, end)
    costs = await asyncio.to_thread(fetch_knowledge_costs, knowledge_id, since, until)

    updated, total = await _apply_usages(list(costs.items()))

    logger.info(
        f"[cost_finalizer] metadata backfill knowledge_id={knowledge_id} period={period} "
        f"runs={len(costs)} updated={updated} total=${total:.4f}"
    )
    return {"knowledge_id": knowledge_id, "period": period, "source": "langsmith",
            "runs_found": len(costs), "rows_updated": updated, "total_cost": round(total, 6)}
