"""DB ops for the connector framework (connector_sync_config + connector_sync_run).

Control-plane state for self-serve external indexing: per-knowledge connection +
selection + schedule, and per-run status for UI progress. Mirrors the async session +
retry pattern used by knowledge_setting_ops.py.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from croniter import croniter
from sqlmodel import select, and_
from sqlalchemy import text

from utils.db.session import async_session
from utils.db.retry_utils import retry_db_connection
from utils.logger import logger
from utils.pydantic_models_knowledge import ConnectorSyncConfig, ConnectorSyncRun
from utils.tracing import track_time

# Distinguishes "caller omitted this field" (leave as-is) from an explicit None/""
# (clear the schedule -> manual sync). Plain None can't carry both meanings.
_UNSET: Any = object()


def _compute_next_sync(cfg: ConnectorSyncConfig, *, initial: bool) -> Optional[datetime]:
    """Compute the next due time for a config's schedule.

    - cron set  -> the next time matching the cron expression after now (UTC). Cron
      wins over interval if both are set.
    - interval  -> 'initial' means due immediately (now) so the first sync runs right
      away; otherwise now + interval_minutes.
    - neither   -> None (manual only).
    """
    now = datetime.now(timezone.utc)
    if cfg.schedule_cron:
        try:
            return croniter(cfg.schedule_cron, now).get_next(datetime)
        except (ValueError, KeyError):
            logger.error(f"Invalid cron '{cfg.schedule_cron}' for knowledge={cfg.knowledge_id} — no next_sync set")
            return None
    if cfg.interval_minutes:
        return now if initial else now + timedelta(minutes=cfg.interval_minutes)
    return None


def _config_to_dict(cfg: ConnectorSyncConfig) -> Dict[str, Any]:
    """Serialize a config row for API clients. The token is never returned — only a
    flag whether one is configured. The source-specific selection blob is returned as-is."""
    return {
        "id": cfg.id,
        "knowledge_id": cfg.knowledge_id,
        "source": cfg.source,
        "enabled": cfg.enabled,
        "token": "configured" if cfg.encrypted_token else None,
        "selection": cfg.selection or {},
        "interval_minutes": cfg.interval_minutes,
        "schedule_cron": cfg.schedule_cron,
        "last_sync_at": cfg.last_sync_at.isoformat() if cfg.last_sync_at else None,
        "next_sync_at": cfg.next_sync_at.isoformat() if cfg.next_sync_at else None,
        "created_at": cfg.created_at.isoformat() if cfg.created_at else None,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


@track_time("DB: Get Connector Config")
@retry_db_connection(max_retries=3)
async def get_connector_config(
    knowledge_id: str, source: str = "clickup", *, reveal_token: bool = False
) -> Optional[Dict[str, Any]]:
    """Return the config row for (knowledge_id, source), or None.

    reveal_token=True returns the stored ciphertext under 'encrypted_token' for the
    sync task to decrypt; the default path masks it for API responses.
    """
    async with async_session() as session:
        result = await session.execute(
            select(ConnectorSyncConfig).where(
                and_(
                    ConnectorSyncConfig.knowledge_id == knowledge_id,
                    ConnectorSyncConfig.source == source,
                )
            )
        )
        cfg = result.scalars().first()
        if not cfg:
            return None
        data = _config_to_dict(cfg)
        if reveal_token:
            # Internal callers (the runner) need the ciphertext to decrypt + the raw selection.
            data["encrypted_token"] = cfg.encrypted_token
        return data


@track_time("DB: Upsert Connector Config")
@retry_db_connection(max_retries=3)
async def upsert_connector_config(
    knowledge_id: str,
    source: str = "clickup",
    *,
    encrypted_token: Optional[str] = None,
    selection_patch: Optional[Dict[str, Any]] = None,
    interval_minutes: Any = _UNSET,
    schedule_cron: Any = _UNSET,
    enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    """Create or update the (knowledge_id, source) config. Only provided fields are
    written; selection_patch keys are merged into the existing selection blob (so
    Connect can set {workspace_id} and a later config save can add {space_ids, ...}
    without clobbering each other). When enabled, next_sync_at is (re)computed from the
    schedule — re-anchored if the schedule changed in this call."""
    async with async_session() as session:
        result = await session.execute(
            select(ConnectorSyncConfig).where(
                and_(
                    ConnectorSyncConfig.knowledge_id == knowledge_id,
                    ConnectorSyncConfig.source == source,
                )
            )
        )
        cfg = result.scalars().first()
        if cfg is None:
            cfg = ConnectorSyncConfig(knowledge_id=knowledge_id, source=source)
            session.add(cfg)

        if encrypted_token is not None:
            cfg.encrypted_token = encrypted_token
        if selection_patch:
            # Merge into a NEW dict so SQLAlchemy detects the JSONB change (in-place
            # mutation of the existing dict wouldn't be flagged dirty).
            merged = dict(cfg.selection or {})
            merged.update(selection_patch)
            cfg.selection = merged
        # interval and cron are mutually exclusive — setting one to a value clears the
        # other, so the caller can switch either direction. If both are sent in one call,
        # cron wins. An explicit None/"" clears that schedule (-> manual); _UNSET leaves
        # it untouched, which is how omitted fields keep their stored value.
        interval_provided = interval_minutes is not _UNSET
        cron_provided = schedule_cron is not _UNSET
        schedule_changed = interval_provided or cron_provided
        if interval_provided:
            cfg.interval_minutes = interval_minutes
            if interval_minutes is not None:
                cfg.schedule_cron = None
        if cron_provided:
            cfg.schedule_cron = schedule_cron or None  # "" clears the cron
            if cfg.schedule_cron:
                cfg.interval_minutes = None
        if enabled is not None:
            cfg.enabled = enabled

        # (Re)compute next_sync_at: set it on first enable, and re-anchor whenever the
        # schedule itself changed in this call. Cleared when disabled.
        if cfg.enabled:
            if cfg.next_sync_at is None or schedule_changed:
                cfg.next_sync_at = _compute_next_sync(cfg, initial=True)
        else:
            cfg.next_sync_at = None

        cfg.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(cfg)
        logger.info(f"Upserted connector config knowledge={knowledge_id} source={source} enabled={cfg.enabled}")
        return _config_to_dict(cfg)


@track_time("DB: Delete Connector Config")
@retry_db_connection(max_retries=3)
async def delete_connector_config(knowledge_id: str, source: str = "clickup") -> bool:
    """Delete the (knowledge_id, source) config row — the disconnect control-plane op.

    Drops the stored (encrypted) token, selection, and schedule. Returns False if there
    was nothing to delete. Does NOT touch already-indexed documents; the caller decides
    whether to purge those separately.
    """
    async with async_session() as session:
        result = await session.execute(
            select(ConnectorSyncConfig).where(
                and_(
                    ConnectorSyncConfig.knowledge_id == knowledge_id,
                    ConnectorSyncConfig.source == source,
                )
            )
        )
        cfg = result.scalars().first()
        if not cfg:
            return False
        await session.delete(cfg)
        await session.commit()
        logger.info(f"Deleted connector config knowledge={knowledge_id} source={source}")
        return True


@track_time("DB: List Due Connector Configs")
@retry_db_connection(max_retries=3)
async def list_due_configs(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Return enabled configs whose next_sync_at is due (<= now). Used by beat."""
    now = now or datetime.now(timezone.utc)
    async with async_session() as session:
        result = await session.execute(
            select(ConnectorSyncConfig).where(
                and_(
                    ConnectorSyncConfig.enabled == True,  # noqa: E712
                    ConnectorSyncConfig.next_sync_at != None,  # noqa: E711
                    ConnectorSyncConfig.next_sync_at <= now,
                )
            )
        )
        rows = result.scalars().all()
        return [{"knowledge_id": r.knowledge_id, "source": r.source, "id": r.id} for r in rows]


