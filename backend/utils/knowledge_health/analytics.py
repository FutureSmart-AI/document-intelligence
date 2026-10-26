"""Aggregation queries over knowledge_health_events for the Agent Health /
Analytics read endpoints (routers/knowledge_health.py) — see
docs/plan/analytics-and-health/endpoints.md.

Query shapes follow fs-agent-chat's docs/12-agent-health-api.md contract,
re-keyed to `knowledge_id`. Uses SQLAlchemy Core (select/func/case) against
the KnowledgeHealthEvent ORM model, matching this repo's existing analytics
convention (utils/usage/analytics.py) rather than raw SQL strings.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Float, Integer, and_, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import literal_column


def _date_trunc(granularity: str, column):
    """date_trunc(<literal>, column), with the granularity inlined as a SQL
    literal rather than a bind parameter.

    Postgres's extended query protocol treats two occurrences of
    ``date_trunc($n, ...)`` with DIFFERENT placeholder numbers as
    syntactically distinct expressions when checking GROUP BY, even though
    both placeholders are bound to the identical string at execution time —
    reusing the same labeled Python expression object across select/
    group_by/order_by does not help, because SQLAlchemy still emits a fresh
    ``$n`` per clause. Inlining as a literal (safe here — ``granularity`` is
    always one of this module's own hardcoded "day"/"week"/"month" strings,
    never request input) makes the three occurrences textually identical,
    which is what Postgres's GROUP BY check actually requires.
    """
    assert granularity in ("day", "week", "month")
    return func.date_trunc(literal_column(f"'{granularity}'"), column)


_NODE_DATA_KEYS = (
    "node_name", "provider", "model", "calls", "errors",
    "latency_ms", "input_tokens", "output_tokens", "total_tokens", "cost_usd",
)


def _node_field(value_col, key: str, cast_to=None):
    """``value_col ->> '<key>'`` (optionally CAST), with the key inlined as a
    SQL literal rather than a bind parameter — same reasoning as
    :func:`_date_trunc` above: this expression is reused across SELECT/
    GROUP BY/ORDER BY in :func:`_by_node`, and a bind-parameter key would get
    a fresh ``$n`` per occurrence, which Postgres's GROUP BY check rejects
    even though every occurrence binds the identical string. ``key`` is
    always one of this module's own ``_NODE_DATA_KEYS``, never request
    input, so inlining it is safe."""
    assert key in _NODE_DATA_KEYS
    text_expr = value_col.op("->>")(literal_column(f"'{key}'"))
    return cast(text_expr, cast_to) if cast_to is not None else text_expr

from utils.models.db_tables import KnowledgeChatLogs, KnowledgeHealthEvent as E
from utils.knowledge_health.rag_eval import context_utilization
from utils.pydantic_models_knowledge_health import (
    ByModelCost,
    CostBlock,
    ExpensiveQuery,
    FailedStageCount,
    FailureCategoryCount,
    FailuresResponse,
    GridSummary,
    GroundingBlock,
    KnowledgeBaseGridRow,
    KnowledgeBasesGridResponse,
    NodeStat,
    PerfBlock,
    QualityBlock,
    QueriesResponse,
    QueryRow,
    ScorecardResponse,
    SlowQuery,
    TopFailedQuery,
    TrendBlock,
    TrendPoint,
    TrendsResponse,
    UsageBlock,
)

PERIOD_DAYS = {"week": 7, "month": 30}


def _window(period: str) -> tuple:
    days = PERIOD_DAYS.get(period, 7)
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=days)
    previous_since = since - timedelta(days=days)
    return since, until, previous_since


def _to_aware_utc(dt: datetime) -> datetime:
    """Custom-range bounds may arrive tz-naive (date-only ISO input, e.g.
    ``2026-06-08``) or tz-aware (``2026-06-08T00:00:00Z``) — normalize both
    to aware UTC so they compare against the tz-aware `created_at` column."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _resolve_window(period: str, start: Optional[datetime] = None, end: Optional[datetime] = None) -> tuple:
    """Resolve (since, until, previous_since). A custom `[start, end)` range
    (both given) overrides the rolling `period`; `previous_since` is always
    the equal-length window immediately preceding `since`, matching
    fs-agent-chat's `_resolve_window`/`_period_comparison` convention."""
    if start is not None and end is not None:
        since, until = _to_aware_utc(start), _to_aware_utc(end)
        previous_since = since - (until - since)
        return since, until, previous_since
    return _window(period)


def _cmp(cur: Any, prev: Any) -> Dict[str, Any]:
    """Current vs previous period: {current, previous, delta, pct_change}.
    `delta` is current-minus-previous (percentage POINTS for rate metrics,
    same unit as the value otherwise); `pct_change` is the % change relative
    to `previous` (None when previous is 0/falsy) — mirrors fs-agent-chat's
    `_cmp` convention so this repo's `/trends` comparison object matches the
    shape already documented for the UI."""
    c = round(cur, 6) if isinstance(cur, float) else cur
    p = round(prev, 6) if isinstance(prev, float) else prev
    delta = c - p
    if isinstance(delta, float):
        delta = round(delta, 6)
    pct = round((delta / p) * 100, 1) if p else None
    return {"current": c, "previous": p, "delta": delta, "pct_change": pct}


