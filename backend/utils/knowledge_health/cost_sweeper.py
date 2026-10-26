"""Self-firing sweep that finalizes chat costs nobody asked it to finalize.

WHY THIS EXISTS
---------------
Chat cost is already finalized automatically per turn: `routers/chat.py` fires
`finalize_query_cost` as a background task right after the health event is
emitted, so the common case needs no sync and no button.

That per-turn attempt can still lose. LangSmith prices runs asynchronously, so a
turn whose cost has not landed within the configured retry delays
(`CHAT_COST_FINALIZE_DELAYS`, 20/40/90s) gives up; a task in flight dies with a
container restart or a deploy; a transient LangSmith error exhausts its retries;
and any turn recorded while `CHAT_COST_FINALIZE_ENABLED` was false never had an
attempt at all.

Every one of those leaves a row at `cost_complete = false` carrying the LOCAL
estimate — which the live comparison showed running ~2.9x high on a real turn
(0.0286 estimated against 0.00980544 actual). Before this sweeper the only route
back was a human pressing sync on the analytics screen, i.e. a wrong number
persisted for exactly as long as nobody noticed. A cost figure should not depend
on someone remembering to reconcile it.

So the service reconciles its own books on a timer, and `POST /health/sync-costs`
goes back to being what it should be: a manual override for a specific window,
not the only path to a correct number.

WHAT IT COVERS
--------------
Both domains: chat rows in `knowledge_health_events` and enrichment rows in
`knowledge_enrichment_events`. Enrichment was originally excluded here, on the
reasoning that enrichment cost was computed at READ time from
`usage_service_pricing` so there was no asynchronous figure to wait for.
Migration 047 and `enrichment_cost_finalizer` made that false — enrichment rows
now carry a `langsmith_run_id` and a `cost_complete` flag exactly like chat rows.
Leaving them out meant the eager per-document finalize task was the only path to
a finalized enrichment cost, and that task dies with its process, so a deploy
alone was enough to leave a row on a read-time estimate forever.

Two meanings still hide behind `cost_complete: false`, and a sweep fixes only the
first: a run id not yet priced (this module's job), and a model absent from the
price catalog, which needs a YAML edit plus migration 040.

WHAT IT DOES NOT DO
-------------------
It cannot price a row with no stored `langsmith_run_id` — those are counted as
skipped, and no amount of sweeping creates a run id after the fact.

It also does not re-price rows that are already complete. `only_incomplete=True`
is what keeps a sweep every few minutes cheap: it converges on a fixed point and
then does almost nothing, instead of re-reading LangSmith for the whole window
forever.

SAFETY
------
* One sweeper per deployment, via the same Postgres session advisory lock the
  connector runner uses (`utils/connectors/runner.py`). Every replica launches
  the loop; only the lock holder works. Without this, N replicas would each hit
  LangSmith for the same rows.
* A distinct lock key from the connector runner's — sharing one would make the
  two features silently exclude each other.
* A grace period before a row is eligible, so the sweeper never races the
  per-turn finalizer that is probably still retrying.
* An upper age bound, because LangSmith retention is finite: past it a read
  cannot succeed and retrying forever is a fixed cost with no possible benefit.
* Every failure is logged and swallowed. This loop must not be able to take the
  API down.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy import text

from utils.db.session import engine
from utils.logger import logger

# Distinct from the connector runner's 778211 — see the module docstring.
_ADVISORY_LOCK_KEY = 778213

_leader_conn = None


async def _ensure_leader() -> bool:
    """Hold the single-sweeper lock, or report that another process has it.

    The lock lives on a dedicated connection held open for as long as we are
    leader; closing that connection is what releases it.
    """
    global _leader_conn
    if _leader_conn is not None:
        return True
    conn = await engine.connect()
    try:
        got = (await conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": _ADVISORY_LOCK_KEY}
        )).scalar()
    except Exception:
        await conn.close()
        raise
    if got:
        _leader_conn = conn
        logger.info("[cost-sweeper] acquired leader lock — this process owns the sweep")
        return True
    await conn.close()
    return False


async def _release_leader() -> None:
    global _leader_conn
    if _leader_conn is not None:
        try:
            await _leader_conn.close()
        except Exception:
            pass
        _leader_conn = None


# The two event tables the sweeper finalizes, keyed by domain. Both carry
# knowledge_id / cost_complete / langsmith_run_id / created_at, so one query
# shape serves both. Hardcoded literals, never caller input — the table name is
# interpolated into SQL below and this dict is the only source for it.
_SWEEP_TABLES = {
    "chat": "knowledge_health_events",
    "enrichment": "knowledge_enrichment_events",
}


async def _knowledge_ids_needing_sweep(
    domain: str, grace_seconds: int, max_age_hours: int, limit: int
) -> List[Tuple[str, int]]:
    """Knowledge bases holding finalizable rows, worst first.

    Grouped rather than returning individual rows because the finalizer's API is
    per-knowledge-base over a window. Ordering by pending count puts the biggest
    backlog first, so a capped sweep spends its budget where the most figures are
    wrong.

    The window bounds matter in both directions: `> grace` avoids racing the
    per-turn finalizer still in its retry ladder, and `< max_age` skips rows old
    enough that LangSmith retention has likely dropped the run — those can never
    succeed and would otherwise be retried on every pass forever.
    """
    now = datetime.now(timezone.utc)
    newest_eligible = now - timedelta(seconds=grace_seconds)
    oldest_eligible = now - timedelta(hours=max_age_hours)

    async with engine.connect() as conn:
        rows = (await conn.execute(
            text(f"""
                SELECT knowledge_id, COUNT(*) AS pending
                FROM {_SWEEP_TABLES[domain]}
                WHERE cost_complete IS NOT TRUE
                  AND langsmith_run_id IS NOT NULL
                  AND created_at < :newest
                  AND created_at >= :oldest
                GROUP BY knowledge_id
                ORDER BY pending DESC
                LIMIT :limit
            """),
            {"newest": newest_eligible, "oldest": oldest_eligible, "limit": limit},
        )).all()
    return [(r[0], int(r[1])) for r in rows]


async def sweep_once(
    grace_seconds: int, max_age_hours: int, max_knowledge_bases: int
) -> dict:
    """One pass over BOTH domains. Returns a small summary; never raises.

    Enrichment was originally out of scope here, on the grounds that enrichment
    cost was computed at READ time from usage_service_pricing so there was no
    asynchronous figure to wait for. Migration 047 and the enrichment finalizer
    made that false: enrichment rows now carry a langsmith_run_id and a
    cost_complete flag exactly like chat rows, and nothing was finalizing them —
    the eager per-document task was the only path, so a task that died with its
    process (a deploy, for instance) left the row unpriced forever.
    """
    from utils.knowledge_health.cost_finalizer import backfill_costs_by_run_id
    from utils.knowledge_health.enrichment_cost_finalizer import (
        backfill_enrichment_costs,
    )

    async def _chat(knowledge_id: str):
        return await backfill_costs_by_run_id(
            knowledge_id, period="month", only_incomplete=True
        )

    async def _enrichment(knowledge_id: str):
        # Same window the chat path uses, spelled explicitly because this
        # finalizer takes bounds rather than a period name.
        now = datetime.now(timezone.utc)
        return await backfill_enrichment_costs(
            knowledge_id,
            since=now - timedelta(days=30),
            until=now,
            only_incomplete=True,
        )

    runners = {"chat": _chat, "enrichment": _enrichment}

    summary = {"knowledge_bases": 0, "pending_before": 0, "updated": 0, "errors": 0}
    for domain, run in runners.items():
        summary[domain] = await _sweep_domain(
            domain, run, grace_seconds, max_age_hours, max_knowledge_bases, summary
        )
    return summary


async def _sweep_domain(
    domain: str,
    run,
    grace_seconds: int,
    max_age_hours: int,
    max_knowledge_bases: int,
    summary: dict,
) -> dict:
    """One domain's pass. Folds its counts into `summary` and also returns its
    own, so a caller can tell which domain the work came from — a combined
    "updated: 4" hides that enrichment finalized nothing."""
    own = {"knowledge_bases": 0, "pending_before": 0, "updated": 0, "errors": 0}
    try:
        targets = await _knowledge_ids_needing_sweep(
            domain, grace_seconds, max_age_hours, max_knowledge_bases
        )
    except Exception as e:
        logger.error(
            f"[cost-sweeper:{domain}] could not list targets: {type(e).__name__}: {e}"
        )
        summary["errors"] += 1
        own["errors"] += 1
        return own

    if not targets:
        return own

    own["knowledge_bases"] = len(targets)
    own["pending_before"] = sum(p for _, p in targets)
    summary["knowledge_bases"] += own["knowledge_bases"]
    summary["pending_before"] += own["pending_before"]
    logger.info(
        f"[cost-sweeper:{domain}] {len(targets)} knowledge base(s) with "
        f"{own['pending_before']} unfinalized row(s)"
    )

    for knowledge_id, pending in targets:
        try:
            # The window is deliberately wider than the eligibility window:
            # the finalizer filters to incomplete rows itself, so a wider window
            # costs nothing and avoids missing a row that sits just outside a
            # narrower one because of clock skew between this process and the DB.
            result = await run(knowledge_id)
            updated = int((result or {}).get("updated") or 0)
            summary["updated"] += updated
            own["updated"] += updated
            if updated:
                logger.info(
                    f"[cost-sweeper:{domain}] knowledge_id={knowledge_id}: finalized"
                    f" {updated} of {pending} pending row(s)"
                )
        except Exception as e:
            # One bad knowledge base must not end the pass.
            summary["errors"] += 1
            own["errors"] += 1
            logger.error(
                f"[cost-sweeper:{domain}] knowledge_id={knowledge_id} failed: "
                f"{type(e).__name__}: {e}"
            )
    return own


async def run_sweeper_loop(
    interval_seconds: int,
    grace_seconds: int,
    max_age_hours: int,
    max_knowledge_bases: int,
) -> None:
    """Sweep forever. Cancellation-safe; launched from main.py's lifespan.

    A non-leader process sleeps and re-checks rather than exiting, so leadership
    transfers on its own when the current leader is replaced during a deploy —
    no restart of the survivors required.
    """
    logger.info(
        f"[cost-sweeper] loop starting: interval={interval_seconds}s grace={grace_seconds}s "
        f"max_age={max_age_hours}h max_kbs={max_knowledge_bases}"
    )
    try:
        while True:
            try:
                if await _ensure_leader():
                    await sweep_once(grace_seconds, max_age_hours, max_knowledge_bases)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Belt and braces: sweep_once already swallows, so reaching here
                # means the lock itself misbehaved. Keep looping.
                logger.error(f"[cost-sweeper] pass failed: {type(e).__name__}: {e}")
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("[cost-sweeper] loop cancelled — releasing leader lock")
        await _release_leader()
        raise