@track_time("DB: Advance Connector Next Sync")
@retry_db_connection(max_retries=3)
async def advance_next_sync(knowledge_id: str, source: str = "clickup") -> None:
    """Advance next_sync_at to the next scheduled time (called on enqueue, not
    completion, to avoid drift). interval -> now + interval; cron -> next cron match.
    No-op if not scheduled."""
    async with async_session() as session:
        result = await session.execute(
            select(ConnectorSyncConfig).where(
                and_(
                    ConnectorSyncConfig.knowledge_id == knowledge_id,
                    ConnectorSyncConfig.source == source,
                )
            )
        )
        cfg = result.scalars().first()
        if not cfg or not (cfg.interval_minutes or cfg.schedule_cron):
            return
        cfg.next_sync_at = _compute_next_sync(cfg, initial=False)
        cfg.updated_at = datetime.utcnow()
        await session.commit()


@track_time("DB: Touch Connector Last Sync")
@retry_db_connection(max_retries=3)
async def touch_last_sync(knowledge_id: str, source: str = "clickup") -> None:
    """Stamp last_sync_at = now after a run finishes."""
    async with async_session() as session:
        result = await session.execute(
            select(ConnectorSyncConfig).where(
                and_(
                    ConnectorSyncConfig.knowledge_id == knowledge_id,
                    ConnectorSyncConfig.source == source,
                )
            )
        )
        cfg = result.scalars().first()
        if not cfg:
            return
        now = datetime.now(timezone.utc)
        cfg.last_sync_at = now
        # Backstop: if a scheduled config is still "due" after a run finishes (e.g. a manual
        # run that bypassed the enqueue-time advance, or a crash between create_run and
        # advance_next_sync), push next_sync_at forward so the scheduler doesn't immediately
        # re-fire. Only fires when already overdue — normal cadence still advances on enqueue.
        if cfg.next_sync_at is not None and cfg.next_sync_at <= now:
            cfg.next_sync_at = _compute_next_sync(cfg, initial=False)
        cfg.updated_at = datetime.utcnow()
        await session.commit()