async def _feedback_counts(session: AsyncSession, knowledge_id: str, since: datetime, until: datetime) -> Dict[str, int]:
    """{"positive", "negative", "total"} feedback counts for the window, joined
    from `KnowledgeChatLogs.feedback` by `chat_id` — feedback lives on the
    chat-turn table, not on `knowledge_health_events`, same split as
    fs-agent-chat's `chat_logs.feedback` vs. `agent_health_events`."""
    rows = (await session.execute(
        select(KnowledgeChatLogs.feedback, func.count().label("c")).where(
            KnowledgeChatLogs.chat_id.in_(select(E.chat_id).where(
                E.knowledge_id == knowledge_id, E.created_at >= since, E.created_at < until)),
            KnowledgeChatLogs.feedback.isnot(None),
        ).group_by(KnowledgeChatLogs.feedback)
    )).all()
    fb = {"positive": 0, "negative": 0}
    for r in rows:
        if r.feedback in fb:
            fb[r.feedback] = int(r.c or 0)
    fb["total"] = fb["positive"] + fb["negative"]
    return fb


def _positive_rate(fb: Dict[str, int]) -> float:
    t = (fb.get("positive") or 0) + (fb.get("negative") or 0)
    return round((fb.get("positive") or 0) / t * 100, 1) if t else 0.0


def _direction(delta: Optional[float]) -> str:
    if delta is None:
        return "flat"
    if delta > 0:
        return "up"
    if delta < 0:
        return "down"
    return "flat"


async def _success_rate_and_counts(session: AsyncSession, knowledge_id: str, since: datetime, until: datetime) -> dict:
    row = (await session.execute(
        select(
            func.count().label("total"),
            func.sum(case((E.status == "success", 1), else_=0)).label("answered"),
            func.sum(case((E.status == "no_answer", 1), else_=0)).label("no_answer"),
            func.sum(case((E.status == "error", 1), else_=0)).label("error"),
            func.avg(E.latency_ms).label("avg_ms"),
            func.percentile_cont(0.5).within_group(E.latency_ms).label("p50_ms"),
            func.percentile_cont(0.95).within_group(E.latency_ms).label("p95_ms"),
            func.count(func.distinct(E.user_id)).label("unique_users"),
            func.sum(case((E.from_widget.is_(True), 1), else_=0)).label("widget_queries"),
        ).where(and_(E.knowledge_id == knowledge_id, E.created_at >= since, E.created_at < until))
    )).one()
    total = row.total or 0
    answered = row.answered or 0
    return {
        "total": total,
        "answered": answered,
        "no_answer": row.no_answer or 0,
        "error": row.error or 0,
        "success_rate": round(100.0 * answered / total, 1) if total else 0.0,
        "avg_ms": float(row.avg_ms or 0),
        "p50_ms": float(row.p50_ms or 0),
        "p95_ms": float(row.p95_ms or 0),
        "unique_users": row.unique_users or 0,
        "widget_queries": row.widget_queries or 0,
    }


