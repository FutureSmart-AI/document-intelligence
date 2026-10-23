"""Agent Health / Analytics read endpoints — see
docs/plan/analytics-and-health/endpoints.md (step 4).

Ported from fs-agent-chat's docs/12-agent-health-api.md, re-keyed by
`knowledge_id`. Data source is knowledge_health_events (migration 034),
written by chat/db/health_events.py after every /v1/{knowledge_id}/chat
turn — see that module for what's actually populated on this
single-capability RAG graph vs. the source's multi-service donor.

Auth: `read` RBAC (owner fast-path) on the per-knowledge_id endpoints,
matching every other per-knowledge_id GET in this repo — NOT master-key-only
like the source (see plan.md's confirmed decisions). The cross-tenant grid
endpoint instead uses get_api_key_or_session directly (mirrors
routers/knowledge_management.py's GET /v1 "list mine, or all for master
key" pattern) since there is no single knowledge_id to check ownership of.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from utils.auth.auth_utils import extract_owner_id_from_session
from utils.auth.dependencies import get_api_key_or_session, require_permission
from utils.db.session import async_session
from utils.knowledge_health.analytics import (
    _resolve_window,
    get_failures,
    get_knowledge_bases_grid,
    get_queries,
    get_scorecard,
    get_trends,
)
from utils.logger import logger
from utils.pydantic_models_knowledge_health import (
    FailuresResponse,
    KnowledgeBasesGridResponse,
    DocumentEvalResponse,
    QueriesResponse,
    RagEvalReadResponse,
    ScorecardResponse,
    TrendsResponse,
)

router = APIRouter()

PERIODS = ("week", "month")


def _validate_period(period: str) -> None:
    if period not in PERIODS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"period must be one of {PERIODS}")


def _validate_custom_range(start: Optional[datetime], end: Optional[datetime]) -> None:
    """Custom range is both-or-neither, and end must be after start — mirrors
    fs-agent-chat's `_custom_range` so this contract matches the source."""
    if (start is None) != (end is None):
        raise HTTPException(status_code=422, detail="provide both start_date and end_date, or neither")
    if start is not None and end is not None and end <= start:
        raise HTTPException(status_code=422, detail="end_date must be after start_date")


