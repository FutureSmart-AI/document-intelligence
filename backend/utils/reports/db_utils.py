"""Database access for Batch Query Reports.

Knowledge Service is the system of record (ported from fs-agent-dashboard's
`batch_query_reports/db_utils.py` — see
docs/plan/batch-query-reports/schema.md). These helpers own the
reports/runs tables and derive all progress counts from stored results (the
Worker, or this service's own in-process executor, never reports counts
directly).

Re-keyed: `agent_id` -> `knowledge_id`, `user_id` -> `owner_id` (always an
email here). `execution_details`/`relevance_score`/`retrieved_chunks` are not
carried over — see decisions.md.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from sqlmodel import select, and_
from sqlalchemy import func

from utils.db.retry_utils import retry_db_connection
from utils.db.session import async_session
from utils.logger import logger
from utils.tracing import track_time
from utils import setup_env
from utils.models.db_tables import (
    Report,
    ReportSettings,
    ReportRun,
    ReportRunGroup,
    ReportRunQuery,
    ReportRunResult,
)
from utils.reports.schedule_utils import compute_next_run_at


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Settings (per-knowledge_id, runtime-configurable)
# ---------------------------------------------------------------------------

async def _read_max_queries(session, knowledge_id: str) -> int:
    """Effective per-report query cap for a knowledge base: its DB value if set, else env default."""
    row = (await session.execute(
        select(ReportSettings).where(ReportSettings.knowledge_id == knowledge_id)
    )).scalar_one_or_none()
    if row and row.max_queries_per_report is not None:
        return row.max_queries_per_report
    return setup_env.REPORTS_MAX_QUERIES_PER_REPORT


@track_time("DB: Get Effective Report Query Limit")
@retry_db_connection(max_retries=3)
async def get_effective_query_limit(knowledge_id: str) -> int:
    """The limit enforcement should use for this knowledge base (its DB value or env default)."""
    async with async_session() as session:
        return await _read_max_queries(session, knowledge_id)


@track_time("DB: Get Report Settings")
@retry_db_connection(max_retries=3)
async def get_report_settings(knowledge_id: str) -> Dict[str, Any]:
    """Return a knowledge base's effective query limit plus the system default.

    The default is returned so the frontend never hardcodes it (it can change
    later server-side).
    """
    return {
        "max_queries_per_report": await get_effective_query_limit(knowledge_id),
        "default_max_queries_per_report": setup_env.REPORTS_MAX_QUERIES_PER_REPORT,
    }


@track_time("DB: Update Report Settings")
@retry_db_connection(max_retries=3)
async def update_max_queries_per_report(knowledge_id: str, value: int, updated_by: Optional[str]) -> Dict[str, Any]:
    """Set a knowledge base's per-report query cap (upserts its row). Returns the new settings."""
    async with async_session() as session:
        row = (await session.execute(
            select(ReportSettings).where(ReportSettings.knowledge_id == knowledge_id)
        )).scalar_one_or_none()
        if not row:
            row = ReportSettings(knowledge_id=knowledge_id)
            session.add(row)
        row.max_queries_per_report = value
        row.updated_at = datetime.now(timezone.utc)
        row.updated_by = updated_by
        await session.commit()
    logger.info(f"Report settings updated for knowledge_id={knowledge_id}: max_queries_per_report={value} by {updated_by}")
    return await get_report_settings(knowledge_id)


class QueryLimitExceeded(Exception):
    """A report has more queries than the effective limit allows.

    Raised at run creation so an over-limit report — including one that
    already existed before the limit was introduced or lowered — cannot be
    run.
    """

    def __init__(self, total_queries: int, limit: int):
        self.total_queries = total_queries
        self.limit = limit
        super().__init__(
            f"Report has {total_queries} queries, exceeding the limit of {limit}."
        )


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _duration_ms(started: Optional[datetime], ended: Optional[datetime]) -> Optional[int]:
    """Total wall-clock time of a run = ended_at - started_at, in ms. None until both set."""
    if started and ended:
        return int((ended - started).total_seconds() * 1000)
    return None


# ---------------------------------------------------------------------------
# Report definition (template)
# ---------------------------------------------------------------------------