async def _cost_block(session: AsyncSession, knowledge_id: str, since: datetime, until: datetime, total_queries: int) -> CostBlock:
    row = (await session.execute(
        select(
            func.coalesce(func.sum(E.cost_usd), 0).label("total_cost"),
            func.coalesce(func.sum(E.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(E.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(E.output_tokens), 0).label("output_tokens"),
            func.bool_and(E.cost_complete).label("cost_complete"),
        ).where(and_(E.knowledge_id == knowledge_id, E.created_at >= since, E.created_at < until))
    )).one()
    total_cost = float(row.total_cost or 0)
    by_model_rows = (await session.execute(
        select(E.provider, E.model, func.count().label("queries"), func.coalesce(func.sum(E.cost_usd), 0).label("cost"))
        .where(and_(E.knowledge_id == knowledge_id, E.created_at >= since, E.created_at < until))
        .group_by(E.provider, E.model)
        .order_by(func.sum(E.cost_usd).desc().nullslast())
    )).all()
    expensive_rows = (await session.execute(
        select(E.chat_id, E.query, E.total_tokens, E.cost_usd, E.trace_id)
        .where(and_(E.knowledge_id == knowledge_id, E.created_at >= since, E.created_at < until))
        .order_by(E.cost_usd.desc().nullslast())
        .limit(10)
    )).all()
    return CostBlock(
        total_cost=round(total_cost, 6),
        avg_cost_per_query=round(total_cost / total_queries, 6) if total_queries else 0.0,
        total_tokens=int(row.total_tokens or 0),
        input_tokens=int(row.input_tokens or 0),
        output_tokens=int(row.output_tokens or 0),
        cost_complete=bool(row.cost_complete) if row.cost_complete is not None else True,
        by_model=[ByModelCost(provider=r.provider, model=r.model, queries=r.queries, cost=round(float(r.cost or 0), 6)) for r in by_model_rows],
        most_expensive=[ExpensiveQuery(chat_id=str(r.chat_id), query=r.query, total_tokens=r.total_tokens, cost_usd=float(r.cost_usd) if r.cost_usd is not None else None, trace_id=r.trace_id) for r in expensive_rows],
    )


async def _by_node(session: AsyncSession, knowledge_id: str, since: datetime, until: datetime) -> List[NodeStat]:
    """Per-(node_name, model) aggregate over the window — the same list
    powers both perf.by_node and cost.by_node (one query, shared object
    shape, matching the NL2SQL analytics contract's "by_node — same objects
    as perf.by_node" convention).

    Each row's node_data JSONB array (chat/db/health_events.py::
    _build_node_data — one entry per node that ran that turn) is expanded via
    a LATERAL jsonb_array_elements: a turn touching 3 nodes contributes 3
    expanded rows, one per node, before GROUP BY collapses them across turns.
    """
    node_elems = func.jsonb_array_elements(E.node_data).table_valued("value", joins_implicitly=True)
    v = node_elems.c.value
    node_name = _node_field(v, "node_name").label("node_name")
    model = _node_field(v, "model").label("model")
    provider = _node_field(v, "provider").label("provider")
    calls = _node_field(v, "calls", Integer)
    errors = _node_field(v, "errors", Integer)
    latency_ms = _node_field(v, "latency_ms", Integer)
    input_tokens = _node_field(v, "input_tokens", Integer)
    output_tokens = _node_field(v, "output_tokens", Integer)
    total_tokens = _node_field(v, "total_tokens", Integer)
    cost_usd = _node_field(v, "cost_usd", Float)

    rows = (await session.execute(
        select(
            node_name, model, provider,
            func.count().label("queries"),
            func.coalesce(func.sum(calls), 0).label("total_calls"),
            func.coalesce(func.sum(errors), 0).label("errors"),
            func.avg(latency_ms).label("avg_latency_ms"),
            func.percentile_cont(0.5).within_group(latency_ms).label("p50_latency_ms"),
            func.percentile_cont(0.95).within_group(latency_ms).label("p95_latency_ms"),
            func.coalesce(func.sum(total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens), 0).label("output_tokens"),
            func.sum(cost_usd).label("total_cost"),
        )
        # Explicit select_from(E, node_elems): Postgres's implicit LATERAL for
        # a set-returning function in FROM only sees columns from FROM items
        # listed BEFORE it. Without this, SQLAlchemy orders the FROM list by
        # first-referenced-in-SELECT, which put the function before E and
        # would fail at the database with "column node_data does not exist"
        # (caught by compiling this statement and inspecting the FROM clause —
        # no live DB available to execute it in this environment).
        .select_from(E, node_elems)
        .where(and_(E.knowledge_id == knowledge_id, E.created_at >= since, E.created_at < until))
        .group_by(node_name, model, provider)
        .order_by(func.sum(total_tokens).desc().nullslast())
    )).all()

    return [
        NodeStat(
            node_name=r.node_name, provider=r.provider, model=r.model,
            queries=r.queries, total_calls=int(r.total_calls or 0), errors=int(r.errors or 0),
            avg_latency_ms=round(float(r.avg_latency_ms or 0), 1),
            p50_latency_ms=round(float(r.p50_latency_ms or 0), 1),
            p95_latency_ms=round(float(r.p95_latency_ms or 0), 1),
            avg_latency_s=round(float(r.avg_latency_ms or 0) / 1000, 2),
            p50_latency_s=round(float(r.p50_latency_ms or 0) / 1000, 2),
            p95_latency_s=round(float(r.p95_latency_ms or 0) / 1000, 2),
            total_tokens=int(r.total_tokens or 0), input_tokens=int(r.input_tokens or 0), output_tokens=int(r.output_tokens or 0),
            total_cost=round(float(r.total_cost), 6) if r.total_cost is not None else None,
        )
        for r in rows
    ]


async def _grounding_block(session: AsyncSession, knowledge_id: str, since: datetime,
                           until: datetime) -> GroundingBlock:
    """RAG eval Tier 0, aggregated over the window (migration 048).

    THE ONLY READER OF THOSE NINE COLUMNS. They were written on every gated
    chat turn from the day migration 048 deployed and, until this function, no
    query anywhere in the service selected them — the deterministic tier was
    collecting into a store nothing could see, which is a strictly worse
    position than not collecting at all: it looks measured.

    Every aggregate here EXCLUDES the NULL rows rather than treating them as
    zero. A turn outside `RAG_EVAL_OWNER_IDS` has NULL in all nine columns, and
    averaging those in as zeros would drag a real measurement toward zero in
    proportion to how few owners are gated on. `measured_turns` is reported
    beside every number so the denominator is never a guess.
    """
    window = and_(E.knowledge_id == knowledge_id, E.created_at >= since, E.created_at < until)

    row = (await session.execute(
        select(
            func.count().label("total"),
            func.count(E.retrieved_chunk_count).label("measured"),
            func.sum(E.retrieved_chunk_count).label("retrieved_chunks"),
            func.sum(E.cited_chunk_count).label("cited_chunks"),
            func.sum(E.retrieved_doc_count).label("retrieved_docs"),
            func.sum(E.cited_doc_count).label("cited_docs"),
            # cited/retrieved per turn, averaged over the turns that retrieved
            # something. NULLIF keeps a zero-retrieval turn out of the average
            # instead of dividing by zero — no retrieval is not zero
            # utilization, the same rule rag_eval.context_utilization applies.
            # Both sides cast to float8 explicitly: a bare 0.0 binds as
            # NUMERIC, and float8/numeric only works because Postgres happens
            # to resolve it through an implicit cast — not something to leave
            # a per-turn quality metric depending on.
            func.avg(
                cast(E.cited_chunk_count, Float)
                / func.nullif(cast(E.retrieved_chunk_count, Float), cast(0.0, Float)).cast(Float)
            ).label("avg_util"),
            func.sum(case((E.retrieval_attempts > 1, 1), else_=0)).label("multi"),
            func.max(E.retrieval_attempts).label("max_attempts"),
            func.sum(E.evidence_groups_loaded).label("groups_loaded"),
            func.sum(E.evidence_groups_kept).label("groups_kept"),
        ).where(window)
    )).one()

    verdicts = {v: n for v, n in (await session.execute(
        select(E.verifier_verdict, func.count())
        .where(and_(window, E.verifier_verdict.isnot(None)))
        .group_by(E.verifier_verdict)
    )).all()}

    total = int(row.total or 0)
    measured = int(row.measured or 0)
    loaded = int(row.groups_loaded or 0)
    kept = int(row.groups_kept or 0)

    return GroundingBlock(
        total_turns=total,
        measured_turns=measured,
        coverage_pct=round(measured / total * 100, 1) if total else 0.0,
        avg_context_utilization=round(float(row.avg_util), 4) if row.avg_util is not None else None,
        retrieved_chunks=int(row.retrieved_chunks or 0),
        cited_chunks=int(row.cited_chunks or 0),
        retrieved_docs=int(row.retrieved_docs or 0),
        cited_docs=int(row.cited_docs or 0),
        multi_attempt_turns=int(row.multi or 0),
        multi_attempt_pct=round(int(row.multi or 0) / measured * 100, 1) if measured else None,
        max_attempts=int(row.max_attempts) if row.max_attempts is not None else None,
        verifier_verdicts=verdicts,
        evidence_groups_loaded=loaded,
        evidence_groups_kept=kept,
        evidence_kept_pct=round(kept / loaded * 100, 1) if loaded else None,
    )


async def get_scorecard(
    session: AsyncSession, knowledge_id: str, period: str = "week", top_n: int = 10,
    start: Optional[datetime] = None, end: Optional[datetime] = None,
) -> ScorecardResponse:
    since, until, previous_since = _resolve_window(period, start, end)
    stats = await _success_rate_and_counts(session, knowledge_id, since, until)
    prev_stats = await _success_rate_and_counts(session, knowledge_id, previous_since, since)
    cost = await _cost_block(session, knowledge_id, since, until, stats["total"])
    feedback = await _feedback_counts(session, knowledge_id, since, until)

    day_col = _date_trunc("day", E.created_at).label("day")
    per_day_rows = (await session.execute(
        select(day_col, func.count().label("queries"))
        .where(and_(E.knowledge_id == knowledge_id, E.created_at >= since, E.created_at < until))
        .group_by(day_col)
        .order_by(day_col)
    )).all()
    slowest_rows = (await session.execute(
        select(E.chat_id, E.query, E.latency_ms, E.total_tokens, E.trace_id)
        .where(and_(E.knowledge_id == knowledge_id, E.created_at >= since, E.created_at < until))
        .order_by(E.latency_ms.desc())
        .limit(top_n)
    )).all()

    success_rate_delta = round(stats["success_rate"] - prev_stats["success_rate"], 1) if prev_stats["total"] else None
    avg_ms_delta = round(stats["avg_ms"] - prev_stats["avg_ms"], 1) if prev_stats["total"] else None
    prev_cost = await _cost_block(session, knowledge_id, previous_since, since, prev_stats["total"])
    cost_delta = round(cost.total_cost - prev_cost.total_cost, 6) if prev_stats["total"] else None

    # One query, shared by perf.by_node and cost.by_node (same objects — the
    # NL2SQL analytics contract this scorecard tracks parity against renders
    # both from one node list rather than computing it twice).
    by_node = await _by_node(session, knowledge_id, since, until)
    cost.by_node = by_node
    grounding = await _grounding_block(session, knowledge_id, since, until)

    return ScorecardResponse(
        knowledge_id=knowledge_id, period=period,
        since=since.isoformat(), until=until.isoformat(), previous_since=previous_since.isoformat(),
        usage=UsageBlock(
            total_queries=stats["total"], unique_users=stats["unique_users"],
            widget_queries=stats["widget_queries"], bucket="day",
            per_day=[{"bucket": r.day.isoformat(), "day": r.day.date().isoformat(), "queries": r.queries} for r in per_day_rows],
        ),
        quality=QualityBlock(answered=stats["answered"], no_answer=stats["no_answer"], error=stats["error"], success_rate=stats["success_rate"], feedback=feedback),
        perf=PerfBlock(
            avg_ms=stats["avg_ms"], p50_ms=stats["p50_ms"], p95_ms=stats["p95_ms"],
            by_node=by_node,
            slowest=[SlowQuery(chat_id=str(r.chat_id), query=r.query, latency_ms=r.latency_ms, total_tokens=r.total_tokens, trace_id=r.trace_id) for r in slowest_rows],
        ),
        cost=cost,
        trend=TrendBlock(
            prev_success_rate=prev_stats["success_rate"] if prev_stats["total"] else None,
            success_rate_delta=success_rate_delta,
            prev_avg_ms=prev_stats["avg_ms"] if prev_stats["total"] else None,
            avg_ms_delta=avg_ms_delta,
            prev_total_cost=prev_cost.total_cost if prev_stats["total"] else None,
            cost_delta=cost_delta,
            direction=_direction(success_rate_delta),
        ),
        grounding=grounding,
    )


async def get_knowledge_bases_grid(
    session: AsyncSession, owner_id: Optional[str] = None, period: str = "week", limit: int = 50, offset: int = 0,
) -> KnowledgeBasesGridResponse:
    since, until, _ = _window(period)
    filters = [E.created_at >= since, E.created_at < until]
    if owner_id:
        filters.append(E.owner_id == owner_id)

    top_failure_subq = (
        select(E.knowledge_id, E.failure_category, func.count().label("cnt"))
        .where(and_(*filters, E.failure_category.isnot(None)))
        .group_by(E.knowledge_id, E.failure_category)
        .subquery()
    )

    rows = (await session.execute(
        select(
            E.knowledge_id, E.owner_id,
            func.count().label("total_queries"),
            func.count(func.distinct(E.user_id)).label("unique_users"),
            func.sum(case((E.status == "success", 1), else_=0)).label("answered"),
            func.coalesce(func.sum(E.cost_usd), 0).label("total_cost"),
            func.coalesce(func.sum(E.total_tokens), 0).label("total_tokens"),
            func.bool_and(E.cost_complete).label("cost_complete"),
            func.avg(E.latency_ms).label("avg_ms"),
            func.percentile_cont(0.95).within_group(E.latency_ms).label("p95_ms"),
        ).where(and_(*filters))
        .group_by(E.knowledge_id, E.owner_id)
        .order_by(func.count().desc())
        .limit(limit).offset(offset)
    )).all()

    total_kb_row = (await session.execute(
        select(func.count(func.distinct(E.knowledge_id))).where(and_(*filters))
    )).scalar() or 0

    # Summary describes the WHOLE filtered window, before limit/offset — deliberately
    # not equal to the sum of the returned page (a total that shrank as you paged
    # would be useless), matching NL2SQL's /analytics/overview convention.
    summary_row = (await session.execute(
        select(
            func.count().label("total_queries"),
            func.coalesce(func.sum(E.cost_usd), 0).label("total_cost"),
            func.bool_and(E.cost_complete).label("cost_complete"),
        ).where(and_(*filters))
    )).one()

    kb_rows = []
    for r in rows:
        total = r.total_queries or 0
        answered = r.answered or 0
        top_fail = (await session.execute(
            select(top_failure_subq.c.failure_category)
            .where(top_failure_subq.c.knowledge_id == r.knowledge_id)
            .order_by(top_failure_subq.c.cnt.desc())
            .limit(1)
        )).scalar()
        kb_rows.append(KnowledgeBaseGridRow(
            knowledge_id=r.knowledge_id, owner_id=r.owner_id, total_queries=total,
            unique_users=r.unique_users or 0, answered=answered, unanswered=total - answered,
            success_rate=round(100.0 * answered / total, 1) if total else 0.0,
            total_cost=round(float(r.total_cost or 0), 6),
            avg_cost_per_query=round(float(r.total_cost or 0) / total, 6) if total else 0.0,
            total_tokens=int(r.total_tokens or 0),
            avg_ms=round(float(r.avg_ms or 0), 1),
            p95_ms=round(float(r.p95_ms or 0), 1),
            cost_complete=bool(r.cost_complete) if r.cost_complete is not None else True,
            top_failure_category=top_fail,
        ))

    return KnowledgeBasesGridResponse(
        period=period, since=since.isoformat(), until=until.isoformat(),
        total_knowledge_bases=total_kb_row, limit=limit, offset=offset,
        summary=GridSummary(
            total_queries=int(summary_row.total_queries or 0),
            total_cost=round(float(summary_row.total_cost or 0), 6),
            cost_complete=bool(summary_row.cost_complete) if summary_row.cost_complete is not None else True,
        ),
        knowledge_bases=kb_rows,
    )


FAILURE_STATUSES = ("no_answer", "error")


async def get_failures(
    session: AsyncSession, knowledge_id: str, period: str = "week",
    category: Optional[str] = None, failed_stage: Optional[str] = None,
    limit: int = 50, offset: int = 0,
    start: Optional[datetime] = None, end: Optional[datetime] = None,
) -> FailuresResponse:
    since, until, _ = _resolve_window(period, start, end)
    filters = [E.knowledge_id == knowledge_id, E.created_at >= since, E.created_at < until, E.status.in_(FAILURE_STATUSES)]
    if category:
        filters.append(E.failure_category == category)
    if failed_stage:
        filters.append(E.failed_stage == failed_stage)

    total_failures = (await session.execute(select(func.count()).where(and_(*filters)))).scalar() or 0

    by_category_rows = (await session.execute(
        select(E.failure_category, func.count().label("count"))
        .where(and_(*filters)).group_by(E.failure_category).order_by(func.count().desc())
    )).all()
    by_stage_rows = (await session.execute(
        select(E.failed_stage, func.count().label("count"))
        .where(and_(*filters)).group_by(E.failed_stage).order_by(func.count().desc())
    )).all()

    top_rows = (await session.execute(
        select(
            func.lower(func.btrim(E.query)).label("query_norm"),
            E.failure_category, E.failed_stage,
            func.count().label("occurrences"),
            func.max(E.created_at).label("last_seen"),
            func.min(E.created_at).label("first_seen"),
        )
        .where(and_(*filters))
        .group_by(func.lower(func.btrim(E.query)), E.failure_category, E.failed_stage)
        .order_by(func.count().desc())
        .limit(limit).offset(offset)
    )).all()

    top_failed_queries: List[TopFailedQuery] = []
    for r in top_rows:
        latest = (await session.execute(
            select(E.chat_id, E.trace_id, E.failure_detail)
            .where(and_(*filters), func.lower(func.btrim(E.query)) == r.query_norm)
            .order_by(E.created_at.desc()).limit(1)
        )).one()
        top_failed_queries.append(TopFailedQuery(
            query_norm=r.query_norm, failure_category=r.failure_category, failed_stage=r.failed_stage,
            occurrences=r.occurrences, last_seen=r.last_seen.isoformat(), first_seen=r.first_seen.isoformat(),
            latest_chat_id=str(latest.chat_id), latest_trace_id=latest.trace_id, sample_failure_detail=latest.failure_detail,
        ))

    return FailuresResponse(
        knowledge_id=knowledge_id, period=period, since=since.isoformat(), until=until.isoformat(),
        total_failures=total_failures,
        filters={"category": category, "failed_stage": failed_stage},
        by_category=[FailureCategoryCount(failure_category=r.failure_category, count=r.count, pct=round(100.0 * r.count / total_failures, 1) if total_failures else 0.0) for r in by_category_rows],
        by_stage=[FailedStageCount(failed_stage=r.failed_stage, count=r.count) for r in by_stage_rows],
        limit=limit, offset=offset, returned=len(top_failed_queries), top_failed_queries=top_failed_queries,
    )


BUCKET_TRUNC = {"day": "day", "week": "week", "month": "month"}


async def _period_comparison(session: AsyncSession, knowledge_id: str, since: datetime, until: datetime) -> Dict[str, Any]:
    """Current-vs-previous-period comparison for every headline metric, each
    as {current, previous, delta, pct_change} (delta in percentage points for
    rate metrics). The previous window is the equal-length span immediately
    before `since`. This is the home for ALL "vs prev period" data — the
    scorecard's own `trend` block stays a smaller, separate summary."""
    prev_since = since - (until - since)
    cur = await _success_rate_and_counts(session, knowledge_id, since, until)
    prev = await _success_rate_and_counts(session, knowledge_id, prev_since, since)
    cur_cost = await _cost_block(session, knowledge_id, since, until, cur["total"])
    prev_cost = await _cost_block(session, knowledge_id, prev_since, since, prev["total"])
    fb_cur = await _feedback_counts(session, knowledge_id, since, until)
    fb_prev = await _feedback_counts(session, knowledge_id, prev_since, since)
    return {
        "previous_since": prev_since.isoformat(),
        "total_queries": _cmp(cur["total"], prev["total"]),
        "answered": _cmp(cur["answered"], prev["answered"]),
        "unanswered": _cmp(cur["total"] - cur["answered"], prev["total"] - prev["answered"]),
        "success_rate": _cmp(cur["success_rate"], prev["success_rate"]),
        "avg_ms": _cmp(cur["avg_ms"], prev["avg_ms"]),
        "avg_s": _cmp(round(cur["avg_ms"] / 1000, 2), round(prev["avg_ms"] / 1000, 2)),
        "p50_ms": _cmp(cur["p50_ms"], prev["p50_ms"]),
        "p50_s": _cmp(round(cur["p50_ms"] / 1000, 2), round(prev["p50_ms"] / 1000, 2)),
        "p95_ms": _cmp(cur["p95_ms"], prev["p95_ms"]),
        "p95_s": _cmp(round(cur["p95_ms"] / 1000, 2), round(prev["p95_ms"] / 1000, 2)),
        "total_cost": _cmp(cur_cost.total_cost, prev_cost.total_cost),
        "avg_cost_per_query": _cmp(cur_cost.avg_cost_per_query, prev_cost.avg_cost_per_query),
        "total_tokens": _cmp(cur_cost.total_tokens, prev_cost.total_tokens),
        "input_tokens": _cmp(cur_cost.input_tokens, prev_cost.input_tokens),
        "output_tokens": _cmp(cur_cost.output_tokens, prev_cost.output_tokens),
        "unique_users": _cmp(cur["unique_users"], prev["unique_users"]),
        "feedback_positive": _cmp(fb_cur["positive"], fb_prev["positive"]),
        "feedback_negative": _cmp(fb_cur["negative"], fb_prev["negative"]),
        "feedback_positive_rate": _cmp(_positive_rate(fb_cur), _positive_rate(fb_prev)),
    }


async def get_trends(
    session: AsyncSession, knowledge_id: str, bucket: str = "week", buckets: int = 8,
    metrics: Optional[List[str]] = None,
    start: Optional[datetime] = None, end: Optional[datetime] = None,
) -> TrendsResponse:
    trunc = BUCKET_TRUNC.get(bucket, "week")
    if start is not None and end is not None:
        # Custom range overrides the trailing `buckets` window; `bucket` still
        # controls the date_trunc granularity within the range.
        since, until = _to_aware_utc(start), _to_aware_utc(end)
    else:
        days_per_bucket = {"day": 1, "week": 7, "month": 30}[trunc]
        until = datetime.now(timezone.utc)
        since = until - timedelta(days=days_per_bucket * buckets)
    metrics = metrics or ["success_rate", "avg_ms", "cost", "failures"]

    bucket_col = _date_trunc(trunc, E.created_at).label("bucket")
    rows = (await session.execute(
        select(
            bucket_col,
            func.count().label("total_queries"),
            func.sum(case((E.status == "success", 1), else_=0)).label("answered"),
            func.avg(E.latency_ms).label("avg_ms"),
            func.percentile_cont(0.95).within_group(E.latency_ms).label("p95_ms"),
            func.coalesce(func.sum(E.cost_usd), 0).label("cost"),
            func.sum(case((E.status.in_(FAILURE_STATUSES), 1), else_=0)).label("failures"),
        )
        .where(and_(E.knowledge_id == knowledge_id, E.created_at >= since, E.created_at < until))
        .group_by(bucket_col).order_by(bucket_col)
    )).all()

    series = []
    for r in rows:
        total = r.total_queries or 0
        point = TrendPoint(bucket=r.bucket.date().isoformat())
        if "success_rate" in metrics:
            point.success_rate = round(100.0 * (r.answered or 0) / total, 1) if total else None
        if "avg_ms" in metrics:
            point.avg_ms = float(r.avg_ms or 0)
        if "p95_ms" in metrics:
            point.p95_ms = float(r.p95_ms or 0)
        if "cost" in metrics:
            point.cost = round(float(r.cost or 0), 6)
        if "failures" in metrics:
            point.failures = r.failures or 0
        if "total_queries" in metrics:
            point.total_queries = total
        series.append(point)

    regression = None
    if len(series) >= 2 and "success_rate" in metrics:
        a, b = series[-2], series[-1]
        if a.success_rate is not None and b.success_rate is not None:
            delta = round(b.success_rate - a.success_rate, 1)
            # One-way by design, matching fs-agent-chat/NL2SQL: only a >=5pp DROP
            # is a regression. An upward spike is never a health concern, so it
            # is intentionally not flagged.
            if delta <= -5:
                regression = {
                    "detected": True, "metric": "success_rate",
                    "from_bucket": a.bucket, "to_bucket": b.bucket, "delta": delta,
                    "note": "step-change in success_rate between adjacent buckets",
                }

    comparison = await _period_comparison(session, knowledge_id, since, until)

    return TrendsResponse(
        knowledge_id=knowledge_id, bucket=bucket, buckets=buckets,
        since=since.isoformat(), until=until.isoformat(), metrics=metrics, series=series, regression=regression,
        comparison=comparison,
    )


SORTABLE_COLUMNS = {
    "latency_ms": E.latency_ms, "cost_usd": E.cost_usd, "total_tokens": E.total_tokens, "created_at": E.created_at,
}


async def get_queries(
    session: AsyncSession, knowledge_id: str, period: str = "week",
    sort: str = "latency_ms", order: str = "desc",
    status: Optional[str] = None, failure_category: Optional[str] = None,
    feedback: Optional[str] = None,
    chat_id: Optional[str] = None, limit: int = 20, offset: int = 0,
    start: Optional[datetime] = None, end: Optional[datetime] = None,
    include_node_data: bool = True,
) -> QueriesResponse:
    # chat_id is an exact lookup that bypasses the window as a FILTER, but the
    # resolved window is still echoed in the response for consistency.
    since, until, _ = _resolve_window(period, start, end)
    filters = [E.knowledge_id == knowledge_id]
    if chat_id:
        filters.append(E.chat_id == chat_id)
    else:
        filters.extend([E.created_at >= since, E.created_at < until])
    if status:
        filters.append(E.status == status)
    if failure_category:
        filters.append(E.failure_category == failure_category)
    if feedback:
        # Feedback lives on KnowledgeChatLogs (positive/negative), keyed by
        # the SAME chat_id — mirrors fs-agent-chat's list_agent_queries filter.
        fb = feedback.strip().lower()
        _rated = select(KnowledgeChatLogs.chat_id).where(KnowledgeChatLogs.feedback.isnot(None))
        if fb == "positive":
            filters.append(E.chat_id.in_(select(KnowledgeChatLogs.chat_id).where(KnowledgeChatLogs.feedback == "positive")))
        elif fb == "negative":
            filters.append(E.chat_id.in_(select(KnowledgeChatLogs.chat_id).where(KnowledgeChatLogs.feedback == "negative")))
        elif fb in ("none", "unrated"):
            filters.append(E.chat_id.notin_(_rated))
        elif fb in ("any", "rated"):
            filters.append(E.chat_id.in_(_rated))

    total_matching = (await session.execute(select(func.count()).where(and_(*filters)))).scalar() or 0

    sort_col = SORTABLE_COLUMNS.get(sort, E.latency_ms)
    sort_col = sort_col.desc().nullslast() if order == "desc" else sort_col.asc().nullslast()

    rows = (await session.execute(
        select(E).where(and_(*filters)).order_by(sort_col).limit(limit).offset(offset)
    )).scalars().all()

    # Feedback (positive/negative) per returned row — one batch lookup keyed
    # by the same chat_id, same pattern as fs-agent-chat's fb_map.
    fb_map: Dict[str, Any] = {}
    if rows:
        fb_rows = (await session.execute(
            select(KnowledgeChatLogs.chat_id, KnowledgeChatLogs.feedback).where(
                KnowledgeChatLogs.chat_id.in_([r.chat_id for r in rows]),
                KnowledgeChatLogs.feedback.isnot(None),
            )
        )).all()
        fb_map = {fr.chat_id: fr.feedback for fr in fb_rows}

    def outcome(status_val: str) -> str:
        if status_val == "success":
            return "success"
        return "failure"

    queries = [
        QueryRow(
            chat_id=str(r.chat_id), session_id=r.session_id, user_id=r.user_id,
            created_at=r.created_at.isoformat(), query=r.query, refined_query=r.refined_query,
            route=r.route, services_used=r.services_used or [],
            status=r.status, outcome=outcome(r.status), is_answered=r.is_answered,
            confidence=r.confidence, stop_reason=r.stop_reason,
            failure_category=r.failure_category, failed_stage=r.failed_stage, failure_detail=r.failure_detail,
            feedback=fb_map.get(r.chat_id),
            provider=r.provider, model=r.model, from_widget=r.from_widget, key_type=r.key_type,
            latency_ms=r.latency_ms, input_tokens=r.input_tokens, output_tokens=r.output_tokens,
            total_tokens=r.total_tokens, retry_count=r.retry_count,
            cost_usd=float(r.cost_usd) if r.cost_usd is not None else None,
            cost_complete=r.cost_complete, cost_breakdown=r.cost_breakdown or [], trace_id=r.trace_id,
            langsmith_run_id=r.langsmith_run_id,
            # include_node_data=false drops the per-node breakdown for a lean
            # list view (matching the source's Node-Trace toggle) — the data
            # is still computed once at emit time either way, this only
            # controls whether THIS response carries it.
            node_data=(r.node_data or []) if include_node_data else [],
            # Tier 0, straight off the row. Passed through as-is, NULLs
            # included: this endpoint's job is to show what was recorded, and
            # substituting a zero for "not recorded" here would be the one
            # place a reader could not tell the difference.
            retrieved_chunk_count=r.retrieved_chunk_count,
            retrieved_doc_count=r.retrieved_doc_count,
            cited_chunk_count=r.cited_chunk_count,
            cited_doc_count=r.cited_doc_count,
            retrieval_attempts=r.retrieval_attempts,
            verifier_verdict=r.verifier_verdict,
            verifier_missing=r.verifier_missing,
            evidence_groups_loaded=r.evidence_groups_loaded,
            evidence_groups_kept=r.evidence_groups_kept,
            context_utilization=context_utilization(r.cited_chunk_count, r.retrieved_chunk_count),
        )
        for r in rows
    ]

    return QueriesResponse(
        knowledge_id=knowledge_id, period=period, since=since.isoformat(), until=until.isoformat(),
        sort=sort, order=order, total_matching=total_matching, limit=limit, offset=offset,
        returned=len(queries), queries=queries,
    )
