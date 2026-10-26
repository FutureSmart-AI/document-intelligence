"""DB-driven connector runner — the knowledge-side background process for crawls.

No message broker. Triggers (manual "Sync now" or the schedule) simply insert a
'queued' row into connector_sync_run; this runner polls the DB, claims queued rows
atomically, and executes the crawl. Per-page indexing still flows to the external
worker via the existing /documents endpoint — exactly like the hand-run script did.

Run it as its own process (one or more; FOR UPDATE SKIP LOCKED keeps them from
double-claiming):

    MODE=PROD python -m utils.connectors.runner

Each tick:
  1. scheduler — enqueue due scheduled configs (skipping any with an in-flight run)
  2. executor  — drain queued runs, executing each crawl to completion
"""

import asyncio
import uuid

from sqlalchemy import text

from utils.logger import logger
from utils.setup_env import (
    CONNECTOR_MAX_CONCURRENT,
    CONNECTOR_POLL_INTERVAL_SECONDS,
    CONNECTOR_STALE_RUN_MINUTES,
    FASTAPI_SECRET_KEY,
    KNOWLEDGE_API_BASE_URL,
)
from utils.connectors.clickup import ClickUpConnector, ClickUpConfig
from utils.security.crypto import decrypt_secret
from utils.db import connector_ops
from utils.db.session import engine

# Stable bigint key for the runner's Postgres advisory lock. Only the process holding
# this lock runs the loop, so exactly one runner is active across uvicorn workers and
# replicas — whether the loop is launched in-process or as a standalone service.
_ADVISORY_LOCK_KEY = 778211
_leader_conn = None  # held AsyncConnection while we are leader (keeps the lock)


def _build_connector(source: str, cfg_row: dict, token: str):
    """Construct the right connector from a config row's selection blob."""
    sel = cfg_row.get("selection") or {}
    if source == "clickup":
        return ClickUpConnector(ClickUpConfig(
            knowledge_id=cfg_row["knowledge_id"],
            knowledge_api_base_url=KNOWLEDGE_API_BASE_URL,
            knowledge_api_key=FASTAPI_SECRET_KEY,
            api_token=token,
            workspace_id=sel.get("workspace_id") or "",
            space_ids=sel.get("space_ids") or [],
            folder_ids=sel.get("folder_ids") or [],
            folder_names=sel.get("folder_names") or [],
            doc_ids=sel.get("doc_ids") or [],
            only_doc_ids=bool(sel.get("only_doc_ids", False)),
            exclude_folder_name=sel.get("exclude_folder_name") or [],
            exclude_folder_id=sel.get("exclude_folder_id") or [],
            exclude_name_keywords=sel.get("exclude_name_keywords") or [],
            space_docs_enabled=sel.get("space_docs_enabled", True),
            space_doc_allowlist=sel.get("space_doc_allowlist") or [],
            deletion_enabled=sel.get("deletion_enabled", False),
            force_reindex=bool(sel.get("force_reindex", False)),
        ))
    raise RuntimeError(f"Unsupported connector source: {source}")


async def execute_run(run_id: str, knowledge_id: str, source: str) -> None:
    """Run one already-claimed sync (row is 'running'); fold stats / mark terminal state."""
    try:
        cfg_row = await connector_ops.get_connector_config(knowledge_id, source, reveal_token=True)
        if not cfg_row:
            raise RuntimeError(f"No connector config for knowledge={knowledge_id} source={source}")
        if not cfg_row.get("encrypted_token"):
            raise RuntimeError("Connector has no stored token — run Connect first")

        # One-shot force-reindex: a manual trigger may set selection.force_reindex to
        # reprocess every page regardless of timestamp (e.g. retry past failures or after
        # an embedding-model change). Consume it BEFORE building the connector so the flag
        # applies to THIS run only — cfg_row still carries the value, so _build_connector
        # sees it, while the next scheduled run reads a cleared config.
        if (cfg_row.get("selection") or {}).get("force_reindex"):
            logger.info(f"Connector run {run_id}: consuming one-shot force_reindex flag")
            await connector_ops.upsert_connector_config(
                knowledge_id, source, selection_patch={"force_reindex": False}
            )

        # One-shot per-doc resync: a /resync trigger stashes selection.resync_doc_ids.
        # Scope THIS run to just those docs (forced, no folder sweep) and clear the key so
        # the next scheduled run reverts to the normal folder selection. Kept distinct from
        # the persistent selection.doc_ids (additive docs) so the two never clobber.
        sel = cfg_row.get("selection") or {}
        resync_ids = sel.get("resync_doc_ids")
        if resync_ids:
            logger.info(f"Connector run {run_id}: one-shot resync of {len(resync_ids)} doc(s)")
            sel.update({"only_doc_ids": True, "doc_ids": resync_ids, "force_reindex": True})
            cfg_row["selection"] = sel
            await connector_ops.upsert_connector_config(
                knowledge_id, source, selection_patch={"resync_doc_ids": None}
            )

        connector = _build_connector(source, cfg_row, decrypt_secret(cfg_row["encrypted_token"]))

        async def _flush_progress(s):
            await connector_ops.update_run_status(run_id, stats=s.as_dict())

        stats = await connector.run_sync(on_progress=_flush_progress)
        # A crawl that couldn't list part of its scope (folder/space/doc enumeration
        # failed) did NOT fully run — reconcile deletes never happened for it. Report that
        # as 'failed' with the reason rather than a clean 'completed' that hides the no-op.
        if stats.crawl_errors:
            detail = (f"crawl incomplete: {len(stats.crawl_errors)} enumeration failure(s) — "
                      + "; ".join(stats.crawl_errors[:5]))
            await connector_ops.update_run_status(
                run_id, "failed", stats=stats.as_dict(), error_detail=detail, mark_completed=True
            )
            logger.error(f"Connector run {run_id} FAILED (crawl errors) — {stats.as_dict()} — {detail}")
        else:
            await connector_ops.update_run_status(
                run_id, "completed", stats=stats.as_dict(), mark_completed=True
            )
            await connector_ops.touch_last_sync(knowledge_id, source)
            logger.info(f"Connector run {run_id} completed — {stats.as_dict()}")
    except Exception as e:
        logger.error(f"Connector run {run_id} failed [{type(e).__name__}]: {e}", exc_info=True)
        await connector_ops.update_run_status(
            run_id, "failed", error_detail=str(e), mark_completed=True
        )


