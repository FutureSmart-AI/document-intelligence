"""Aggregation queries over knowledge_enrichment_events for the Enrichment
Analytics read endpoints (routers/knowledge_enrichment_health.py) — see
docs/plan/enrichment-analytics/plan.md.

Sits in the same package as analytics.py (the chat Agent Health queries) and
reuses its window/comparison helpers rather than re-deriving them, so the two
halves of the Analytics screen agree on what "last week" means and on the shape
of a period-over-period delta.

Two things here work differently from the chat side, both deliberate:

1. **Cost is computed here, at read time**, from the versioned
   `usage_service_pricing` table — the chat side reads a `cost_usd` frozen onto
   each row at write time from a hardcoded map. Pricing happens over
   (provider, model, day) groups, never per row, so a month of documents costs
   a handful of price lookups rather than thousands.

2. **Latency is also reported per 1,000 characters.** Enrichment time scales
   with document length, so a raw average compares the documents each model
   happened to receive. The normalized figure is weighted
   (`SUM(duration_ms) / SUM(char_count)`), not a mean of per-document ratios —
   a mean of ratios lets one tiny document, where fixed overhead dominates,
   distort the whole number.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import Float, and_, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import literal_column

from utils.knowledge_health.analytics import _cmp, _date_trunc, _direction, _resolve_window
from utils.logger import logger
from utils.models.db_tables import KnowledgeEnrichmentEvent as EV
from utils.pydantic_models_knowledge_enrichment import (
    ByModelCost,
    DocumentRow,
    DocumentsResponse,
    EnrichmentCostBlock,
    EnrichmentFailuresResponse,
    EnrichmentPerfBlock,
    EnrichmentQualityBlock,
    EnrichmentScorecardResponse,
    EnrichmentTrendBlock,
    EnrichmentUsageBlock,
    ExpensiveDocument,
    FailureCategoryCount,
    FieldCoverageStat,
    ModeModelStat,
    ModelComparisonResponse,
    ModelStat,
    SlowDocument,
    CompletenessAvailability,
)
from utils.usage.cost_calculator import CostCalculator

# The three outcomes a row can carry. `validation_failed` is kept separate from
# `error` because they need different fixes: the first is a prompt/schema
# problem, the second is a call that never came back.
STATUS_SUCCESS = "success"
STATUS_VALIDATION_FAILED = "validation_failed"
STATUS_ERROR = "error"
FAILURE_STATUSES = (STATUS_VALIDATION_FAILED, STATUS_ERROR)

_FIELD_DATA_KEYS = ("field_name", "level", "populated", "value_count")


def _field_expr(value_col, key: str, cast_to=None):
    """``value_col ->> '<key>'`` with the key inlined as a SQL literal.

    Same reasoning as analytics.py's ``_node_field``: this expression is reused
    across SELECT/GROUP BY, and a bind-parameter key gets a fresh ``$n`` per
    occurrence, which Postgres's GROUP BY check rejects even though every
    occurrence binds the identical string. ``key`` is always one of this
    module's own ``_FIELD_DATA_KEYS``, never request input.
    """
    assert key in _FIELD_DATA_KEYS
    text_expr = value_col.op("->>")(literal_column(f"'{key}'"))
    return cast(text_expr, cast_to) if cast_to is not None else text_expr


def _ms_per_1k(duration_ms: Optional[float], char_count: Optional[float]) -> Optional[float]:
    """Latency normalized by document size. None when size is unknown — a
    normalized figure computed against a zero/absent char_count would be an
    invention, and 0 would read as "instant"."""
    if not char_count or not duration_ms:
        return None
    return round(float(duration_ms) * 1000.0 / float(char_count), 1)


def _coverage_pct(populated: Optional[int], configured: Optional[int]) -> Optional[float]:
    if not configured:
        return None
    return round((populated or 0) / configured * 100, 1)


def _window_filter(knowledge_id: str, since: datetime, until: datetime):
    """The half-open `[since, until)` filter every query here shares."""
    return and_(EV.knowledge_id == knowledge_id, EV.created_at >= since, EV.created_at < until)


# ---------------------------------------------------------------------------
# Pricing — read-time, from the versioned pricing table
# ---------------------------------------------------------------------------

async def _priced_groups(
    session: AsyncSession, knowledge_id: str, since: datetime, until: datetime
) -> Tuple[Optional[float], bool, Dict[Tuple[Optional[str], Optional[str]], Dict[str, Any]]]:
    """Price the window by (provider, model), grouping by day underneath.

    The day grouping is what makes a mid-window price change come out right: a
    document enriched before the change is priced at the old rate and one after
    it at the new rate, because `get_pricing` is asked for the rate effective on
    that document's own day. Grouping only by (provider, model) would apply one
    rate to the whole window.

    Returns `(total_cost, cost_complete, by_model)`. `total_cost` sums only the
    priced groups and is a documented lower bound when `cost_complete` is False;
    an unpriced model's cost is None, never 0 — the same rule
    chat/pricing.py::price_breakdown follows, and deliberately not the silent
    zero utils/usage/cost_calculator.py returns for an unknown model.
    """
    day = _date_trunc("day", EV.created_at).label("day")
    rows = (await session.execute(
        select(
            EV.provider, EV.model, day,
            func.count().label("documents"),
            func.coalesce(func.sum(EV.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(EV.output_tokens), 0).label("output_tokens"),
        )
        .where(_window_filter(knowledge_id, since, until))
        .group_by(EV.provider, EV.model, day)
    )).all()

    calc = CostCalculator(session)
    by_model: Dict[Tuple[Optional[str], Optional[str]], Dict[str, Any]] = {}
    total = 0.0
    any_priced = False
    complete = True

    for r in rows:
        key = (r.provider, r.model)
        entry = by_model.setdefault(key, {"documents": 0, "cost": 0.0, "priced": False, "complete": True})
        entry["documents"] += int(r.documents or 0)

        pricing = None
        if r.model and r.provider:
            pricing = await calc.get_pricing(r.model, r.provider, "llm", r.day)

        if pricing and pricing.get("pricing_model") == "token_based":
            group_cost, _, _ = calc.calculate_llm_cost(
                float(r.input_tokens or 0), float(r.output_tokens or 0), pricing["pricing_details"]
            )
            entry["cost"] += group_cost
            entry["priced"] = True
            total += group_cost
            any_priced = True
        else:
            # No pricing row for this model on this day. Mark the model line
            # incomplete rather than adding a zero.
            entry["complete"] = False
            complete = False
            logger.info(
                f"[enrichment-analytics] no pricing for provider={r.provider} model={r.model} "
                f"day={r.day} — cost reported as partial"
            )

    for entry in by_model.values():
        entry["cost"] = round(entry["cost"], 6) if entry["priced"] else None

    return (round(total, 6) if any_priced else None), complete, by_model


# ---------------------------------------------------------------------------
# Window-level aggregates
# ---------------------------------------------------------------------------

async def _counts_and_perf(session: AsyncSession, knowledge_id: str, since: datetime, until: datetime) -> dict:
    """One query for the outcome counts, latency distribution, size totals and
    mean field coverage over a window. Used twice per scorecard — once for the
    current window, once for the previous one, for the trend deltas."""
    coverage_expr = case(
        (and_(EV.status == STATUS_SUCCESS, EV.fields_configured > 0),
         cast(EV.fields_populated, Float) / cast(EV.fields_configured, Float)),
        else_=None,
    )
    row = (await session.execute(
        select(
            func.count().label("total"),
            func.sum(case((EV.status == STATUS_SUCCESS, 1), else_=0)).label("succeeded"),
            func.sum(case((EV.status == STATUS_VALIDATION_FAILED, 1), else_=0)).label("validation_failed"),
            func.sum(case((EV.status == STATUS_ERROR, 1), else_=0)).label("error"),
            func.count(func.distinct(EV.object_id)).label("unique_documents"),
            func.coalesce(func.sum(EV.char_count), 0).label("total_chars"),
            func.coalesce(func.sum(EV.chunk_count), 0).label("total_chunks"),
            func.avg(EV.duration_ms).label("avg_ms"),
            func.percentile_cont(0.5).within_group(EV.duration_ms).label("p50_ms"),
            func.percentile_cont(0.95).within_group(EV.duration_ms).label("p95_ms"),
            # Weighted normalization: totals divided, not a mean of ratios.
            func.sum(case((EV.char_count > 0, EV.duration_ms), else_=0)).label("sized_duration_ms"),
            func.sum(case((EV.char_count > 0, EV.char_count), else_=0)).label("sized_chars"),
            func.coalesce(func.sum(EV.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(EV.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(EV.total_tokens), 0).label("total_tokens"),
            func.avg(coverage_expr).label("avg_coverage"),
        ).where(_window_filter(knowledge_id, since, until))
    )).one()

    total = int(row.total or 0)
    succeeded = int(row.succeeded or 0)
    return {
        "total": total,
        "succeeded": succeeded,
        "validation_failed": int(row.validation_failed or 0),
        "error": int(row.error or 0),
        "unique_documents": int(row.unique_documents or 0),
        "total_chars": int(row.total_chars or 0),
        "total_chunks": int(row.total_chunks or 0),
        "success_rate": round(succeeded / total * 100, 1) if total else 0.0,
        "avg_ms": round(float(row.avg_ms or 0), 1),
        "p50_ms": round(float(row.p50_ms or 0), 1),
        "p95_ms": round(float(row.p95_ms or 0), 1),
        "avg_ms_per_1k_chars": _ms_per_1k(row.sized_duration_ms, row.sized_chars),
        "input_tokens": int(row.input_tokens or 0),
        "output_tokens": int(row.output_tokens or 0),
        "total_tokens": int(row.total_tokens or 0),
        "avg_field_coverage": round(float(row.avg_coverage) * 100, 1) if row.avg_coverage is not None else None,
    }


async def _per_day(session: AsyncSession, knowledge_id: str, since: datetime, until: datetime) -> List[Dict[str, Any]]:
    bucket = _date_trunc("day", EV.created_at).label("bucket")
    rows = (await session.execute(
        select(
            bucket,
            func.count().label("documents"),
            func.sum(case((EV.status == STATUS_SUCCESS, 1), else_=0)).label("succeeded"),
            func.coalesce(func.sum(EV.char_count), 0).label("chars"),
            func.avg(EV.duration_ms).label("avg_ms"),
        )
        .where(_window_filter(knowledge_id, since, until))
        .group_by(bucket).order_by(bucket)
    )).all()
    return [
        {
            "bucket": r.bucket.isoformat().replace("+00:00", "Z") if r.bucket else None,
            "day": r.bucket.date().isoformat() if r.bucket else None,
            "documents": int(r.documents or 0),
            "succeeded": int(r.succeeded or 0),
            "chars": int(r.chars or 0),
            "avg_ms": round(float(r.avg_ms or 0), 1),
        }
        for r in rows
    ]


async def _by_failure_category(
    session: AsyncSession, knowledge_id: str, since: datetime, until: datetime
) -> List[FailureCategoryCount]:
    rows = (await session.execute(
        select(EV.failure_category, func.count().label("c"))
        .where(and_(_window_filter(knowledge_id, since, until), EV.status.in_(FAILURE_STATUSES)))
        .group_by(EV.failure_category).order_by(func.count().desc())
    )).all()
    total = sum(int(r.c or 0) for r in rows)
    return [
        FailureCategoryCount(
            failure_category=r.failure_category,
            count=int(r.c or 0),
            pct_of_failures=round(int(r.c or 0) / total * 100, 1) if total else 0.0,
        )
        for r in rows
    ]


async def _slowest(
    session: AsyncSession, knowledge_id: str, since: datetime, until: datetime, top_n: int
) -> List[SlowDocument]:
    rows = (await session.execute(
        select(EV.object_id, EV.document_name, EV.duration_ms, EV.char_count, EV.model, EV.trace_id)
        .where(_window_filter(knowledge_id, since, until))
        .order_by(EV.duration_ms.desc()).limit(top_n)
    )).all()
    return [
        SlowDocument(
            object_id=r.object_id, document_name=r.document_name,
            duration_ms=int(r.duration_ms or 0), char_count=int(r.char_count or 0),
            ms_per_1k_chars=_ms_per_1k(r.duration_ms, r.char_count),
            model=r.model, trace_id=r.trace_id,
        )
        for r in rows
    ]


async def _most_expensive(
    session: AsyncSession, knowledge_id: str, since: datetime, until: datetime, top_n: int
) -> List[ExpensiveDocument]:
    """Top documents by token count, priced individually.

    Ordered by tokens rather than by cost, because cost is not a column here —
    ordering by it would mean pricing every row in the window. Within one
    (provider, model) the two orders agree; across models they can differ, so
    this is "the heaviest documents, with their cost" rather than a strict
    cost ranking. The same caveat applies to chat's `most_expensive`, which
    orders by its stored cost.
    """
    rows = (await session.execute(
        select(EV.object_id, EV.document_name, EV.total_tokens, EV.input_tokens, EV.output_tokens,
               EV.provider, EV.model, EV.trace_id, EV.created_at)
        .where(_window_filter(knowledge_id, since, until))
        .order_by(EV.total_tokens.desc()).limit(top_n)
    )).all()

    calc = CostCalculator(session)
    out: List[ExpensiveDocument] = []
    for r in rows:
        cost = None
        if r.model and r.provider:
            pricing = await calc.get_pricing(r.model, r.provider, "llm", r.created_at)
            if pricing and pricing.get("pricing_model") == "token_based":
                cost, _, _ = calc.calculate_llm_cost(
                    float(r.input_tokens or 0), float(r.output_tokens or 0), pricing["pricing_details"]
                )
        out.append(ExpensiveDocument(
            object_id=r.object_id, document_name=r.document_name,
            total_tokens=int(r.total_tokens or 0), cost_usd=cost, model=r.model, trace_id=r.trace_id,
        ))
    return out


# ---------------------------------------------------------------------------
# GET /scorecard
# ---------------------------------------------------------------------------

async def get_enrichment_scorecard(
    session: AsyncSession,
    knowledge_id: str,
    period: str = "week",
    top_n: int = 10,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> EnrichmentScorecardResponse:
    """Usage / quality / perf / cost / trend for the enrichment step, in one
    call — the enrichment counterpart of analytics.py::get_scorecard."""
    since, until, previous_since = _resolve_window(period, start, end)
    logger.info(f"[enrichment-analytics] scorecard knowledge_id={knowledge_id} since={since} until={until}")

    cur = await _counts_and_perf(session, knowledge_id, since, until)
    prev = await _counts_and_perf(session, knowledge_id, previous_since, since)
    per_day = await _per_day(session, knowledge_id, since, until)
    categories = await _by_failure_category(session, knowledge_id, since, until)
    slowest = await _slowest(session, knowledge_id, since, until, top_n)
    total_cost, cost_complete, by_model = await _priced_groups(session, knowledge_id, since, until)
    prev_cost, _, _ = await _priced_groups(session, knowledge_id, previous_since, since)
    expensive = await _most_expensive(session, knowledge_id, since, until, top_n)

    total = cur["total"]
    avg_cost = round(total_cost / total, 6) if (total_cost is not None and total) else None

    # `direction` follows success rate, the metric a reader checks first — same
    # convention as the chat scorecard's trend block.
    sr_delta = round(cur["success_rate"] - prev["success_rate"], 1) if prev["total"] else None
    cov_delta = None
    if cur["avg_field_coverage"] is not None and prev["avg_field_coverage"] is not None:
        cov_delta = round(cur["avg_field_coverage"] - prev["avg_field_coverage"], 1)

    return EnrichmentScorecardResponse(
        knowledge_id=knowledge_id,
        period=period,
        since=since.isoformat().replace("+00:00", "Z"),
        until=until.isoformat().replace("+00:00", "Z"),
        previous_since=previous_since.isoformat().replace("+00:00", "Z"),
        usage=EnrichmentUsageBlock(
            total_documents=total,
            unique_documents=cur["unique_documents"],
            total_chars=cur["total_chars"],
            total_chunks=cur["total_chunks"],
            per_day=per_day,
        ),
        quality=EnrichmentQualityBlock(
            succeeded=cur["succeeded"],
            validation_failed=cur["validation_failed"],
            error=cur["error"],
            success_rate=cur["success_rate"],
            avg_field_coverage=cur["avg_field_coverage"],
            by_failure_category=categories,
        ),
        perf=EnrichmentPerfBlock(
            avg_ms=cur["avg_ms"], p50_ms=cur["p50_ms"], p95_ms=cur["p95_ms"],
            avg_ms_per_1k_chars=cur["avg_ms_per_1k_chars"], slowest=slowest,
        ),
        cost=EnrichmentCostBlock(
            total_cost=total_cost,
            avg_cost_per_document=avg_cost,
            total_tokens=cur["total_tokens"],
            input_tokens=cur["input_tokens"],
            output_tokens=cur["output_tokens"],
            cost_complete=cost_complete,
            by_model=[
                ByModelCost(provider=p, model=m, documents=v["documents"], cost=v["cost"], cost_complete=v["complete"])
                for (p, m), v in sorted(by_model.items(), key=lambda kv: -(kv[1]["cost"] or 0))
            ],
            most_expensive=expensive,
        ),
        trend=EnrichmentTrendBlock(
            prev_success_rate=prev["success_rate"] if prev["total"] else None,
            success_rate_delta=sr_delta,
            prev_avg_ms=prev["avg_ms"] if prev["total"] else None,
            avg_ms_delta=round(cur["avg_ms"] - prev["avg_ms"], 1) if prev["total"] else None,
            prev_total_cost=prev_cost,
            cost_delta=round((total_cost or 0) - prev_cost, 6) if prev_cost is not None else None,
            prev_avg_field_coverage=prev["avg_field_coverage"],
            field_coverage_delta=cov_delta,
            direction=_direction(sr_delta),
        ),
    )


# ---------------------------------------------------------------------------
# GET /model-comparison
# ---------------------------------------------------------------------------

async def _by_field_coverage(
    session: AsyncSession, knowledge_id: str, since: datetime, until: datetime, model: Optional[str] = None
) -> List[FieldCoverageStat]:
    """Per-configured-field coverage, by expanding each row's `field_data`
    JSONB array via a LATERAL jsonb_array_elements — the same technique
    analytics.py::_by_node uses for `node_data`. A document with 8 configured
    fields contributes 8 expanded rows before GROUP BY collapses them across
    documents.

    Scoped to successful attempts: a failed attempt populated nothing, and
    counting its fields as un-populated would blame the fields for a failure
    that belongs to the call.
    """
    elems = func.jsonb_array_elements(EV.field_data).table_valued("value", joins_implicitly=True)
    v = elems.c.value
    field_name = _field_expr(v, "field_name").label("field_name")
    level = _field_expr(v, "level").label("level")
    populated = _field_expr(v, "populated")
    value_count = _field_expr(v, "value_count", Float)

    filters = [_window_filter(knowledge_id, since, until), EV.status == STATUS_SUCCESS]
    if model:
        filters.append(EV.model == model)

    rows = (await session.execute(
        select(
            field_name, level,
            func.count().label("documents"),
            func.sum(case((populated == "true", 1), else_=0)).label("populated"),
            func.avg(value_count).label("avg_value_count"),
        )
        # Explicit select_from(EV, elems): Postgres resolves the implicit LATERAL
        # only when the table appears BEFORE the function that references its
        # column. Without this, SQLAlchemy infers the FROM list from the selected
        # columns -- which name only `elems` -- and emits
        # `FROM jsonb_array_elements(...) AS anon_1, knowledge_enrichment_events`,
        # a comma-join whose first entry references a table introduced second.
        # Postgres rejects that with "missing FROM-clause entry", a hard 500 on
        # GET /model-comparison. analytics.py::_by_node carries the same line for
        # the same reason; this function's docstring claimed the technique while
        # omitting the one part that makes it work.
        .select_from(EV, elems)
        .where(and_(*filters))
        .group_by(field_name, level)
        .order_by(func.count().desc())
    )).all()

    out: List[FieldCoverageStat] = []
    for r in rows:
        docs = int(r.documents or 0)
        pop = int(r.populated or 0)
        out.append(FieldCoverageStat(
            field_name=r.field_name, level=r.level, documents=docs, populated=pop,
            coverage_pct=round(pop / docs * 100, 1) if docs else 0.0,
            avg_value_count=round(float(r.avg_value_count or 0), 2),
        ))
    return out


async def get_model_comparison(
    session: AsyncSession,
    knowledge_id: str,
    period: str = "week",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    model: Optional[str] = None,
) -> ModelComparisonResponse:
    """One row per (provider, model, strategy) over the window, plus per-field
    coverage — the panel this whole feature exists for.

    The enrichment analogue of the chat scorecard's `by_node`, re-cut by model
    and strategy: enrichment has no graph nodes, but the same model behaves
    differently under strategy 1 (an LLM call for the document AND its chunks)
    than under strategy 2 (pre-split, one call).
    """
    since, until, _ = _resolve_window(period, start, end)
    logger.info(f"[enrichment-analytics] model-comparison knowledge_id={knowledge_id} since={since} until={until}")

    coverage_expr = case(
        (and_(EV.status == STATUS_SUCCESS, EV.fields_configured > 0),
         cast(EV.fields_populated, Float) / cast(EV.fields_configured, Float)),
        else_=None,
    )
    filters = [_window_filter(knowledge_id, since, until)]
    if model:
        filters.append(EV.model == model)

    rows = (await session.execute(
        select(
            EV.provider, EV.model, EV.enrichment_strategy_name,
            func.count().label("documents"),
            func.sum(case((EV.status == STATUS_SUCCESS, 1), else_=0)).label("succeeded"),
            func.sum(case((EV.status == STATUS_VALIDATION_FAILED, 1), else_=0)).label("validation_failed"),
            func.sum(case((EV.status == STATUS_ERROR, 1), else_=0)).label("error"),
            func.avg(EV.duration_ms).label("avg_ms"),
            func.percentile_cont(0.5).within_group(EV.duration_ms).label("p50_ms"),
            func.percentile_cont(0.95).within_group(EV.duration_ms).label("p95_ms"),
            func.sum(case((EV.char_count > 0, EV.duration_ms), else_=0)).label("sized_duration_ms"),
            func.sum(case((EV.char_count > 0, EV.char_count), else_=0)).label("sized_chars"),
            func.coalesce(func.sum(EV.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(EV.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(EV.total_tokens), 0).label("total_tokens"),
            func.avg(coverage_expr).label("avg_coverage"),
            func.sum(case((EV.used_fallback.is_(True), 1), else_=0)).label("fallback_count"),
            # Cost needs a rate per day, so carry the window's midpoint day per
            # group: the first day this (model, strategy) ran in the window.
            func.min(EV.created_at).label("first_seen"),
        )
        .where(and_(*filters))
        .group_by(EV.provider, EV.model, EV.enrichment_strategy_name)
        .order_by(func.count().desc())
    )).all()

    calc = CostCalculator(session)
    models: List[ModelStat] = []
    for r in rows:
        documents = int(r.documents or 0)
        succeeded = int(r.succeeded or 0)

        cost = None
        complete = True
        if r.model and r.provider:
            pricing = await calc.get_pricing(r.model, r.provider, "llm", r.first_seen)
            if pricing and pricing.get("pricing_model") == "token_based":
                cost, _, _ = calc.calculate_llm_cost(
                    float(r.input_tokens or 0), float(r.output_tokens or 0), pricing["pricing_details"]
                )
            else:
                complete = False
        else:
            complete = False

        models.append(ModelStat(
            provider=r.provider, model=r.model, enrichment_strategy_name=r.enrichment_strategy_name,
            documents=documents, succeeded=succeeded,
            validation_failed=int(r.validation_failed or 0), error=int(r.error or 0),
            success_rate=round(succeeded / documents * 100, 1) if documents else 0.0,
            avg_ms=round(float(r.avg_ms or 0), 1),
            p50_ms=round(float(r.p50_ms or 0), 1),
            p95_ms=round(float(r.p95_ms or 0), 1),
            avg_ms_per_1k_chars=_ms_per_1k(r.sized_duration_ms, r.sized_chars),
            total_tokens=int(r.total_tokens or 0),
            avg_tokens_per_document=round(int(r.total_tokens or 0) / documents, 1) if documents else 0.0,
            total_cost=round(cost, 6) if cost is not None else None,
            avg_cost_per_document=round(cost / documents, 6) if (cost is not None and documents) else None,
            cost_complete=complete,
            avg_field_coverage=round(float(r.avg_coverage) * 100, 1) if r.avg_coverage is not None else None,
            fallback_count=int(r.fallback_count or 0),
        ))

    return ModelComparisonResponse(
        knowledge_id=knowledge_id,
        period=period,
        since=since.isoformat().replace("+00:00", "Z"),
        until=until.isoformat().replace("+00:00", "Z"),
        models=models,
        by_field=await _by_field_coverage(session, knowledge_id, since, until, model=model),
        by_mode_model=await _by_mode_model(session, knowledge_id, since, until, model=model),
        completeness=await _completeness_availability(session, knowledge_id, since, until),
    )


async def _by_mode_model(
    session: AsyncSession, knowledge_id: str, since: datetime, until: datetime,
    model: Optional[str] = None,
) -> List[ModeModelStat]:
    """The mode x model cross-tab: one row per (mode, provider, model).

    Separates two factors that the default routing keeps welded together —
    `get_enrichment_config` level 2 upgrades the model for every mode except
    text_based, so cutting by either factor alone attributes the whole effect to
    whichever one you happened to group by. See ModeModelStat's docstring for
    the measurement that proved both single-factor readings wrong.

    Rows with no mode (written before migration 046) are EXCLUDED rather than
    bucketed: folding them into any mode would attribute their results to a mode
    they may not have run in, and folding them into a synthetic "unknown" cell
    invites averaging across it.
    """
    coverage_expr = case(
        (and_(EV.status == STATUS_SUCCESS, EV.fields_configured > 0),
         cast(EV.fields_populated, Float) / cast(EV.fields_configured, Float)),
        else_=None,
    )
    filters = [
        _window_filter(knowledge_id, since, until),
        EV.document_processing_mode.isnot(None),
    ]
    if model:
        filters.append(EV.model == model)

    rows = (await session.execute(
        select(
            EV.document_processing_mode, EV.provider, EV.model,
            func.count().label("documents"),
            func.sum(case((EV.status == STATUS_SUCCESS, 1), else_=0)).label("succeeded"),
            func.avg(EV.duration_ms).label("avg_ms"),
            func.sum(case((EV.char_count > 0, EV.duration_ms), else_=0)).label("sized_duration_ms"),
            func.sum(case((EV.char_count > 0, EV.char_count), else_=0)).label("sized_chars"),
            func.avg(EV.char_count).label("avg_chars"),
            func.coalesce(func.sum(EV.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(EV.output_tokens), 0).label("output_tokens"),
            func.avg(coverage_expr).label("avg_coverage"),
            # AVG skips NULLs, so an unvalidated row lowers neither score — it
            # simply is not counted. `validated` is carried alongside so a
            # reader can tell "0.9 over 40 documents" from "0.9 over one".
            func.avg(EV.correctness_score).label("avg_correctness"),
            func.avg(EV.completeness_score).label("avg_completeness"),
            func.sum(case((EV.validation_status.isnot(None), 1), else_=0)).label("validated"),
            # `validated` counts every non-NULL status, errors included, so it
            # answers "was this row attempted" rather than "was it scored".
            # Carried alongside because the two diverge exactly when something is
            # wrong: a cell reading validated=14 / errors=14 has been attempted
            # fourteen times and scored zero times, and without this count that
            # is indistinguishable from a healthy cell whose averages are NULL
            # for some benign reason.
            func.sum(case((EV.validation_status == "error", 1), else_=0)).label("validation_errors"),
            # Counted separately from `validated`: completeness is gated by its
            # own flag, so a fully correctness-validated cell can hold zero
            # completeness scores. AVG already skips NULLs, so without this
            # count a NULL average and a real one are indistinguishable.
            func.sum(case((EV.completeness_score.isnot(None), 1), else_=0)).label("completeness_scored"),
            func.min(EV.created_at).label("first_seen"),
        )
        .where(and_(*filters))
        .group_by(EV.document_processing_mode, EV.provider, EV.model)
        .order_by(func.count().desc())
    )).all()

    calc = CostCalculator(session)
    out: List[ModeModelStat] = []
    for r in rows:
        documents = int(r.documents or 0)
        cost, complete = None, True
        # The parameter is `effective_date`, not `at`. Passing `at=` raised
        # TypeError at request time and made GET /model-comparison a hard 500 for
        # any window containing mode-bearing rows — this whole cross-tab had
        # therefore never once executed in production. Every other call site in
        # this module passes the date positionally, which is why they were fine.
        pricing = await calc.get_pricing(
            service_name=r.model, provider=r.provider, service_type="llm",
            effective_date=r.first_seen,
        ) if r.model else None
        if pricing and pricing.get("pricing_model") == "token_based":
            cost, _, _ = calc.calculate_llm_cost(
                float(r.input_tokens or 0), float(r.output_tokens or 0), pricing["pricing_details"]
            )
        else:
            # No price row for this model: cost is UNKNOWN, not zero. A 0.0 here
            # would make an unpriced model look free and win every cost
            # comparison it appears in.
            complete = False

        sized_chars = int(r.sized_chars or 0)
        out.append(ModeModelStat(
            document_processing_mode=r.document_processing_mode,
            provider=r.provider, model=r.model,
            documents=documents,
            succeeded=int(r.succeeded or 0),
            success_rate=round(int(r.succeeded or 0) / documents * 100, 1) if documents else 0.0,
            avg_ms=round(float(r.avg_ms or 0), 1),
            avg_ms_per_1k_chars=(
                round(int(r.sized_duration_ms or 0) / sized_chars * 1000, 1) if sized_chars else None
            ),
            avg_char_count=round(float(r.avg_chars), 1) if r.avg_chars is not None else None,
            total_cost=round(cost, 6) if cost is not None else None,
            cost_complete=complete,
            avg_field_coverage=round(float(r.avg_coverage) * 100, 1) if r.avg_coverage is not None else None,
            avg_correctness=round(float(r.avg_correctness) * 100, 1) if r.avg_correctness is not None else None,
            avg_completeness=round(float(r.avg_completeness) * 100, 1) if r.avg_completeness is not None else None,
            validated_documents=int(r.validated or 0),
            validation_errors=int(r.validation_errors or 0),
            completeness_scored_documents=int(r.completeness_scored or 0),
        ))
    return out


async def _completeness_availability(
    session: AsyncSession, knowledge_id: str, since, until
) -> CompletenessAvailability:
    """Say whether completeness may be read from this window, and why not if not.

    A NULL completeness average is ambiguous three ways and a consumer has to
    tell them apart before rendering or ranking: the metric is switched off, it
    is on but nothing has been scored yet, or it ran and legitimately could not
    score these documents. Collapsing those to 0.0 would invent the worst
    possible score for every model and invert any ranking built on it.
    """
    from utils.setup_env import EXTRACTION_COMPLETENESS_ENABLED

    scored = int((await session.execute(
        select(func.count()).where(and_(
            _window_filter(knowledge_id, since, until),
            EV.completeness_score.isnot(None),
        ))
    )).scalar() or 0)

    if not EXTRACTION_COMPLETENESS_ENABLED:
        return CompletenessAvailability(
            enabled=False, scored_documents=scored, status="disabled",
            reason=(
                "Completeness is disabled (EXTRACTION_COMPLETENESS_ENABLED). "
                "Every avg_completeness in this response is NULL because the "
                "metric did not run — do not read it as 0."
            ),
        )
    if scored == 0:
        return CompletenessAvailability(
            enabled=True, scored_documents=0, status="no_scored_documents",
            reason=(
                "Completeness is enabled but no document in this window has been "
                "scored yet. Run POST /health/enrichment/validate."
            ),
        )
    return CompletenessAvailability(
        enabled=True, scored_documents=scored, status="available", reason=None,
    )


# ---------------------------------------------------------------------------
# GET /documents  (per-document drill-down)
# ---------------------------------------------------------------------------

async def get_enrichment_documents(
    session: AsyncSession,
    knowledge_id: str,
    period: str = "week",
    status: Optional[str] = None,
    failure_category: Optional[str] = None,
    model: Optional[str] = None,
    object_id: Optional[str] = None,
    include_field_data: bool = False,
    sort_by: str = "created_at",
    limit: int = 50,
    offset: int = 0,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> DocumentsResponse:
    """Filterable, paginated per-attempt rows — the enrichment counterpart of
    analytics.py::get_queries.

    As on that endpoint, the window is resolved and echoed even when
    `object_id` is set, but is not applied as a filter in that case: an exact
    document lookup should return every attempt on it, including the failures
    that preceded a success, whenever they happened.
    """
    since, until, _ = _resolve_window(period, start, end)
    logger.info(
        f"[enrichment-analytics] documents knowledge_id={knowledge_id} status={status} "
        f"model={model} object_id={object_id} limit={limit} offset={offset}"
    )

    filters = [EV.knowledge_id == knowledge_id]
    if object_id:
        filters.append(EV.object_id == object_id)
    else:
        filters += [EV.created_at >= since, EV.created_at < until]
    if status:
        filters.append(EV.status == status)
    if failure_category:
        filters.append(EV.failure_category == failure_category)
    if model:
        filters.append(EV.model == model)

    total_matching = int((await session.execute(
        select(func.count()).select_from(EV).where(and_(*filters))
    )).scalar() or 0)

    sort_column = {
        "created_at": EV.created_at,
        "duration_ms": EV.duration_ms,
        "total_tokens": EV.total_tokens,
        "char_count": EV.char_count,
    }.get(sort_by, EV.created_at)

    rows = (await session.execute(
        select(EV).where(and_(*filters)).order_by(sort_column.desc()).limit(limit).offset(offset)
    )).scalars().all()

    calc = CostCalculator(session)
    documents: List[DocumentRow] = []
    for e in rows:
        # Prefer the finalized LangSmith figure when the finalizer stored one.
        # Recomputing from the catalog unconditionally made the finalizer's whole
        # output invisible to every API consumer: it wrote cost_usd and nothing
        # ever read it. The estimate stays as the fallback, but it is now labelled
        # rather than passed off as the same number.
        cost = float(e.cost_usd) if e.cost_usd is not None else None
        cost_source = "langsmith" if cost is not None else None
        if cost is None and e.model and e.provider:
            pricing = await calc.get_pricing(e.model, e.provider, "llm", e.created_at)
            if pricing and pricing.get("pricing_model") == "token_based":
                cost, _, _ = calc.calculate_llm_cost(
                    float(e.input_tokens or 0), float(e.output_tokens or 0), pricing["pricing_details"]
                )
                cost_source = "estimate" if cost is not None else None
        documents.append(DocumentRow(
            event_key=str(e.event_key), object_id=e.object_id, document_name=e.document_name,
            created_at=e.created_at.isoformat().replace("+00:00", "Z") if e.created_at else "",
            status=e.status, failure_category=e.failure_category, failure_detail=e.failure_detail,
            requested_model=e.requested_model, provider=e.provider, model=e.model,
            used_fallback=bool(e.used_fallback), fallback_reason=e.fallback_reason,
            enrichment_strategy_name=e.enrichment_strategy_name,
            char_count=int(e.char_count or 0), chunk_count=int(e.chunk_count or 0),
            duration_ms=int(e.duration_ms or 0),
            ms_per_1k_chars=_ms_per_1k(e.duration_ms, e.char_count),
            retry_count=int(e.retry_count or 0),
            cost_source=cost_source, cost_complete=bool(e.cost_complete),
            langsmith_run_id=e.langsmith_run_id,
            input_tokens=int(e.input_tokens or 0), output_tokens=int(e.output_tokens or 0),
            total_tokens=int(e.total_tokens or 0), cost_usd=cost,
            fields_configured=int(e.fields_configured or 0), fields_populated=int(e.fields_populated or 0),
            field_coverage=_coverage_pct(e.fields_populated, e.fields_configured),
            # Dropped from the payload unless asked for — a document with a wide
            # schema carries a long array, and the list view never renders it.
            field_data=(e.field_data or []) if include_field_data else None,
            document_processing_mode=e.document_processing_mode,
            # Passed through as-is, NULL included. Coalescing a missing score to
            # 0.0 here would turn "not validated" into "scored zero", which is
            # the opposite claim — see migration 046.
            validation_status=e.validation_status,
            validation_model=e.validation_model,
            correctness_score=e.correctness_score,
            completeness_score=e.completeness_score,
            validation_ms=e.validation_ms,
            validation_cost_usd=float(e.validation_cost_usd) if e.validation_cost_usd is not None else None,
            trace_id=e.trace_id,
        ))

    return DocumentsResponse(
        knowledge_id=knowledge_id, period=period,
        since=since.isoformat().replace("+00:00", "Z"),
        until=until.isoformat().replace("+00:00", "Z"),
        total_matching=total_matching, limit=limit, offset=offset, documents=documents,
    )


# ---------------------------------------------------------------------------
# GET /failures
# ---------------------------------------------------------------------------

async def get_enrichment_failures(
    session: AsyncSession,
    knowledge_id: str,
    period: str = "week",
    failure_category: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> EnrichmentFailuresResponse:
    """Failure breakdown by category and by model, plus the documents that keep
    failing — the enrichment counterpart of analytics.py::get_failures.

    Note this endpoint can only report what the worker sends. Before the
    failure-emit path landed, a failed enrichment produced no row anywhere
    (all three LLM strategies raise before reporting), so a window that predates
    it will show zero failures regardless of what actually happened. See
    docs/plan/enrichment-analytics/state.md.
    """
    since, until, _ = _resolve_window(period, start, end)
    logger.info(f"[enrichment-analytics] failures knowledge_id={knowledge_id} since={since} until={until}")

    window = _window_filter(knowledge_id, since, until)
    total_attempts = int((await session.execute(
        select(func.count()).select_from(EV).where(window)
    )).scalar() or 0)

    fail_filters = [window, EV.status.in_(FAILURE_STATUSES)]
    if failure_category:
        fail_filters.append(EV.failure_category == failure_category)

    total_failures = int((await session.execute(
        select(func.count()).select_from(EV).where(and_(*fail_filters))
    )).scalar() or 0)

    by_category = await _by_failure_category(session, knowledge_id, since, until)

    # Failures per model, next to that model's attempt count — a raw failure
    # count ranks the most-used model first, which is not the same as the worst.
    model_rows = (await session.execute(
        select(
            EV.provider, EV.model,
            func.count().label("attempts"),
            func.sum(case((EV.status.in_(FAILURE_STATUSES), 1), else_=0)).label("failures"),
        ).where(window).group_by(EV.provider, EV.model)
    )).all()
    by_model = [
        {
            "provider": r.provider, "model": r.model,
            "attempts": int(r.attempts or 0), "failures": int(r.failures or 0),
            "failure_rate": round(int(r.failures or 0) / int(r.attempts) * 100, 1) if r.attempts else 0.0,
        }
        for r in sorted(model_rows, key=lambda r: -(int(r.failures or 0)))
    ]

    # Documents grouped by object_id, worst first. One query, no per-row
    # follow-up: the latest failure's detail is pulled with max(created_at)'s
    # row via a window-free aggregate on the failure subset, which is enough
    # for a list view and avoids failures' N+1 on the chat side.
    doc_rows = (await session.execute(
        select(
            EV.object_id,
            func.max(EV.document_name).label("document_name"),
            func.count().label("attempts"),
            func.sum(case((EV.status.in_(FAILURE_STATUSES), 1), else_=0)).label("failures"),
            func.max(EV.failure_category).label("failure_category"),
            func.max(EV.failure_detail).label("latest_failure_detail"),
            func.max(EV.trace_id).label("latest_trace_id"),
            func.max(EV.model).label("model"),
        )
        .where(and_(*fail_filters))
        .group_by(EV.object_id)
        .order_by(func.count().desc())
        .limit(limit).offset(offset)
    )).all()

    top_failed_documents = [
        {
            "object_id": r.object_id, "document_name": r.document_name,
            "attempts": int(r.attempts or 0), "failures": int(r.failures or 0),
            "failure_category": r.failure_category,
            "latest_failure_detail": r.latest_failure_detail,
            "latest_event_key": None,
            "latest_trace_id": r.latest_trace_id,
            "model": r.model,
        }
        for r in doc_rows
    ]

    return EnrichmentFailuresResponse(
        knowledge_id=knowledge_id, period=period,
        since=since.isoformat().replace("+00:00", "Z"),
        until=until.isoformat().replace("+00:00", "Z"),
        total_attempts=total_attempts,
        total_failures=total_failures,
        failure_rate=round(total_failures / total_attempts * 100, 1) if total_attempts else 0.0,
        by_category=by_category,
        by_model=by_model,
        top_failed_documents=top_failed_documents,
        limit=limit, offset=offset,
    )


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

async def record_enrichment_event(session: AsyncSession, knowledge_id: str, payload: dict) -> Tuple[bool, bool]:
    """Insert one enrichment event, ignoring a duplicate `event_key`.

    Returns `(recorded, duplicate)`. A retried POST from the worker is a benign
    re-emit, not an error — same idempotency contract as
    chat/db/health_events.py's swallowed IntegrityError on a duplicate chat_id.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    row = dict(payload)
    row["knowledge_id"] = knowledge_id
    stmt = (
        pg_insert(EV.__table__)
        .values(**row)
        .on_conflict_do_nothing(index_elements=["event_key"])
        .returning(EV.__table__.c.id)
    )
    result = await session.execute(stmt)
    inserted = result.scalar_one_or_none()
    await session.commit()
    duplicate = inserted is None
    if duplicate:
        logger.info(f"[enrichment-analytics] duplicate event_key={row.get('event_key')} — ignored")
    return True, duplicate