@track_time("DB: Create Report")
@retry_db_connection(max_retries=3)
async def create_report(
    knowledge_id: str,
    owner_id: Optional[str],
    name: str,
    query_groups: List[Dict[str, Any]],
    report_instructions: Optional[str] = None,
    analysis_prompt: Optional[str] = None,
    schedule: Optional[Dict[str, Any]] = None,
) -> str:
    """Insert a report definition and return its report_id."""
    report_id = _new_id("rep")
    async with async_session() as session:
        report = Report(
            report_id=report_id,
            knowledge_id=knowledge_id,
            owner_id=owner_id or "",
            name=name,
            report_instructions=report_instructions,
            query_groups=query_groups,
            analysis_prompt=analysis_prompt,
            schedule=schedule,
            next_run_at=compute_next_run_at(schedule),
            deleted=False,
        )
        session.add(report)
        await session.commit()
    logger.info(f"Created report {report_id} for knowledge_id={knowledge_id}")
    return report_id


async def _get_report_row(session, report_id: str) -> Optional[Report]:
    statement = select(Report).where(
        and_(Report.report_id == report_id, Report.deleted == False)  # noqa: E712
    )
    result = await session.execute(statement)
    return result.scalar_one_or_none()


def _report_to_dict(r: Report) -> Dict[str, Any]:
    return {
        "report_id": r.report_id,
        "knowledge_id": r.knowledge_id,
        "owner_id": r.owner_id,
        "name": r.name,
        "report_instructions": r.report_instructions,
        "query_groups": r.query_groups,
        "analysis_prompt": r.analysis_prompt,
        "schedule": r.schedule,
        "next_run_at": _iso(r.next_run_at),
        "created_at": _iso(r.created_at),
        "updated_at": _iso(r.updated_at),
    }


@track_time("DB: Get Report")
@retry_db_connection(max_retries=3)
async def get_report(report_id: str) -> Optional[Dict[str, Any]]:
    async with async_session() as session:
        r = await _get_report_row(session, report_id)
        return _report_to_dict(r) if r else None


@track_time("DB: List Reports")
@retry_db_connection(max_retries=3)
async def list_reports(knowledge_id: str) -> List[Dict[str, Any]]:
    async with async_session() as session:
        statement = (
            select(Report)
            .where(and_(Report.knowledge_id == knowledge_id, Report.deleted == False))  # noqa: E712
            .order_by(Report.created_at.desc())
        )
        result = await session.execute(statement)
        return [_report_to_dict(r) for r in result.scalars().all()]


@track_time("DB: Update Report")
@retry_db_connection(max_retries=3)
async def update_report(report_id: str, fields: Dict[str, Any]) -> bool:
    """Update mutable report fields (only those present in `fields`).

    The caller (router) has already decided which fields to write, so a value
    of None here is an intentional clear (e.g. removing a schedule) — it must
    NOT be filtered out, or the field can never be unset.
    """
    async with async_session() as session:
        r = await _get_report_row(session, report_id)
        if not r:
            return False
        for key, value in fields.items():
            setattr(r, key, value)
        # If the schedule changed, recompute when the next run is due (from now).
        # A None schedule clears next_run_at, so the scheduler stops picking it up.
        if "schedule" in fields:
            r.next_run_at = compute_next_run_at(fields["schedule"])
        r.updated_at = datetime.now(timezone.utc)
        await session.commit()
    logger.info(f"Updated report {report_id}: fields={list(fields.keys())}")
    return True


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