async def enqueue_due_schedules() -> int:
    """Insert a 'queued' run for each due scheduled config without an in-flight run."""
    due = await connector_ops.list_due_configs()
    enqueued = 0
    for row in due:
        kid, source = row["knowledge_id"], row["source"]
        if await connector_ops.has_active_run(kid, source):
            continue
        run_id = str(uuid.uuid4())
        await connector_ops.create_run(run_id, kid, source, trigger="scheduled")
        # Advance on enqueue (not completion) so cadence doesn't drift with run time.
        await connector_ops.advance_next_sync(kid, source)
        enqueued += 1
    if enqueued:
        logger.info(f"Connector scheduler: enqueued {enqueued} due sync(s)")
    return enqueued


async def drain_queued_runs() -> int:
    """Claim and execute queued runs until none remain, up to CONNECTOR_MAX_CONCURRENT
    at a time. Crawls are I/O-bound, so this one process runs several in parallel on the
    event loop. Returns how many ran.

    The semaphore caps in-flight crawls; the loop claims a new run only when a slot is
    free, keeping the pool full until the queue empties, then waits for stragglers.
    """
    sem = asyncio.Semaphore(CONNECTOR_MAX_CONCURRENT)
    active: set = set()
    ran = 0

    async def _run(claimed: dict) -> None:
        try:
            logger.info(f"Claimed connector run {claimed['run_id']} "
                        f"knowledge={claimed['knowledge_id']} source={claimed['source']}")
            await execute_run(claimed["run_id"], claimed["knowledge_id"], claimed["source"])
        finally:
            sem.release()

    while True:
        await sem.acquire()              # block until a concurrency slot frees up
        claimed = await connector_ops.claim_queued_run()
        if not claimed:
            sem.release()                # nothing left to claim
            break
        ran += 1
        task = asyncio.create_task(_run(claimed))
        active.add(task)
        task.add_done_callback(active.discard)

    if active:
        await asyncio.gather(*active, return_exceptions=True)
    return ran


async def tick() -> None:
    """One scheduler+executor cycle (also usable directly in tests)."""
    await enqueue_due_schedules()
    await drain_queued_runs()


async def _ensure_leader() -> bool:
    """Become (or stay) the single active runner via a Postgres session advisory lock.

    Holds a dedicated connection open for as long as we're leader — that's what keeps
    the lock. If another process already holds it, we return False and stay idle.
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
        logger.info("Connector runner acquired leader lock — this process owns the loop")
        return True
    await conn.close()
    return False


async def _release_leader() -> None:
    global _leader_conn
    if _leader_conn is not None:
        try:
            await _leader_conn.close()  # closing the connection releases the advisory lock
        except Exception:
            pass
        _leader_conn = None


async def run_loop() -> None:
    """Leader-elected poll loop. Safe to launch in every process (in-process via the
    FastAPI lifespan, and/or as a standalone service) — only the lock holder does work."""
    logger.info(f"Connector runner loop starting — poll {CONNECTOR_POLL_INTERVAL_SECONDS}s")
    try:
        while True:
            try:
                if await _ensure_leader():
                    await connector_ops.requeue_stale_runs(CONNECTOR_STALE_RUN_MINUTES)
                    await tick()
            except Exception as e:
                logger.error(f"Connector runner loop error [{type(e).__name__}]: {e}", exc_info=True)
                # Drop leadership so a healthy process can take over if our conn is broken.
                await _release_leader()
            await asyncio.sleep(CONNECTOR_POLL_INTERVAL_SECONDS)
    finally:
        await _release_leader()


if __name__ == "__main__":
    asyncio.run(run_loop())