@router.get("/v1/health/knowledge-bases", response_model=KnowledgeBasesGridResponse, tags=["Agent Health"])
async def api_knowledge_bases_grid(
    period: str = Query("week"),
    owner_id: Optional[str] = Query(None, description="Master-key callers may scope to one owner; session callers are always scoped to their own owner_id."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    auth: dict = Depends(get_api_key_or_session),
) -> KnowledgeBasesGridResponse:
    """Overview grid: one row per knowledge base with headline health numbers."""
    _validate_period(period)
    if auth.get("auth_type") == "session":
        owner_id = extract_owner_id_from_session(auth["session_data"]) or owner_id
    try:
        async with async_session() as session:
            return await get_knowledge_bases_grid(session, owner_id=owner_id, period=period, limit=limit, offset=offset)
    except Exception as e:
        logger.error(f"GET /v1/health/knowledge-bases - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


@router.get("/v1/{knowledge_id}/health/scorecard", response_model=ScorecardResponse, tags=["Agent Health"])
async def api_scorecard(
    knowledge_id: str,
    period: str = Query("week"),
    top_n: int = Query(10, ge=1, le=50),
    start_date: Optional[datetime] = Query(None, description="Custom range start (ISO-8601); with end_date overrides period"),
    end_date: Optional[datetime] = Query(None, description="Custom range end, exclusive (ISO-8601)"),
    _: Optional[str] = Depends(require_permission("read")),
) -> ScorecardResponse:
    """The "everything" call — usage/quality/perf/cost/trend in one response."""
    _validate_period(period)
    _validate_custom_range(start_date, end_date)
    try:
        async with async_session() as session:
            return await get_scorecard(session, knowledge_id, period=period, top_n=top_n, start=start_date, end=end_date)
    except Exception as e:
        logger.error(f"GET /v1/{knowledge_id}/health/scorecard - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


@router.get("/v1/{knowledge_id}/health/failures", response_model=FailuresResponse, tags=["Agent Health"])
async def api_failures(
    knowledge_id: str,
    period: str = Query("week"),
    category: Optional[str] = Query(None),
    failed_stage: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    start_date: Optional[datetime] = Query(None, description="Custom range start (ISO-8601); with end_date overrides period"),
    end_date: Optional[datetime] = Query(None, description="Custom range end, exclusive (ISO-8601)"),
    _: Optional[str] = Depends(require_permission("read")),
) -> FailuresResponse:
    """Top failed queries plus category/stage breakdowns."""
    _validate_period(period)
    _validate_custom_range(start_date, end_date)
    try:
        async with async_session() as session:
            return await get_failures(
                session, knowledge_id, period=period, category=category, failed_stage=failed_stage,
                limit=limit, offset=offset, start=start_date, end=end_date,
            )
    except Exception as e:
        logger.error(f"GET /v1/{knowledge_id}/health/failures - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


@router.get("/v1/{knowledge_id}/health/trends", response_model=TrendsResponse, tags=["Agent Health"])
async def api_trends(
    knowledge_id: str,
    bucket: str = Query("week", description="day | week | month"),
    buckets: int = Query(8, ge=1, le=52),
    metrics: str = Query("success_rate,avg_ms,cost,failures", description="CSV subset of success_rate,avg_ms,p95_ms,cost,failures,total_queries"),
    start_date: Optional[datetime] = Query(None, description="Custom range start (ISO-8601); with end_date overrides the trailing `buckets` window"),
    end_date: Optional[datetime] = Query(None, description="Custom range end, exclusive (ISO-8601)"),
    _: Optional[str] = Depends(require_permission("read")),
) -> TrendsResponse:
    """Bucketed trend series for regression detection."""
    if bucket not in ("day", "week", "month"):
        raise HTTPException(status_code=422, detail="bucket must be one of day, week, month")
    _validate_custom_range(start_date, end_date)
    metrics_list = [m.strip() for m in metrics.split(",") if m.strip()]
    try:
        async with async_session() as session:
            return await get_trends(
                session, knowledge_id, bucket=bucket, buckets=buckets, metrics=metrics_list,
                start=start_date, end=end_date,
            )
    except Exception as e:
        logger.error(f"GET /v1/{knowledge_id}/health/trends - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


@router.get("/v1/{knowledge_id}/health/queries", response_model=QueriesResponse, tags=["Agent Health"])
async def api_queries(
    knowledge_id: str,
    period: str = Query("week"),
    sort: str = Query("latency_ms", description="latency_ms | cost_usd | total_tokens | created_at"),
    order: str = Query("desc"),
    status_filter: Optional[str] = Query(None, alias="status"),
    failure_category: Optional[str] = Query(None),
    feedback: Optional[str] = Query(None, description="positive | negative | none | unrated | any | rated"),
    chat_id: Optional[str] = Query(None, description="Exact lookup; bypasses period."),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    start_date: Optional[datetime] = Query(None, description="Custom range start (ISO-8601); ignored when chat_id is set"),
    end_date: Optional[datetime] = Query(None, description="Custom range end, exclusive (ISO-8601); ignored when chat_id is set"),
    include_node_data: bool = Query(True, description="Node-Trace toggle — false drops the per-node breakdown for a leaner list."),
    _: Optional[str] = Depends(require_permission("read")),
) -> QueriesResponse:
    """Per-query drill-down — slowest/most-expensive/failure investigation."""
    if not chat_id:
        _validate_period(period)
        _validate_custom_range(start_date, end_date)
    try:
        async with async_session() as session:
            return await get_queries(
                session, knowledge_id, period=period, sort=sort, order=order,
                status=status_filter, failure_category=failure_category, feedback=feedback,
                chat_id=chat_id, limit=limit, offset=offset, start=start_date, end=end_date,
                include_node_data=include_node_data,
            )
    except Exception as e:
        logger.error(f"GET /v1/{knowledge_id}/health/queries - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


SYNC_STRATEGIES = ("run_id", "metadata", "both")


@router.post("/v1/{knowledge_id}/health/sync-costs", tags=["Agent Health"])
async def api_sync_costs(
    knowledge_id: str,
    period: str = Query("week"),
    strategy: str = Query(
        "run_id",
        description="How to pull LangSmith cost: run_id (direct read_run per row — "
                    "the default, needs no project config) | metadata (one "
                    "server-side query, needs LANGCHAIN_PROJECT) | both",
    ),
    only_incomplete: bool = Query(
        True,
        description="run_id strategy only: limit work to rows still lacking a complete "
                    "cost. false re-prices every row in the window.",
    ),
    start_date: Optional[datetime] = Query(None, description="Custom range start (ISO-8601); sync the same window you read"),
    end_date: Optional[datetime] = Query(None, description="Custom range end, exclusive (ISO-8601)"),
    _: Optional[str] = Depends(require_permission("write")),
):
    """Refresh this knowledge base's stored chat cost from LangSmith.

    A REPAIR tool, not the main path. Every turn already schedules its own
    finalizer (utils/knowledge_health/cost_finalizer.py, launched from
    routers/chat.py), so cost normally becomes accurate on its own within about
    a minute. This endpoint exists for the windows that missed it — a restart
    mid-backoff, rows written before migration 045 added `langsmith_run_id`,
    or a period when tracing was switched off.

    Sync the SAME window you intend to read: the two strategies are both
    window-scoped, and pricing a week does nothing for a month-long view.

    Strategies differ in what they can reach, which is why `both` exists:
      - `run_id` reads each row's own captured run id. Robust — no
        `LANGCHAIN_PROJECT`, no metadata filter — but blind to rows that never
        captured one.
      - `metadata` finds runs by their `knowledge_id`/`chat_id` metadata in one
        query. Cheap over a wide window and reaches rows with no stored run id,
        but needs `LANGCHAIN_PROJECT` set and needs the run to have been tagged.

    `write` RBAC rather than `read`: this MUTATES stored cost.
    """
    _validate_period(period)
    _validate_custom_range(start_date, end_date)
    if strategy not in SYNC_STRATEGIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"strategy must be one of: {', '.join(SYNC_STRATEGIES)}",
        )
    try:
        from utils.knowledge_health.cost_finalizer import (
            backfill_costs_by_run_id,
            backfill_costs_from_langsmith,
        )
        if strategy == "run_id":
            return await backfill_costs_by_run_id(
                knowledge_id, period=period, start=start_date, end=end_date,
                only_incomplete=only_incomplete,
            )
        if strategy == "metadata":
            return await backfill_costs_from_langsmith(
                knowledge_id, period=period, start=start_date, end=end_date,
            )
        # both — run_id first so the rows that can be priced directly are done
        # before the metadata query, which then mops up whatever had no run id.
        by_run = await backfill_costs_by_run_id(
            knowledge_id, period=period, start=start_date, end=end_date,
            only_incomplete=only_incomplete,
        )
        meta = await backfill_costs_from_langsmith(
            knowledge_id, period=period, start=start_date, end=end_date,
        )
        return {"strategy": "both", "run_id": by_run, "metadata": meta}
    except Exception as e:
        logger.error(f"POST /v1/{knowledge_id}/health/sync-costs - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


@router.get("/v1/{knowledge_id}/health/rag-eval/documents", tags=["Agent Health"],
            response_model=DocumentEvalResponse)
async def api_rag_eval_documents(
    knowledge_id: str,
    period: str = Query("month"),
    start_date: Optional[datetime] = Query(None, description="Custom range start (ISO-8601); with end_date overrides period"),
    end_date: Optional[datetime] = Query(None, description="Custom range end, exclusive (ISO-8601)"),
    include_uncited: bool = Query(True, description="Also list documents in the collection that no answer cited"),
    _: Optional[str] = Depends(require_permission("read")),
):
    """Per-document eval rollup: which documents answers stood on, and how those
    answers scored.

    **A document has no quality of its own.** What this reports is the
    distribution of the answers that cited it, which is why `scored_turns` sits
    beside every mean — 0.55 over one turn and 0.55 over nine are different
    claims. `worst` names the single weakest citing turn so the drill-down is one
    click, not a query.

    **`never_cited` is the number that usually changes a decision.** A document
    ingested, indexed and never once used to answer anything is retrieval noise
    and storage cost with nothing on the other side. It is listed rather than
    counted so the files can be looked at.

    **What this cannot tell you: which documents retrieval CONSIDERED.** Only
    cited evidence is persisted, so a document that is retrieved every time and
    never cited is indistinguishable here from one that is never retrieved at
    all. Tier 0's `retrieved_doc_count` against `cited_doc_count` covers that
    ground in aggregate; per document it is not recoverable.

    Reads only — no judge call, no spend, and not behind `RAG_EVAL_OWNER_IDS`.
    """
    _validate_period(period)
    _validate_custom_range(start_date, end_date)
    since, until, _prev = _resolve_window(period, start_date, end_date)
    try:
        from utils.knowledge_health.rag_eval_runner import document_rollup

        async with async_session() as session:
            data = await document_rollup(session, knowledge_id, since, until,
                                         include_uncited=include_uncited)
        return DocumentEvalResponse(
            knowledge_id=knowledge_id, period=period,
            since=since.isoformat(), until=until.isoformat(), **data,
        )
    except Exception as e:
        logger.error(f"GET /v1/{knowledge_id}/health/rag-eval/documents - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


@router.get("/v1/{knowledge_id}/health/rag-eval", tags=["Agent Health"],
            response_model=RagEvalReadResponse)
async def api_rag_eval_read(
    knowledge_id: str,
    period: str = Query("week"),
    start_date: Optional[datetime] = Query(None, description="Custom range start (ISO-8601); with end_date overrides period"),
    end_date: Optional[datetime] = Query(None, description="Custom range end, exclusive (ISO-8601)"),
    include_turns: bool = Query(True, description="Include the per-turn score list"),
    limit: int = Query(50, ge=1, le=500, description="Cap on per-turn rows returned"),
    _: Optional[str] = Depends(require_permission("read")),
):
    """Read the judge scores already stored for this knowledge base's chat turns.

    **GET reads, POST spends.** This endpoint calls no judge and costs nothing;
    it aggregates what a previous POST wrote to `knowledge_quality_scores`. It
    is therefore **not** behind `RAG_EVAL_OWNER_IDS`: that allowlist governs
    spend, and hiding numbers already paid for from the account that paid for
    them serves nobody.

    **Read `judged_turns` against `answered_turns` before reading any mean.** A
    faithfulness of 0.83 over 3 of 42 turns and the same figure over 42 of 42
    are different claims, and `coverage_pct` is what separates them. An empty
    `dimensions` list means nothing has been judged in this window yet — POST
    to the same path to produce scores.

    **`observations` is per dimension.** Faithfulness is withheld on a turn that
    cited no evidence while relevance and coherence are still scored, so the
    three dimensions of one window legitimately carry three different
    denominators. Per-turn, the withheld ones are listed by name in `withheld`
    rather than left as a key the caller has to notice is missing.
    """
    _validate_period(period)
    _validate_custom_range(start_date, end_date)
    since, until, _prev = _resolve_window(period, start_date, end_date)
    try:
        from utils.knowledge_health.rag_eval_runner import read_scores

        async with async_session() as session:
            data = await read_scores(session, knowledge_id, since, until,
                                     include_turns=include_turns, limit=limit)
        return RagEvalReadResponse(
            knowledge_id=knowledge_id, period=period,
            since=since.isoformat(), until=until.isoformat(), **data,
        )
    except Exception as e:
        logger.error(f"GET /v1/{knowledge_id}/health/rag-eval - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")


@router.post("/v1/{knowledge_id}/health/rag-eval", tags=["Agent Health"])
async def api_rag_eval(
    knowledge_id: str,
    period: str = Query("week"),
    start_date: Optional[datetime] = Query(None, description="Custom range start (ISO-8601); with end_date overrides period"),
    end_date: Optional[datetime] = Query(None, description="Custom range end, exclusive (ISO-8601)"),
    only_unscored: bool = Query(True, description="Skip turns already judged. False re-scores them, e.g. after a rubric change."),
    limit: int = Query(25, ge=1, le=200, description="Cap turns judged by this call"),
    _: Optional[str] = Depends(require_permission("write")),
):
    """Score this knowledge base's chat turns for faithfulness, answer relevance
    and coherence — the reference-free half of RAG evaluation.

    **Gated by owner.** Both `RAG_EVAL_OWNER_IDS` (an allowlist, empty by
    default) and `RAG_EVAL_JUDGE_ENABLED` must permit this knowledge base's
    owner. When they don't, this returns `enabled: false` with a note and spends
    nothing — a clear no-op rather than a 500 or a silent empty success. The
    owner is resolved from `knowledge_config`, never from the caller, because the
    gate decides who is billed for judge calls.

    **No gold answers needed.** These three metrics score the answer against the
    question and the evidence the turn actually cited, so they run on real
    production traffic with no benchmark corpus — which is what makes them
    available now, while a domain corpus does not yet exist
    (docs/plan/quality-baselines/corpora.md).

    **Not a complete picture, by construction.** Context relevance — the RAG
    triad's third leg — is NOT scored: retrieved-but-uncited evidence is not
    stored anywhere, so judging the cited set would ask "was what the agent used
    relevant", which is pre-filtered and high by definition. The Tier-0 columns
    from migration 048 (`retrieved_chunk_count` vs `cited_chunk_count`) cover
    that ground structurally instead.

    **Faithfulness is withheld, not zeroed, on a turn that cited no evidence.**
    An answer with nothing to check against is not a hallucination; scoring it
    0.0 would read as one. A withheld dimension writes no score row at all.

    `write` RBAC and POST rather than GET: this spends money per turn and INSERTs
    score rows. `limit` is capped so a wide window cannot spend without bound —
    the response reports `remaining` so a partial pass is never mistaken for a
    complete one.

    Scores land in `knowledge_quality_scores` (migration 049), one row per
    dimension, upserted on (subject, name, source) so a re-run replaces an
    opinion rather than appending a second one that averages would double-count.
    """
    _validate_period(period)
    _validate_custom_range(start_date, end_date)
    since, until, _prev = _resolve_window(period, start_date, end_date)
    try:
        from utils.knowledge_health.rag_eval_runner import judge_window

        async with async_session() as session:
            return await judge_window(
                session, knowledge_id, since, until,
                only_unscored=only_unscored, limit=limit,
            )
    except Exception as e:
        logger.error(f"POST /v1/{knowledge_id}/health/rag-eval - unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")