@track_time("DB: Claim Due Reports")
@retry_db_connection(max_retries=3)
async def claim_due_reports(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Atomically claim every report whose scheduled run is due and advance its next_run_at.

    Due rows (next_run_at <= now, scheduled, not deleted) are selected FOR
    UPDATE SKIP LOCKED and their next_run_at is advanced to the following
    occurrence in the SAME transaction. This makes the scheduler safe to run
    on every replica concurrently: each due report is claimed by exactly one
    instance (the others skip the locked row), so it fires exactly once.

    Returns the claimed reports for the caller to create + enqueue runs for.
    """
    now = now or datetime.utcnow()
    claimed: List[Dict[str, Any]] = []
    async with async_session() as session:
        statement = (
            select(Report)
            .where(and_(
                Report.deleted == False,  # noqa: E712
                Report.schedule.isnot(None),
                Report.next_run_at.isnot(None),
                Report.next_run_at <= now,
            ))
            .with_for_update(skip_locked=True)
        )
        rows = (await session.execute(statement)).scalars().all()
        for r in rows:
            # Advance FIRST (within the lock) so the row is no longer "due" the moment we commit.
            r.next_run_at = compute_next_run_at(r.schedule, now)
            claimed.append({
                "report_id": r.report_id,
                "knowledge_id": r.knowledge_id,
                "schedule": r.schedule,
                "next_run_at": _iso(r.next_run_at),
            })
        await session.commit()
    return claimed


@track_time("DB: Delete Report")
@retry_db_connection(max_retries=3)
async def soft_delete_report(report_id: str, deleted_by: Optional[str] = None) -> bool:
    async with async_session() as session:
        r = await _get_report_row(session, report_id)
        if not r:
            return False
        r.deleted = True
        r.deleted_by = deleted_by
        r.deleted_at = datetime.now(timezone.utc)
        await session.commit()
    logger.info(f"Soft-deleted report {report_id} by {deleted_by}")
    return True


# ---------------------------------------------------------------------------
# Run creation + pre-materialization
# ---------------------------------------------------------------------------

@track_time("DB: Create Run")
@retry_db_connection(max_retries=3)
async def create_run(report_id: str, trigger: str = "manual", owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Create a run from a report definition and pre-materialize its groups/queries
    with stable IDs. Returns {run_id, total_queries} or None if the report is gone.
    """
    if not owner_id:
        owner_id = "system"

    async with async_session() as session:
        report = await _get_report_row(session, report_id)
        if not report:
            return None

        # Enforce the per-report query cap at the single choke point every run
        # (manual and scheduled) passes through, so it also blocks reports that
        # already exceed the limit. Uses the knowledge base's runtime-configurable limit.
        limit = await _read_max_queries(session, report.knowledge_id)
        query_count = sum(len(g.get("queries") or []) for g in (report.query_groups or []))
        if query_count > limit:
            raise QueryLimitExceeded(query_count, limit)

        run_id = _new_id("run")
        run = ReportRun(
            run_id=run_id,
            report_id=report.report_id,
            knowledge_id=report.knowledge_id,
            owner_id=owner_id,
            status="pending",
            trigger=trigger,
        )
        session.add(run)

        total_queries = 0
        for g_index, group in enumerate(report.query_groups or []):
            group_id = _new_id("g")
            session.add(ReportRunGroup(
                group_id=group_id,
                run_id=run_id,
                type=group.get("type", "independent"),
                position=g_index,
            ))
            for q_index, text in enumerate(group.get("queries", [])):
                session.add(ReportRunQuery(
                    query_id=_new_id("q"),
                    run_id=run_id,
                    group_id=group_id,
                    position=q_index,
                    text=text,
                ))
                total_queries += 1

        await session.commit()

    logger.info(f"Created run {run_id} for report {report_id} ({total_queries} queries, trigger={trigger})")
    return {"run_id": run_id, "total_queries": total_queries}


async def _get_run_row(session, run_id: str) -> Optional[ReportRun]:
    result = await session.execute(select(ReportRun).where(ReportRun.run_id == run_id))
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Progress derivation
# ---------------------------------------------------------------------------

async def _derive_progress(session, run_id: str) -> Dict[str, int]:
    total = (await session.execute(
        select(func.count()).select_from(ReportRunQuery).where(ReportRunQuery.run_id == run_id)
    )).scalar_one()

    completed = (await session.execute(
        select(func.count()).select_from(ReportRunResult).where(
            and_(ReportRunResult.run_id == run_id, ReportRunResult.error.is_(None))
        )
    )).scalar_one()

    failed = (await session.execute(
        select(func.count()).select_from(ReportRunResult).where(
            and_(ReportRunResult.run_id == run_id, ReportRunResult.error.is_not(None))
        )
    )).scalar_one()

    done = completed + failed
    percent = int(round(done / total * 100)) if total else 0
    return {
        "total_queries": total,
        "completed_queries": completed,
        "failed_queries": failed,
        "percent_complete": percent,
    }


@track_time("DB: Get Run Status")
@retry_db_connection(max_retries=3)
async def get_run_status(run_id: str) -> Optional[Dict[str, Any]]:
    async with async_session() as session:
        run = await _get_run_row(session, run_id)
        if not run:
            return None
        progress = await _derive_progress(session, run_id)
        return {
            "run_id": run.run_id,
            "report_id": run.report_id,
            "knowledge_id": run.knowledge_id,
            "status": run.status,
            "progress": progress,
            "started_at": _iso(run.started_at),
            "ended_at": _iso(run.ended_at),
        }


@track_time("DB: List Runs")
@retry_db_connection(max_retries=3)
async def list_runs(report_id: str) -> List[Dict[str, Any]]:
    """List runs for a report with progress, in a single aggregated read.

    Replaces an N+1 (3 counts per run) with three grouped queries over all
    runs of the report: total queries, completed (error IS NULL), failed
    (error IS NOT NULL). Per-run dicts assemble in Python from those maps.
    """
    async with async_session() as session:
        runs = (await session.execute(
            select(ReportRun)
            .where(ReportRun.report_id == report_id)
            .order_by(ReportRun.created_at.desc())
        )).scalars().all()
        if not runs:
            return []

        run_ids = [r.run_id for r in runs]

        totals = dict((await session.execute(
            select(ReportRunQuery.run_id, func.count())
            .where(ReportRunQuery.run_id.in_(run_ids))
            .group_by(ReportRunQuery.run_id)
        )).all())

        completed = dict((await session.execute(
            select(ReportRunResult.run_id, func.count())
            .where(and_(
                ReportRunResult.run_id.in_(run_ids),
                ReportRunResult.error.is_(None),
            ))
            .group_by(ReportRunResult.run_id)
        )).all())

        failed = dict((await session.execute(
            select(ReportRunResult.run_id, func.count())
            .where(and_(
                ReportRunResult.run_id.in_(run_ids),
                ReportRunResult.error.is_not(None),
            ))
            .group_by(ReportRunResult.run_id)
        )).all())

    out: List[Dict[str, Any]] = []
    for run in runs:
        total = totals.get(run.run_id, 0)
        done = completed.get(run.run_id, 0)
        bad = failed.get(run.run_id, 0)
        finished = done + bad
        percent = int(round(finished / total * 100)) if total else 0
        out.append({
            "run_id": run.run_id,
            "report_id": run.report_id,
            "status": run.status,
            "trigger": run.trigger,
            "progress": {
                "total_queries": total,
                "completed_queries": done,
                "failed_queries": bad,
                "percent_complete": percent,
            },
            "started_at": _iso(run.started_at),
            "ended_at": _iso(run.ended_at),
            "total_time_ms": _duration_ms(run.started_at, run.ended_at),
            "created_at": _iso(run.created_at),
        })
    return out


# ---------------------------------------------------------------------------
# Run definition (Worker/executor fetch) + full run (user retrieve)
# ---------------------------------------------------------------------------

@track_time("DB: Get Run Definition")
@retry_db_connection(max_retries=3)
async def get_run_definition(run_id: str) -> Optional[Dict[str, Any]]:
    """Worker/executor-facing: the pre-materialized definition with stable IDs + report context."""
    async with async_session() as session:
        run = await _get_run_row(session, run_id)
        if not run:
            return None

        # Report context (instructions/analysis_prompt) — read even if
        # the report was later soft-deleted, so an in-flight run can still complete.
        report = (await session.execute(
            select(Report).where(Report.report_id == run.report_id)
        )).scalar_one_or_none()

        groups = (await session.execute(
            select(ReportRunGroup).where(ReportRunGroup.run_id == run_id)
            .order_by(ReportRunGroup.position)
        )).scalars().all()

        queries = (await session.execute(
            select(ReportRunQuery).where(ReportRunQuery.run_id == run_id)
            .order_by(ReportRunQuery.position)
        )).scalars().all()

    queries_by_group: Dict[str, list] = {}
    for q in queries:
        queries_by_group.setdefault(q.group_id, []).append(
            {"query_id": q.query_id, "text": q.text}
        )

    return {
        "run_id": run.run_id,
        "report_id": run.report_id,
        "knowledge_id": run.knowledge_id,
        "owner_id": run.owner_id,
        "report_instructions": report.report_instructions if report else None,
        "analysis_prompt": report.analysis_prompt if report else None,
        "query_groups": [
            {
                "group_id": g.group_id,
                "type": g.type,
                "queries": queries_by_group.get(g.group_id, []),
            }
            for g in groups
        ],
    }


def _result_to_dict(r: ReportRunResult) -> Dict[str, Any]:
    return {
        "query_id": r.query_id,
        "group_id": r.group_id,
        "session_id": r.session_id,
        "answer": r.answer,
        "answer_ref": r.answer_ref,
        "sources_used": r.sources_used or [],
        "retrieved_documents": r.retrieved_documents or [],
        "response_time_ms": r.response_time_ms,
        "token_usage": r.token_usage,
        "error": r.error,
    }


@track_time("DB: Get Full Run")
@retry_db_connection(max_retries=3)
async def get_run_full(run_id: str) -> Optional[Dict[str, Any]]:
    """User-facing: the full assembled run — groups, per-query results, summary, analysis."""
    async with async_session() as session:
        run = await _get_run_row(session, run_id)
        if not run:
            return None

        report = (await session.execute(
            select(Report).where(Report.report_id == run.report_id)
        )).scalar_one_or_none()

        groups = (await session.execute(
            select(ReportRunGroup).where(ReportRunGroup.run_id == run_id)
            .order_by(ReportRunGroup.position)
        )).scalars().all()

        queries = (await session.execute(
            select(ReportRunQuery).where(ReportRunQuery.run_id == run_id)
            .order_by(ReportRunQuery.position)
        )).scalars().all()

        results = (await session.execute(
            select(ReportRunResult).where(ReportRunResult.run_id == run_id)
        )).scalars().all()

        progress = await _derive_progress(session, run_id)

    results_by_query = {r.query_id: r for r in results}
    queries_by_group: Dict[str, list] = {}
    for q in queries:
        queries_by_group.setdefault(q.group_id, []).append(q)

    # Run-level summary aggregates
    times = [r.response_time_ms for r in results if r.response_time_ms is not None]
    tokens = [r.token_usage for r in results if r.token_usage is not None]
    avg_time = int(round(sum(times) / len(times))) if times else 0
    total_tokens = sum(tokens) if tokens else 0

    group_blocks = []
    for g in groups:
        g_queries = queries_by_group.get(g.group_id, [])
        result_items = []
        conversation_flow = []
        for q in g_queries:
            r = results_by_query.get(q.query_id)
            item = {"question": q.text}
            if r:
                item.update(_result_to_dict(r))
            else:
                item.update({
                    "query_id": q.query_id,
                    "group_id": q.group_id,
                    "answer": None,
                    "sources_used": [],
                    "retrieved_documents": [],
                    "error": None,
                })
            result_items.append(item)
            if g.type == "sequential":
                conversation_flow.append({
                    "question": q.text,
                    "answer": (r.answer if r else None),
                })

        block = {"group_id": g.group_id, "type": g.type, "results": result_items}
        if g.type == "sequential":
            block["conversation_flow"] = conversation_flow
        group_blocks.append(block)

    return {
        "run_id": run.run_id,
        "report_id": run.report_id,
        "name": report.name if report else None,
        "knowledge_id": run.knowledge_id,
        "status": run.status,
        "trigger": run.trigger,
        "executed_at": _iso(run.started_at or run.created_at),
        "started_at": _iso(run.started_at),
        "ended_at": _iso(run.ended_at),
        "failure_reason": run.failure_reason,
        "summary": {
            "total_queries": progress["total_queries"],
            "completed_queries": progress["completed_queries"],
            "failed_queries": progress["failed_queries"],
            "total_time_ms": _duration_ms(run.started_at, run.ended_at),
            "avg_response_time_ms": avg_time,
            "total_tokens": total_tokens,
        },
        "overall_summary": run.overall_summary,
        "query_groups": group_blocks,
    }


# ---------------------------------------------------------------------------
# Writes: status, results, analysis (idempotent — shared by the internal
# Worker-facing API and this service's own in-process executor)
# ---------------------------------------------------------------------------

# Terminal states cannot be overwritten by a later transition (idempotency guard).
_TERMINAL = {"completed", "failed", "cancelled"}


@track_time("DB: Update Run Status")
@retry_db_connection(max_retries=3)
async def update_run_status(
    run_id: str,
    status: str,
    started_at: Optional[datetime] = None,
    ended_at: Optional[datetime] = None,
    failure_reason: Optional[str] = None,
    updated_by: Optional[str] = None,
) -> Optional[str]:
    """Idempotent lifecycle transition. Returns the resulting status, or None if run missing.

    Re-sending the same state, or any transition once terminal, is a no-op.

    `updated_by` records which user drove a dashboard-initiated transition
    (e.g. a manual cancel); executor-driven transitions leave it None.
    """
    async with async_session() as session:
        run = await _get_run_row(session, run_id)
        if not run:
            return None

        if run.status == status or run.status in _TERMINAL:
            logger.info(f"Run {run_id} status transition {run.status}->{status} is a no-op")
            return run.status

        run.status = status
        if status == "running" and run.started_at is None:
            run.started_at = started_at or datetime.now(timezone.utc)
        if status in _TERMINAL:
            run.ended_at = ended_at or datetime.now(timezone.utc)
        if status == "failed":
            run.failure_reason = failure_reason
        run.updated_at = datetime.now(timezone.utc)
        if updated_by is not None:
            run.updated_by = updated_by
        await session.commit()
    logger.info(f"Run {run_id} -> {status}")
    return status


@track_time("DB: Upsert Run Results")
@retry_db_connection(max_retries=3)
async def upsert_results(run_id: str, results: List[Dict[str, Any]]) -> int:
    """Upsert results by (run_id, query_id). Retries (Worker or executor) must not duplicate.

    Guards against orphan rows: any item whose `query_id` doesn't belong to
    this run is dropped with a warning rather than written.
    """
    if not results:
        return 0

    async with async_session() as session:
        valid_query_ids = {
            row[0]
            for row in (await session.execute(
                select(ReportRunQuery.query_id).where(ReportRunQuery.run_id == run_id)
            )).all()
        }
        if not valid_query_ids:
            logger.warning(
                f"Upsert called for run {run_id} which has no query rows; ignoring "
                f"{len(results)} submitted item(s)"
            )
            return 0

        count = 0
        skipped = 0
        for item in results:
            query_id = item.get("query_id")
            if not query_id:
                skipped += 1
                continue
            if query_id not in valid_query_ids:
                skipped += 1
                logger.warning(
                    f"Dropping result for run {run_id}: query_id {query_id} "
                    f"does not belong to this run"
                )
                continue

            existing = (await session.execute(
                select(ReportRunResult).where(and_(
                    ReportRunResult.run_id == run_id,
                    ReportRunResult.query_id == query_id,
                ))
            )).scalar_one_or_none()

            if existing:
                for key in (
                    "group_id", "session_id", "answer", "answer_ref",
                    "sources_used", "retrieved_documents",
                    "response_time_ms", "token_usage", "error",
                ):
                    if key in item:
                        setattr(existing, key, item[key])
                existing.updated_at = datetime.now(timezone.utc)
            else:
                session.add(ReportRunResult(
                    run_id=run_id,
                    group_id=item.get("group_id"),
                    query_id=query_id,
                    session_id=item.get("session_id"),
                    answer=item.get("answer"),
                    answer_ref=item.get("answer_ref"),
                    sources_used=item.get("sources_used"),
                    retrieved_documents=item.get("retrieved_documents"),
                    response_time_ms=item.get("response_time_ms"),
                    token_usage=item.get("token_usage"),
                    error=item.get("error"),
                ))
            count += 1
        await session.commit()
    if skipped:
        logger.info(f"Upserted {count} results for run {run_id} (skipped {skipped} invalid)")
    else:
        logger.info(f"Upserted {count} results for run {run_id}")
    return count


@track_time("DB: Set Run Analysis")
@retry_db_connection(max_retries=3)
async def set_run_analysis(run_id: str, overall_summary: str) -> bool:
    async with async_session() as session:
        run = await _get_run_row(session, run_id)
        if not run:
            return False
        run.overall_summary = overall_summary
        await session.commit()
    logger.info(f"Stored analysis summary for run {run_id}")
    return True


@track_time("DB: Cancel Run")
@retry_db_connection(max_retries=3)
async def cancel_run(run_id: str, cancelled_by: Optional[str] = None) -> Optional[str]:
    """Soft cancel: mark the run cancelled unless already terminal.

    `cancelled_by` is the user email who initiated the cancel from the UI.
    """
    return await update_run_status(run_id, "cancelled", updated_by=cancelled_by)
