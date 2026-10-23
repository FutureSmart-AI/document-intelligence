"""In-service scheduler for Batch Query Reports.

Ported from fs-agent-dashboard's `batch_query_reports/scheduler.py` (see
docs/plan/batch-query-reports/schema.md). `run_due_reports()` is one
tick: it claims every report whose `next_run_at` has passed (atomically
advancing each so it can't double-fire), creates a `scheduled` run for each,
and enqueues it to the Worker's queue exactly like an on-demand run.
`run_scheduler_loop()` calls that tick on a fixed interval and is started by
this service's own `main.py` lifespan, gated by `REPORTS_SCHEDULER_ENABLED`
(default off) — no external trigger (Celery Beat/cron) needed.

Multi-instance safe: due reports are claimed with FOR UPDATE SKIP LOCKED in
`utils/reports/db_utils.py::claim_due_reports`, so when several replicas tick
at the same moment each report is claimed by exactly one of them.
"""
import asyncio
from datetime import datetime
from typing import Dict, Any

from utils.logger import logger
from utils.reports import db_utils
from utils.reports.queue_utils import enqueue_run


async def run_due_reports() -> Dict[str, Any]:
    """Fire every due report once. Returns a summary of what was triggered."""
    now = datetime.utcnow()
    due = await db_utils.claim_due_reports(now)
    triggered = []

    for report in due:
        report_id = report["report_id"]
        try:
            run = await db_utils.create_run(report_id, trigger="scheduled")
        except db_utils.QueryLimitExceeded as e:
            logger.warning(f"[reports.scheduler] skipping report {report_id}: {e}")
            continue
        except Exception as e:
            logger.error(f"[reports.scheduler] failed to create run for {report_id}: {e}")
            continue
        if not run:
            logger.warning(f"[reports.scheduler] report {report_id} vanished before run creation")
            continue

        await enqueue_run(run["run_id"])
        triggered.append({
            "report_id": report_id,
            "run_id": run["run_id"],
            "next_run_at": report["next_run_at"],
        })

    if triggered:
        logger.info(f"[reports.scheduler] triggered {len(triggered)} scheduled run(s)")
    return {"checked_at": now.isoformat(), "triggered_count": len(triggered), "triggered": triggered}


async def run_scheduler_loop(interval_seconds: int) -> None:
    """Run `run_due_reports()` every `interval_seconds` until cancelled.

    Started as a background task on app startup. A failing tick is logged and
    the loop continues — one bad tick must not kill the scheduler.
    """
    logger.info(f"[reports.scheduler] in-service scheduler started (interval={interval_seconds}s)")
    try:
        while True:
            try:
                await run_due_reports()
            except Exception:
                logger.exception("[reports.scheduler] tick failed")
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("[reports.scheduler] in-service scheduler stopped")
        raise
