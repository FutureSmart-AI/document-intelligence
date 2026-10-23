"""Usage event retrieval + analytics aggregation, ported from fs-agent-usage-limits'
utils/usage_event_retrieval.py and utils/usage_analytics_query.py, merged into one
module and rewritten to scope by knowledge_id instead of owner_id.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from collections import defaultdict

from sqlalchemy import select, func, and_, case
from sqlalchemy.ext.asyncio import AsyncSession

from utils.models.usage_events import UsageEvent, UsageMetric
from utils.pydantic_models_usage_events import (
    UsageEventResponse,
    EventMetricResponse,
    UsageEventsListResponse,
    AggregatedMetric,
    AggregatedUsageResponse,
    UsageSummaryResponse,
    ServiceOperationSummary,
)
from utils.pydantic_models_usage_analytics import (
    AnalyticsQueryRequest,
    AnalyticsQueryResponse,
    AggregatedMetrics,
    KnowledgeOverviewMetrics,
    AnalyticsDataPoint,
    DateRange,
    ALLOWED_METADATA_FIELDS,
)
from utils.usage.cost_calculator import CostCalculator, merge_pricing_periods, build_pricing_periods_list
from utils.pydantic_models_usage_analytics import CostBreakdown, PricingPeriodCost
from utils.logger import logger

NULLABLE_COLUMN_DEFAULTS = {"label": "unlabeled", "knowledge_id": "unattributed"}
TIME_BUCKETS = ["hour", "day", "week", "month"]
# This list is an allowlist, not a description: a metric the workers emit but that is
# missing here lands in usage_metrics and then never appears in any aggregate. Adding a
# metric upstream means adding it here too.
#
# llama_parse_credits sits alongside llama_parse_page_count rather than replacing it,
# because they answer different questions. Pages is how much was processed; credits is
# what it cost — and since a page costs between 1 and 10 credits depending on the
# processing mode, neither number can be derived from the other.
# file_count, text_count, document_vector_count and chunk_vector_count are added here
# deliberately without char_count. char_count is emitted under FOUR different
# (operation, label) pairs (file/weblink/text ingestion and both embedding steps) — the
# exact "metric_name alone is not a unique key" trap this module's own docs/modules
# catalog warns about (see utils/usage/limit_checking.py's indexed_characters comment,
# which hit the same trap and fixed it by filtering on operation). Summing char_count
# in a *groupable* aggregate would let the UI slice a triple-counted number by model —
# worse than not showing it. The other four are each emitted by exactly one
# (operation, label) pair, so summing them here carries no such risk.
ALL_METRICS = [
    "total_tokens", "input_tokens", "output_tokens", "audio_seconds",
    "llama_parse_page_count", "llama_parse_credits", "processed_weblink_count",
    "file_count", "text_count", "document_vector_count", "chunk_vector_count",
]


# ---------------------------------------------------------------------------
# Event retrieval (GET /events, /aggregate, /summary)
# ---------------------------------------------------------------------------

async def get_usage_events(
    session: AsyncSession,
    owner_id: str,
    key_type: Optional[str] = None,
    operation: Optional[str] = None,
    knowledge_id: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
) -> UsageEventsListResponse:
    filters = [UsageEvent.owner_id == owner_id]
    if knowledge_id:
        filters.append(UsageEvent.knowledge_id == knowledge_id)
    if key_type:
        filters.append(UsageEvent.key_type == key_type)
    if operation:
        filters.append(UsageEvent.operation == operation)
    if start_date:
        filters.append(UsageEvent.timestamp >= start_date)
    if end_date:
        filters.append(UsageEvent.timestamp <= end_date)

    count_result = await session.execute(select(func.count()).select_from(UsageEvent).where(and_(*filters)))
    total_count = count_result.scalar() or 0

    query = select(UsageEvent).where(and_(*filters)).order_by(UsageEvent.timestamp.desc()).limit(limit).offset(offset)
    result = await session.execute(query)
    events_db = result.scalars().all()

    event_ids = [event.event_id for event in events_db]
    metrics_by_event: Dict[Any, List[EventMetricResponse]] = {}
    if event_ids:
        metrics_result = await session.execute(select(UsageMetric).where(UsageMetric.event_id.in_(event_ids)))
        for metric in metrics_result.scalars().all():
            metrics_by_event.setdefault(metric.event_id, []).append(
                EventMetricResponse(name=metric.metric_name, value=float(metric.metric_value))
            )

    events_response = [
        UsageEventResponse(
            event_id=str(event.event_id),
            knowledge_id=event.knowledge_id,
            owner_id=event.owner_id,
            key_type=event.key_type,
            service_type=event.service_type,
            operation=event.operation,
            timestamp=event.timestamp.isoformat(),
            label=event.label,
            session_id=event.session_id,
            request_id=event.request_id,
            operation_id=event.operation_id,
            metadata=event.event_metadata,
            metrics=metrics_by_event.get(event.event_id, []),
            created_at=event.created_at.isoformat(),
        )
        for event in events_db
    ]

    return UsageEventsListResponse(total_count=total_count, events=events_response, limit=limit, offset=offset)


async def get_aggregated_usage(
    session: AsyncSession,
    owner_id: str,
    operation: Optional[str] = None,
    label: Optional[str] = None,
    knowledge_id: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    group_by: Optional[List[str]] = None,
) -> AggregatedUsageResponse:
    group_by = group_by or ["service_type", "operation"]

    filters = [UsageEvent.owner_id == owner_id]
    if knowledge_id:
        filters.append(UsageEvent.knowledge_id == knowledge_id)
    if operation:
        filters.append(UsageEvent.operation == operation)
    if label:
        filters.append(UsageEvent.label == label)
    if start_date:
        filters.append(UsageEvent.timestamp >= start_date)
    if end_date:
        filters.append(UsageEvent.timestamp <= end_date)

    group_columns = [getattr(UsageEvent, field) for field in group_by if hasattr(UsageEvent, field)]
    if not group_columns:
        raise ValueError("Invalid group_by fields")

    query = select(*group_columns, func.count(UsageEvent.event_id).label("event_count")).where(and_(*filters)).group_by(*group_columns)
    result = await session.execute(query)
    groups_data = result.all()

    groups = []
    for row in groups_data:
        group_dict: Dict[str, Any] = {field: row[i] for i, field in enumerate(group_by)}
        group_dict["event_count"] = row[-1]

        metric_filters = filters + [getattr(UsageEvent, field) == row[i] for i, field in enumerate(group_by)]
        metrics_query = (
            select(
                UsageMetric.metric_name,
                func.sum(UsageMetric.metric_value).label("total"),
                func.avg(UsageMetric.metric_value).label("avg"),
                func.min(UsageMetric.metric_value).label("min"),
                func.max(UsageMetric.metric_value).label("max"),
                func.count(UsageMetric.metric_id).label("count"),
            )
            .join(UsageEvent, UsageMetric.event_id == UsageEvent.event_id)
            .where(and_(*metric_filters))
            .group_by(UsageMetric.metric_name)
        )
        metrics_result = await session.execute(metrics_query)
        group_dict["metrics"] = [
            AggregatedMetric(metric_name=m[0], total=float(m[1]), avg=float(m[2]), min=float(m[3]), max=float(m[4]), count=m[5]).dict()
            for m in metrics_result.all()
        ]
        groups.append(group_dict)

    return AggregatedUsageResponse(group_by=group_by, groups=groups)


async def get_usage_summary(
    session: AsyncSession, owner_id: str, knowledge_id: Optional[str] = None,
    start_date: Optional[datetime] = None, end_date: Optional[datetime] = None
) -> UsageSummaryResponse:
    filters = [UsageEvent.owner_id == owner_id]
    if knowledge_id:
        filters.append(UsageEvent.knowledge_id == knowledge_id)
    if start_date:
        filters.append(UsageEvent.timestamp >= start_date)
    if end_date:
        filters.append(UsageEvent.timestamp <= end_date)

    total_result = await session.execute(select(func.count()).select_from(UsageEvent).where(and_(*filters)))
    total_events = total_result.scalar() or 0

    query = (
        select(UsageEvent.service_type, UsageEvent.operation, func.count(UsageEvent.event_id).label("event_count"))
        .where(and_(*filters))
        .group_by(UsageEvent.service_type, UsageEvent.operation)
    )
    result = await session.execute(query)

    services = []
    for row in result.all():
        service_type, operation, event_count = row[0], row[1], row[2]
        metric_filters = filters + [UsageEvent.service_type == service_type, UsageEvent.operation == operation]
        metrics_query = (
            select(
                UsageMetric.metric_name,
                func.sum(UsageMetric.metric_value).label("total"),
                func.avg(UsageMetric.metric_value).label("avg"),
                func.min(UsageMetric.metric_value).label("min"),
                func.max(UsageMetric.metric_value).label("max"),
                func.count(UsageMetric.metric_id).label("count"),
            )
            .join(UsageEvent, UsageMetric.event_id == UsageEvent.event_id)
            .where(and_(*metric_filters))
            .group_by(UsageMetric.metric_name)
        )
        metrics_result = await session.execute(metrics_query)
        metrics = [
            AggregatedMetric(metric_name=m[0], total=float(m[1]), avg=float(m[2]), min=float(m[3]), max=float(m[4]), count=m[5])
            for m in metrics_result.all()
        ]
        services.append(ServiceOperationSummary(service_type=service_type, operation=operation, event_count=event_count, metrics=metrics))

    return UsageSummaryResponse(
        owner_id=owner_id,
        start_date=start_date.isoformat() if start_date else "",
        end_date=end_date.isoformat() if end_date else "",
        total_events=total_events,
        services=services,
    )


# ---------------------------------------------------------------------------
# Analytics query (POST /analytics/query)
# ---------------------------------------------------------------------------

def _build_filter_conditions(owner_id: str, filters, start_date: datetime, end_date: datetime, key_filter=None) -> List:
    conditions = [UsageEvent.owner_id == owner_id, UsageEvent.timestamp >= start_date, UsageEvent.timestamp <= end_date]
    if key_filter is not None:
        conditions.append(UsageEvent.key_type == key_filter.value)
    if filters is None:
        return conditions
    if filters.key_type and key_filter is None:
        conditions.append(UsageEvent.key_type == filters.key_type)
    if filters.service_types:
        conditions.append(UsageEvent.service_type.in_(filters.service_types))
    if filters.operations:
        conditions.append(UsageEvent.operation.in_(filters.operations))
    if filters.labels:
        conditions.append(UsageEvent.label.in_(filters.labels))
    if filters.knowledge_id:
        conditions.append(UsageEvent.knowledge_id == filters.knowledge_id)
    if filters.metadata:
        for key, values in filters.metadata.items():
            if key in ALLOWED_METADATA_FIELDS:
                value_list = values if isinstance(values, list) else [values]
                conditions.append(UsageEvent.event_metadata[key].astext.in_(value_list))
    return conditions


def _build_group_columns(group_by: List[str], time_bucket: Optional[str] = None):
    columns, labels = [], []
    if time_bucket and time_bucket in TIME_BUCKETS:
        columns.append(func.date_trunc(time_bucket, UsageEvent.timestamp).label("time_bucket"))
        labels.append("time_bucket")
    for field in group_by:
        if field.startswith("metadata."):
            meta_field = field.split(".", 1)[1]
            if meta_field in ALLOWED_METADATA_FIELDS:
                if meta_field == "model":
                    col = func.coalesce(
                        func.nullif(UsageEvent.event_metadata["model"].astext, ""),
                        func.nullif(UsageEvent.event_metadata["model_name"].astext, ""),
                        "unknown",
                    ).label(field)
                else:
                    col = func.coalesce(func.nullif(UsageEvent.event_metadata[meta_field].astext, ""), "unknown").label(field)
                columns.append(col)
                labels.append(field)
        elif hasattr(UsageEvent, field):
            if field in NULLABLE_COLUMN_DEFAULTS:
                col = func.coalesce(getattr(UsageEvent, field), NULLABLE_COLUMN_DEFAULTS[field]).label(field)
            else:
                col = getattr(UsageEvent, field)
            columns.append(col)
            labels.append(field)
    return columns, labels


def _build_metrics_subquery(filter_conditions: List):
    input_tokens_expr = func.sum(case((UsageMetric.metric_name == "input_tokens", UsageMetric.metric_value), else_=0))
    output_tokens_expr = func.sum(case((UsageMetric.metric_name == "output_tokens", UsageMetric.metric_value), else_=0))
    total_tokens_direct_expr = func.sum(case((UsageMetric.metric_name == "total_tokens", UsageMetric.metric_value), else_=None))
    return (
        select(
            UsageMetric.event_id,
            input_tokens_expr.label("input_tokens"),
            output_tokens_expr.label("output_tokens"),
            func.coalesce(total_tokens_direct_expr, input_tokens_expr + output_tokens_expr, 0).label("total_tokens"),
            func.sum(case((UsageMetric.metric_name == "audio_seconds", UsageMetric.metric_value), else_=0)).label("audio_seconds"),
            func.sum(case((UsageMetric.metric_name == "llama_parse_page_count", UsageMetric.metric_value), else_=0)).label("llama_parse_page_count"),
            func.sum(case((UsageMetric.metric_name == "processed_page_count", UsageMetric.metric_value), else_=0)).label("processed_page_count"),
            # Each of these four is emitted by exactly one (operation, label) pair — see
            # the ALL_METRICS comment above for why that makes an unfiltered sum safe.
            func.sum(case((UsageMetric.metric_name == "file_count", UsageMetric.metric_value), else_=0)).label("file_count"),
            func.sum(case((UsageMetric.metric_name == "text_count", UsageMetric.metric_value), else_=0)).label("text_count"),
            func.sum(case((UsageMetric.metric_name == "document_vector_count", UsageMetric.metric_value), else_=0)).label("document_vector_count"),
            func.sum(case((UsageMetric.metric_name == "chunk_vector_count", UsageMetric.metric_value), else_=0)).label("chunk_vector_count"),
        )
        .select_from(UsageMetric)
        .join(UsageEvent, UsageEvent.event_id == UsageMetric.event_id)
        .where(and_(*filter_conditions))
        .group_by(UsageMetric.event_id)
        .subquery("metrics_agg")
    )


def _build_knowledge_overview_subquery(filter_conditions: List):
    return (
        select(
            UsageMetric.event_id,
            func.sum(case((UsageMetric.metric_name == "char_count", UsageMetric.metric_value), else_=0)).label("char_count"),
            func.sum(case((UsageMetric.metric_name == "audio_seconds", UsageMetric.metric_value), else_=0)).label("audio_seconds"),
            func.sum(case((UsageMetric.metric_name == "llama_parse_page_count", UsageMetric.metric_value), else_=0)).label("llama_parse_page_count"),
            func.sum(case((UsageMetric.metric_name == "processed_page_count", UsageMetric.metric_value), else_=0)).label("processed_page_count"),
            func.sum(case((UsageMetric.metric_name == "document_vector_count", UsageMetric.metric_value), else_=0)).label("document_vector_count"),
            func.sum(case((UsageMetric.metric_name == "chunk_vector_count", UsageMetric.metric_value), else_=0)).label("chunk_vector_count"),
            func.sum(case((UsageMetric.metric_name == "file_count", UsageMetric.metric_value), else_=0)).label("file_count"),
            func.sum(case((UsageMetric.metric_name == "text_count", UsageMetric.metric_value), else_=0)).label("text_count"),
        )
        .select_from(UsageMetric)
        .join(UsageEvent, UsageEvent.event_id == UsageMetric.event_id)
        .where(and_(*filter_conditions))
        .group_by(UsageMetric.event_id)
        .subquery("knowledge_metrics_agg")
    )


async def _calculate_total_costs(session: AsyncSession, filter_conditions: List) -> CostBreakdown:
    metrics_sub = _build_metrics_subquery(filter_conditions)
    events_query = (
        select(
            UsageEvent.event_id, UsageEvent.operation, UsageEvent.event_metadata, UsageEvent.timestamp,
            metrics_sub.c.input_tokens, metrics_sub.c.output_tokens, metrics_sub.c.total_tokens,
            metrics_sub.c.audio_seconds, metrics_sub.c.llama_parse_page_count, metrics_sub.c.processed_page_count,
        )
        .select_from(UsageEvent)
        .outerjoin(metrics_sub, UsageEvent.event_id == metrics_sub.c.event_id)
        .where(and_(*filter_conditions))
    )
    rows = (await session.execute(events_query)).all()

    calculator = CostCalculator(session)
    aggregated = {
        "total_cost_usd": 0.0, "llm_cost_usd": 0.0, "llm_input_tokens_cost_usd": 0.0, "llm_output_tokens_cost_usd": 0.0,
        "embedding_cost_usd": 0.0, "document_processing_cost_usd": 0.0, "transcription_cost_usd": 0.0,
    }
    all_period_costs: Dict[str, Any] = {}

    for row in rows:
        event = UsageEvent(event_id=row.event_id, operation=row.operation, event_metadata=row.event_metadata, timestamp=row.timestamp, service_type="")
        metrics = {
            "input_tokens": float(row.input_tokens or 0), "output_tokens": float(row.output_tokens or 0),
            "total_tokens": float(row.total_tokens or 0), "audio_seconds": float(row.audio_seconds or 0),
            "llama_parse_page_count": float(row.llama_parse_page_count or 0), "processed_page_count": float(row.processed_page_count or 0),
        }
        event_cost = await calculator.calculate_event_cost(event, metrics)
        merge_pricing_periods(all_period_costs, event_cost)
        for key, value in event_cost.items():
            if value > 0:
                aggregated[key] = aggregated.get(key, 0.0) + value

    for key in aggregated:
        aggregated[key] = round(aggregated[key], 6)

    periods_list = build_pricing_periods_list(all_period_costs)
    pricing_periods = [PricingPeriodCost(**p) for p in periods_list] if periods_list else None
    return CostBreakdown(**aggregated, pricing_periods=pricing_periods)


def _parse_range_start(value: str) -> datetime:
    """Parse a date_range bound as an inclusive lower bound, in UTC."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_range_end(value: str) -> datetime:
    """Parse a date_range upper bound so that a **date** covers its whole day.

    ``datetime.fromisoformat("2026-08-20")`` is midnight, and the query filters
    ``timestamp <= end``, so a caller asking for ``end: "2026-08-20"`` used to get
    nothing that happened *on* the 20th — only the instant of midnight. Confirmed
    live 2026-08-20: an owner with 19 events (1 on the 19th, 18 on the 20th) saw
    ``request_count: 1`` for ``end: "2026-08-20"`` and 19 for ``"2026-08-21"``.
    Silently losing the most recent day of data is the worst possible failure for
    an analytics endpoint, because the number looks plausible.

    So: a date-only bound is pushed to the end of that day. A bound that names a
    time is respected exactly — someone who writes a timestamp means it.
    """
    raw = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # "2026-08-20" (no time component) -> 2026-08-20T23:59:59.999999Z
    if "T" not in raw and " " not in raw.strip():
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt


async def execute_analytics_query(session: AsyncSession, owner_id: str, request: AnalyticsQueryRequest) -> AnalyticsQueryResponse:
    """Execute the analytics query for one owner (across all their knowledge bases):
    filters, group_by, time bucketing, and per-group cost breakdown."""
    logger.info(f"Analytics query for owner_id={owner_id}, group_by={request.group_by}, time_bucket={request.time_bucket}")

    start_dt = _parse_range_start(request.date_range.start)
    end_dt = _parse_range_end(request.date_range.end)

    filter_conditions = _build_filter_conditions(owner_id, request.filters, start_dt, end_dt, request.key_filter)
    metrics_sub = _build_metrics_subquery(filter_conditions)
    metrics_to_include = request.metrics_to_include or ALL_METRICS

    totals_query = (
        select(
            func.count(UsageEvent.event_id).label("request_count"),
            func.coalesce(func.sum(metrics_sub.c.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(metrics_sub.c.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(metrics_sub.c.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(metrics_sub.c.audio_seconds), 0).label("audio_seconds"),
            func.coalesce(func.sum(metrics_sub.c.llama_parse_page_count), 0).label("llama_parse_page_count"),
            func.coalesce(func.sum(metrics_sub.c.processed_page_count), 0).label("processed_page_count"),
            func.coalesce(func.sum(metrics_sub.c.file_count), 0).label("file_count"),
            func.coalesce(func.sum(metrics_sub.c.text_count), 0).label("text_count"),
            func.coalesce(func.sum(metrics_sub.c.document_vector_count), 0).label("document_vector_count"),
            func.coalesce(func.sum(metrics_sub.c.chunk_vector_count), 0).label("chunk_vector_count"),
        )
        .select_from(UsageEvent)
        .outerjoin(metrics_sub, UsageEvent.event_id == metrics_sub.c.event_id)
        .where(and_(*filter_conditions))
    )
    totals_row = (await session.execute(totals_query)).one()

    group_columns, group_labels = _build_group_columns(request.group_by, request.time_bucket.value if request.time_bucket else None)

    if not group_columns:
        total_costs = await _calculate_total_costs(session, filter_conditions)
        totals_dict: Dict[str, Any] = {"request_count": totals_row.request_count or 0}
        for m in ("total_tokens", "input_tokens", "output_tokens", "audio_seconds", "llama_parse_page_count"):
            if m in metrics_to_include:
                totals_dict[m] = float(getattr(totals_row, m) or 0)
        if "processed_weblink_count" in metrics_to_include:
            totals_dict["processed_weblink_count"] = float(totals_row.processed_page_count or 0)
        totals_dict["metadata"] = {}
        totals_dict["cost"] = total_costs
        totals = AggregatedMetrics(**totals_dict)

        knowledge_sub = _build_knowledge_overview_subquery(filter_conditions)
        overview_query = (
            select(
                func.coalesce(func.sum(knowledge_sub.c.file_count), 0).label("total_files_processed"),
                func.coalesce(func.sum(knowledge_sub.c.char_count), 0).label("total_char_count"),
                func.coalesce(func.sum(knowledge_sub.c.audio_seconds), 0).label("total_audio_seconds"),
                func.coalesce(func.sum(knowledge_sub.c.llama_parse_page_count), 0).label("total_llama_parse_pages"),
                func.coalesce(func.sum(knowledge_sub.c.processed_page_count), 0).label("total_weblink_pages"),
                func.coalesce(func.sum(knowledge_sub.c.text_count), 0).label("total_text_count"),
                func.coalesce(func.sum(knowledge_sub.c.document_vector_count) + func.sum(knowledge_sub.c.chunk_vector_count), 0).label("total_vectors_indexed"),
            )
            .select_from(UsageEvent)
            .outerjoin(knowledge_sub, UsageEvent.event_id == knowledge_sub.c.event_id)
            .where(and_(*filter_conditions))
        )
        overview_row = (await session.execute(overview_query)).one()

        embedding_query = (
            select(func.coalesce(func.sum(metrics_sub.c.total_tokens), 0).label("total_embedding_tokens"))
            .select_from(UsageEvent)
            .outerjoin(metrics_sub, UsageEvent.event_id == metrics_sub.c.event_id)
            .where(and_(*filter_conditions, UsageEvent.operation == "embedding_generation"))
        )
        embedding_row = (await session.execute(embedding_query)).one()

        overview = KnowledgeOverviewMetrics(
            total_files_processed=int(overview_row.total_files_processed or 0),
            total_char_count=int(overview_row.total_char_count or 0),
            total_audio_seconds=float(overview_row.total_audio_seconds or 0),
            total_llama_parse_pages=int(overview_row.total_llama_parse_pages or 0),
            total_weblink_pages=int(overview_row.total_weblink_pages or 0),
            total_text_count=int(overview_row.total_text_count or 0),
            total_embedding_tokens=float(embedding_row.total_embedding_tokens or 0),
            total_vectors_indexed=int(overview_row.total_vectors_indexed or 0),
            cost=total_costs,
        )

        return AnalyticsQueryResponse(
            owner_id=owner_id, executed_at=datetime.now(timezone.utc).isoformat(), date_range=request.date_range,
            group_by=request.group_by, time_bucket=request.time_bucket.value if request.time_bucket else None,
            totals=totals, knowledge_overview_metrics=overview, data=[], total_groups=0,
            limit=request.limit, offset=request.offset, has_more=False,
        )

    count_query = select(func.count().over().label("total_count")).select_from(
        select(*group_columns).select_from(UsageEvent).where(and_(*filter_conditions)).group_by(*group_columns).subquery()
    )
    count_row = (await session.execute(count_query)).first()
    total_groups = count_row.total_count if count_row else 0

    grouped_query = (
        select(
            *group_columns,
            func.count(UsageEvent.event_id).label("request_count"),
            func.coalesce(func.sum(metrics_sub.c.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(metrics_sub.c.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(metrics_sub.c.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(metrics_sub.c.audio_seconds), 0).label("audio_seconds"),
            func.coalesce(func.sum(metrics_sub.c.llama_parse_page_count), 0).label("llama_parse_page_count"),
            func.coalesce(func.sum(metrics_sub.c.processed_page_count), 0).label("processed_page_count"),
            func.coalesce(func.sum(metrics_sub.c.file_count), 0).label("file_count"),
            func.coalesce(func.sum(metrics_sub.c.text_count), 0).label("text_count"),
            func.coalesce(func.sum(metrics_sub.c.document_vector_count), 0).label("document_vector_count"),
            func.coalesce(func.sum(metrics_sub.c.chunk_vector_count), 0).label("chunk_vector_count"),
        )
        .select_from(UsageEvent)
        .outerjoin(metrics_sub, UsageEvent.event_id == metrics_sub.c.event_id)
        .where(and_(*filter_conditions))
        .group_by(*group_columns)
        .order_by(func.count(UsageEvent.event_id).desc())
        .limit(request.limit)
        .offset(request.offset)
    )
    rows = (await session.execute(grouped_query)).all()

    # Batch per-group cost calc (one query for all events instead of N+1).
    batch_query = (
        select(
            *group_columns, UsageEvent.event_id, UsageEvent.operation, UsageEvent.event_metadata, UsageEvent.timestamp,
            metrics_sub.c.input_tokens, metrics_sub.c.output_tokens, metrics_sub.c.total_tokens,
            metrics_sub.c.audio_seconds, metrics_sub.c.llama_parse_page_count, metrics_sub.c.processed_page_count,
        )
        .select_from(UsageEvent)
        .outerjoin(metrics_sub, UsageEvent.event_id == metrics_sub.c.event_id)
        .where(and_(*filter_conditions))
    )
    batch_rows = (await session.execute(batch_query)).all()

    group_costs_map: Dict[tuple, Dict[str, float]] = defaultdict(lambda: {"total_cost_usd": 0.0})
    calculator = CostCalculator(session)
    num_dims = len(group_labels)
    for batch_row in batch_rows:
        group_key = tuple(str(batch_row[i]) for i in range(num_dims))
        offset = num_dims
        event = UsageEvent(
            event_id=batch_row[offset], operation=batch_row[offset + 1], event_metadata=batch_row[offset + 2], timestamp=batch_row[offset + 3], service_type="",
        )
        event_metrics = {
            "input_tokens": float(batch_row[offset + 4] or 0), "output_tokens": float(batch_row[offset + 5] or 0),
            "total_tokens": float(batch_row[offset + 6] or 0), "audio_seconds": float(batch_row[offset + 7] or 0),
            "llama_parse_page_count": float(batch_row[offset + 8] or 0), "processed_page_count": float(batch_row[offset + 9] or 0),
        }
        event_cost = await calculator.calculate_event_cost(event, event_metrics)
        event_cost.pop("_pricing_periods", None)
        costs = group_costs_map[group_key]
        for key, value in event_cost.items():
            if value > 0:
                costs[key] = costs.get(key, 0.0) + value

    total_costs = await _calculate_total_costs(session, filter_conditions)
    totals_dict: Dict[str, Any] = {"request_count": totals_row.request_count or 0}
    for m in ("total_tokens", "input_tokens", "output_tokens", "audio_seconds", "llama_parse_page_count",
              "file_count", "text_count", "document_vector_count", "chunk_vector_count"):
        if m in metrics_to_include:
            totals_dict[m] = float(getattr(totals_row, m) or 0)
    if "processed_weblink_count" in metrics_to_include:
        totals_dict["processed_weblink_count"] = float(totals_row.processed_page_count or 0)
    totals_dict["metadata"] = {}
    totals_dict["cost"] = total_costs
    totals = AggregatedMetrics(**totals_dict)

    data_points = []
    for row in rows:
        dimensions = {}
        for i, label in enumerate(group_labels):
            value = row[i]
            dimensions[label] = value.isoformat() if isinstance(value, datetime) else value
        group_key = tuple(str(row[i]) for i in range(len(group_labels)))

        group_costs_dict = {k: round(v, 6) for k, v in group_costs_map.get(group_key, {"total_cost_usd": 0.0}).items()}
        group_costs = CostBreakdown(**group_costs_dict)

        base_offset = len(group_labels)
        metrics_dict: Dict[str, Any] = {
            "request_count": row[base_offset], "metadata": {}, "cost": group_costs,
        }
        field_map = {
            "total_tokens": base_offset + 1, "input_tokens": base_offset + 2, "output_tokens": base_offset + 3,
            "audio_seconds": base_offset + 4, "llama_parse_page_count": base_offset + 5,
            "file_count": base_offset + 7, "text_count": base_offset + 8,
            "document_vector_count": base_offset + 9, "chunk_vector_count": base_offset + 10,
        }
        for name, idx in field_map.items():
            if name in metrics_to_include:
                metrics_dict[name] = float(row[idx] or 0)
        if "processed_weblink_count" in metrics_to_include:
            metrics_dict["processed_weblink_count"] = float(row[base_offset + 6] or 0)

        data_points.append(AnalyticsDataPoint(dimensions=dimensions, metrics=AggregatedMetrics(**metrics_dict)))

    has_more = (request.offset + len(rows)) < total_groups
    return AnalyticsQueryResponse(
        owner_id=owner_id, executed_at=datetime.now(timezone.utc).isoformat(), date_range=request.date_range,
        group_by=request.group_by, time_bucket=request.time_bucket.value if request.time_bucket else None,
        totals=totals, knowledge_overview_metrics=None, data=data_points, total_groups=total_groups,
        limit=request.limit, offset=request.offset, has_more=has_more,
    )