@track_time("DB: Create Connector Run")
@retry_db_connection(max_retries=3)
async def create_run(run_id: str, knowledge_id: str, source: str = "clickup", trigger: str = "manual") -> None:
    async with async_session() as session:
        session.add(ConnectorSyncRun(
            run_id=run_id, knowledge_id=knowledge_id, source=source,
            trigger=trigger, status="queued",
        ))
        await session.commit()
        logger.info(f"Created connector run {run_id} knowledge={knowledge_id} trigger={trigger}")


@track_time("DB: Requeue Stale Connector Runs")
@retry_db_connection(max_retries=3)
async def requeue_stale_runs(timeout_minutes: int = 90) -> int:
    """Reset runs stuck in 'running' past the timeout back to 'queued'.

    A redeploy/crash mid-crawl leaves a run 'running' forever (the runner that owned
    it is gone). This re-queues any such run so the current runner re-claims it. The
    timeout must comfortably exceed a real crawl's duration to avoid clobbering a
    genuinely-running sync. Returns how many were re-queued.
    """
    async with async_session() as session:
        result = await session.execute(text(
            """
            UPDATE connector_sync_run
            SET status = 'queued', started_at = NULL
            WHERE status = 'running'
              AND started_at < now() - make_interval(mins => :mins)
            RETURNING run_id
            """
        ), {"mins": timeout_minutes})
        rows = result.fetchall()
        await session.commit()
        if rows:
            logger.warning(f"Re-queued {len(rows)} stale 'running' connector run(s) (> {timeout_minutes}m)")
        return len(rows)


@track_time("DB: Claim Queued Connector Run")
@retry_db_connection(max_retries=3)
async def claim_queued_run() -> Optional[Dict[str, Any]]:
    """Atomically claim the oldest queued run: flip it to 'running' and return it.

    Uses FOR UPDATE SKIP LOCKED so multiple runner processes never grab the same row
    (the standard Postgres job-queue pattern). Returns None when nothing is queued.
    """
    async with async_session() as session:
        result = await session.execute(text(
            """
            UPDATE connector_sync_run
            SET status = 'running', started_at = now()
            WHERE id = (
                SELECT id FROM connector_sync_run
                WHERE status = 'queued'
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING run_id, knowledge_id, source
            """
        ))
        row = result.first()
        await session.commit()
        if not row:
            return None
        return {"run_id": row[0], "knowledge_id": row[1], "source": row[2]}


@track_time("DB: Has Active Connector Run")
@retry_db_connection(max_retries=3)
async def has_active_run(knowledge_id: str, source: str = "clickup") -> bool:
    """True if a run for this (knowledge_id, source) is queued or running — used to
    prevent overlapping syncs."""
    async with async_session() as session:
        result = await session.execute(
            select(ConnectorSyncRun).where(
                and_(
                    ConnectorSyncRun.knowledge_id == knowledge_id,
                    ConnectorSyncRun.source == source,
                    ConnectorSyncRun.status.in_(["queued", "running"]),
                )
            )
        )
        return result.scalars().first() is not None


@track_time("DB: Update Connector Run")
@retry_db_connection(max_retries=3)
async def update_run_status(
    run_id: str,
    status: Optional[str] = None,
    *,
    stats: Optional[Dict[str, int]] = None,
    error_detail: Optional[str] = None,
    mark_started: bool = False,
    mark_completed: bool = False,
) -> None:
    async with async_session() as session:
        result = await session.execute(
            select(ConnectorSyncRun).where(ConnectorSyncRun.run_id == run_id)
        )
        run = result.scalars().first()
        if not run:
            logger.warning(f"update_run_status: run {run_id} not found")
            return
        if status is not None:
            run.status = status
        if stats is not None:
            run.indexed = stats.get("indexed", run.indexed)
            run.skipped = stats.get("skipped", run.skipped)
            run.errors = stats.get("errors", run.errors)
            run.deleted = stats.get("deleted", run.deleted)
        if error_detail is not None:
            run.error_detail = error_detail[:2000]
        if mark_started:
            run.started_at = datetime.now(timezone.utc)
        if mark_completed:
            run.completed_at = datetime.now(timezone.utc)
        await session.commit()


@track_time("DB: Get Connector Runs")
@retry_db_connection(max_retries=3)
async def get_runs(
    knowledge_id: str, source: str = "clickup", run_id: Optional[str] = None, limit: int = 20
) -> List[Dict[str, Any]]:
    """Return recent runs (or a single run if run_id given), newest first."""
    async with async_session() as session:
        stmt = select(ConnectorSyncRun).where(
            and_(
                ConnectorSyncRun.knowledge_id == knowledge_id,
                ConnectorSyncRun.source == source,
            )
        )
        if run_id:
            stmt = stmt.where(ConnectorSyncRun.run_id == run_id)
        stmt = stmt.order_by(ConnectorSyncRun.id.desc()).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
        return [
            {
                "run_id": r.run_id,
                "status": r.status,
                "trigger": r.trigger,
                "indexed": r.indexed,
                "skipped": r.skipped,
                "errors": r.errors,
                "deleted": r.deleted,
                "error_detail": r.error_detail,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
