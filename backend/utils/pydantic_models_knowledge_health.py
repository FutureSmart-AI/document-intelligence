"""Response models for the Agent Health / Analytics read endpoints
(routers/knowledge_health.py) — see docs/plan/analytics-and-health/endpoints.md.

Shape follows fs-agent-chat's docs/12-agent-health-api.md contract, re-keyed
by `knowledge_id` instead of `agent_id` (see that plan's field-mapping
decision). Health-score / red-amber-green band intentionally NOT
reintroduced — removed by design upstream (14-agent-health-dashboard-apis.md),
carried forward here per decisions.md.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class UsageBlock(BaseModel):
    total_queries: int
    unique_users: int
    widget_queries: int
    bucket: str = "day"
    per_day: List[Dict[str, Any]] = Field(default_factory=list)


class QualityBlock(BaseModel):
    answered: int
    no_answer: int
    error: int
    success_rate: float
    feedback: Dict[str, int] = Field(default_factory=lambda: {"positive": 0, "negative": 0, "total": 0})


class SlowQuery(BaseModel):
    chat_id: str
    query: str
    latency_ms: int
    total_tokens: int
    trace_id: Optional[str] = None


class NodeStat(BaseModel):
    """One node's (node_name, model) aggregate over the window — same object
    shape on both perf.by_node and cost.by_node (they read from one query),
    matching the NL2SQL analytics contract this repo's scorecard tracks
    parity against."""
    node_name: Optional[str]
    provider: Optional[str]
    model: Optional[str]
    queries: int
    total_calls: int
    errors: int
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    avg_latency_s: float
    p50_latency_s: float
    p95_latency_s: float
    total_tokens: int
    input_tokens: int
    output_tokens: int
    total_cost: Optional[float]


class PerfBlock(BaseModel):
    avg_ms: float
    p50_ms: float
    p95_ms: float
    by_node: List[NodeStat] = Field(default_factory=list)
    slowest: List[SlowQuery] = Field(default_factory=list)


class ByModelCost(BaseModel):
    provider: Optional[str]
    model: Optional[str]
    queries: int
    cost: float


class ExpensiveQuery(BaseModel):
    chat_id: str
    query: str
    total_tokens: int
    cost_usd: Optional[float]
    trace_id: Optional[str] = None


class CostBlock(BaseModel):
    total_cost: float
    avg_cost_per_query: float
    total_tokens: int
    input_tokens: int
    output_tokens: int
    cost_complete: bool
    by_model: List[ByModelCost] = Field(default_factory=list)
    by_node: List[NodeStat] = Field(default_factory=list)
    most_expensive: List[ExpensiveQuery] = Field(default_factory=list)


class TrendBlock(BaseModel):
    prev_success_rate: Optional[float]
    success_rate_delta: Optional[float]
    prev_avg_ms: Optional[float]
    avg_ms_delta: Optional[float]
    prev_total_cost: Optional[float]
    cost_delta: Optional[float]
    direction: str


class GroundingBlock(BaseModel):
    """RAG eval Tier 0 aggregated over the window — the deterministic tier
    (migration 048), read back for the first time.

    EVERY FIELD IS NULL-AWARE. A turn whose Tier 0 columns are NULL was not
    measured (its owner is outside `RAG_EVAL_OWNER_IDS`, or it predates the
    migration) and is excluded from the denominators here, never counted as a
    zero. `measured_turns` against `total_turns` is what makes that visible
    instead of silently shrinking the sample.
    """
    total_turns: int
    measured_turns: int
    coverage_pct: float

    # Retrieved vs cited, pooled over the window. Both the mean of per-turn
    # ratios AND the raw totals: the mean answers "how well does a typical turn
    # use its evidence", the totals answer "how much evidence went unused
    # overall", and on a window with a few huge retrievals they disagree.
    avg_context_utilization: Optional[float] = None
    retrieved_chunks: int = 0
    cited_chunks: int = 0
    retrieved_docs: int = 0
    cited_docs: int = 0

    # Turns that needed more than one retrieval pass — the verifier sent them
    # back. A rising share is a retrieval-quality regression that no latency or
    # cost metric shows.
    multi_attempt_turns: int = 0
    multi_attempt_pct: Optional[float] = None
    max_attempts: Optional[int] = None

    # The verifier's own verdict, counted. `not_run` is a real value, not a
    # gap: the verifier is skipped on the final attempt by design.
    verifier_verdicts: Dict[str, int] = Field(default_factory=dict)

    # Cross-turn evidence reuse, either side of the relevance filter.
    evidence_groups_loaded: int = 0
    evidence_groups_kept: int = 0
    evidence_kept_pct: Optional[float] = None


class ScorecardResponse(BaseModel):
    knowledge_id: str
    period: str
    since: str
    until: str
    previous_since: str
    usage: UsageBlock
    quality: QualityBlock
    perf: PerfBlock
    cost: CostBlock
    trend: TrendBlock
    # Tier 0 (migration 048). Present on every scorecard; `measured_turns: 0`
    # is the honest answer for a knowledge base whose owner is not gated on,
    # and is different from the block being absent.
    grounding: Optional[GroundingBlock] = None


class KnowledgeBaseGridRow(BaseModel):
    knowledge_id: str
    owner_id: Optional[str]
    total_queries: int
    unique_users: int
    answered: int
    unanswered: int
    success_rate: float
    total_cost: float
    avg_cost_per_query: float
    total_tokens: int
    avg_ms: float
    p95_ms: float
    cost_complete: bool
    top_failure_category: Optional[str]


class GridSummary(BaseModel):
    total_queries: int
    total_cost: float
    cost_complete: bool


class KnowledgeBasesGridResponse(BaseModel):
    period: str
    since: str
    until: str
    total_knowledge_bases: int
    limit: int
    offset: int
    summary: GridSummary
    knowledge_bases: List[KnowledgeBaseGridRow]


class FailureCategoryCount(BaseModel):
    failure_category: Optional[str]
    count: int
    pct: float


class FailedStageCount(BaseModel):
    failed_stage: Optional[str]
    count: int


class TopFailedQuery(BaseModel):
    query_norm: str
    failure_category: Optional[str]
    failed_stage: Optional[str]
    occurrences: int
    last_seen: str
    first_seen: str
    latest_chat_id: str
    latest_trace_id: Optional[str]
    sample_failure_detail: Optional[str]


class FailuresResponse(BaseModel):
    knowledge_id: str
    period: str
    since: str
    until: str
    total_failures: int
    filters: Dict[str, Optional[str]] = Field(default_factory=dict)
    by_category: List[FailureCategoryCount] = Field(default_factory=list)
    by_stage: List[FailedStageCount] = Field(default_factory=list)
    limit: int
    offset: int
    returned: int
    top_failed_queries: List[TopFailedQuery] = Field(default_factory=list)


class TrendPoint(BaseModel):
    bucket: str
    success_rate: Optional[float] = None
    avg_ms: Optional[float] = None
    p95_ms: Optional[float] = None
    cost: Optional[float] = None
    failures: Optional[int] = None
    total_queries: Optional[int] = None


class TrendsResponse(BaseModel):
    knowledge_id: str
    bucket: str
    buckets: int
    since: str
    until: str
    metrics: List[str]
    series: List[TrendPoint]
    regression: Optional[Dict[str, Any]] = None
    comparison: Optional[Dict[str, Any]] = None


class QueryRow(BaseModel):
    chat_id: str
    session_id: Optional[str]
    user_id: Optional[str]
    created_at: str
    query: str
    refined_query: str
    route: Optional[str] = None
    services_used: List[str] = Field(default_factory=list)
    status: str
    outcome: str
    is_answered: bool
    confidence: Optional[str] = None
    stop_reason: Optional[str] = None
    failure_category: Optional[str]
    failed_stage: Optional[str]
    failure_detail: Optional[str] = None
    feedback: Optional[str] = None
    provider: str
    model: str
    from_widget: bool
    key_type: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    retry_count: int = 0
    cost_usd: Optional[float]
    cost_complete: bool
    cost_breakdown: List[Dict[str, Any]] = Field(default_factory=list)
    node_data: List[Dict[str, Any]] = Field(default_factory=list)
    trace_id: Optional[str]
    # The LangSmith run id for this turn (migration 045) — NOT `trace_id`,
    # which holds an OpenTelemetry/Tempo span id and resolves to nothing in
    # LangSmith. Both are returned because they address different systems and
    # confusing them is the documented trap (docs/api/agent-health.md:72).
    # It is also the id the Tier 1 judge attaches its scores to as LangSmith
    # feedback, so one id opens both the chat turn and its eval.
    langsmith_run_id: Optional[str] = None
    # RAG eval Tier 0, per turn (migration 048). NULL means NOT RECORDED —
    # `retrieved_chunk_count: 0` is a real event (retrieval found nothing),
    # `null` is "this turn was never measured". Any consumer that coerces one
    # to the other destroys the distinction the columns exist to keep.
    retrieved_chunk_count: Optional[int] = None
    retrieved_doc_count: Optional[int] = None
    cited_chunk_count: Optional[int] = None
    cited_doc_count: Optional[int] = None
    retrieval_attempts: Optional[int] = None
    verifier_verdict: Optional[str] = None
    verifier_missing: Optional[str] = None
    evidence_groups_loaded: Optional[int] = None
    evidence_groups_kept: Optional[int] = None
    # cited / retrieved, derived here rather than stored — None, not 0.0, when
    # nothing was retrieved.
    context_utilization: Optional[float] = None


class QueriesResponse(BaseModel):
    knowledge_id: str
    period: str
    since: str
    until: str
    sort: str
    order: str
    total_matching: int
    limit: int
    offset: int
    returned: int
    queries: List[QueryRow]


class ScoreDimension(BaseModel):
    """One judged dimension aggregated over the window.

    `observations` is per dimension, not per window, because faithfulness is
    WITHHELD on a turn that cited no evidence while the other two are still
    scored. Three dimensions from the same window legitimately have three
    different denominators, and a single shared `n` would misreport all of them.
    """
    name: str
    observations: int
    mean: float
    median: float
    min: float
    max: float
    # Ordered bands, not a histogram of arbitrary buckets: 1.0 is "every claim
    # supported", and under 0.7 is the band worth reading turns from.
    perfect: int
    partial: int
    weak: int


class ScoredTurn(BaseModel):
    chat_id: str
    created_at: Optional[str] = None
    query: Optional[str] = None
    scores: Dict[str, float] = Field(default_factory=dict)
    grader_model: Optional[str] = None
    comment: Optional[str] = None
    # Absent dimensions are absent on purpose — a withheld score is no row.
    withheld: List[str] = Field(default_factory=list)
    # The chat turn's LangSmith run. The judge attaches its scores to that run
    # as feedback rather than opening a trace of its own, so this one id opens
    # both the turn and its eval — and without it a stored score cannot be
    # traced back to the run that produced it. Not `trace_id` (OTel span id).
    langsmith_run_id: Optional[str] = None


class RagEvalReadResponse(BaseModel):
    """The read half of `/health/rag-eval`. GET aggregates what POST produced.

    `judged_turns` against `answered_turns` is the coverage of the window: a
    mean over 3 of 42 turns and a mean over 42 of 42 are different claims, and
    only this pair tells them apart.
    """
    knowledge_id: str
    period: str
    since: str
    until: str
    answered_turns: int
    judged_turns: int
    coverage_pct: float
    dimensions: List[ScoreDimension] = Field(default_factory=list)
    grader_models: Dict[str, int] = Field(default_factory=dict)
    turns: List[ScoredTurn] = Field(default_factory=list)


class DocumentEvalRow(BaseModel):
    """One document's eval record over a window.

    The scores are the answers', not the document's — a document has no
    faithfulness of its own. `mean_faithfulness` is the mean over the judged
    turns that cited it, which is why `scored_turns` sits beside it: a 0.55 over
    one turn and a 0.55 over nine are different claims about the same document.
    """
    object_id: str
    file_name: Optional[str] = None
    processing_mode: Optional[str] = None
    cited_turns: int
    judged_turns: int
    scored_turns: int
    cited_chunks: int
    mean_faithfulness: Optional[float] = None
    min_faithfulness: Optional[float] = None
    weak_turns: int = 0
    last_cited: Optional[str] = None
    worst: Optional[Dict[str, Any]] = None
    known_document: bool = True


class UncitedDocument(BaseModel):
    object_id: str
    file_name: Optional[str] = None
    processing_mode: Optional[str] = None
    created_at: Optional[str] = None


class DocumentEvalResponse(BaseModel):
    """Per-document rollup for one knowledge base and window.

    `documents_never_cited` is usually the number that changes a decision: a
    document ingested and never used to answer anything is retrieval noise and
    storage cost with nothing on the other side of the ledger.
    """
    knowledge_id: str
    period: str
    since: str
    until: str
    documents_in_collection: int
    documents_cited: int
    documents_never_cited: int
    turns_in_window: int
    documents: List[DocumentEvalRow] = Field(default_factory=list)
    never_cited: List[UncitedDocument] = Field(default_factory=list)

