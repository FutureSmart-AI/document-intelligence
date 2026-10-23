"""SQLModel ORM table definitions for all database tables."""

import uuid
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON, DateTime, String, UniqueConstraint, Index, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID


# class Users(SQLModel, table=True):
#     __tablename__ = "users"
#     __table_args__ = {'extend_existing': True}
#     user_id: str = Field(primary_key=True)
#     email_id: str = Field(index=True)
#     user_name: Optional[str] = None
#     user_pic: Optional[str] = None
#     first_logged_in: Optional[datetime] = None
#     last_accessed: Optional[datetime] = None
#     service_count: Optional[int] = Field(default=0)
#     tier: Optional[str] = Field(default="User")
#     total_requests: Optional[int] = Field(default=0)


class Document(SQLModel, table=True):
    __tablename__ = "document"
    __table_args__ = {'extend_existing': True}
    id: Optional[int] = Field(default=None, primary_key=True)
    # Actor who created the doc; on delete, overwritten with the deleting actor
    # (session email or "master_api_key") since deleted rows are excluded from every
    # user_id-filtered query, so the original creator is no longer needed off this row.
    user_id: str = Field(index=True)
    knowledge_id: str = Field(index=True)
    object_id: str = Field(index=True)
    file_name: Optional[str] = None
    content: Optional[str] = None
    llama_parse: bool = Field(default=False)
    # The DocumentProcessingMode the request resolved to ("text_based", "scanned",
    # "layout_aware", "visual", "mixed"). Supersedes llama_parse, which can only say
    # plain-vs-fancy; both are written so a worker reading the old boolean still works.
    # This is the mode *requested* — the worker may auto-shift a mode it finds wrong
    # (e.g. an image-only PDF shifts to OCR) and that shift is not reported back here.
    document_processing_mode: Optional[str] = Field(default=None, max_length=32)
    url: Optional[str] = None
    deleted: bool = Field(default=False)
    created_at: Optional[datetime] = Field(default_factory=datetime.now)
    # Equals created_at on insert; bumped on every reindex, and on delete (see
    # mark_document_processing / delete_chatbot_knowledge)
    updated_at: Optional[datetime] = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = Field(default=None)
    contextualise_chunks: Optional[bool] = Field(default=False)
    chunk_size: Optional[int] = None
    processing_status: Optional[str] = Field(default='processing')
    content_type: Optional[str] = None
    source_knowledge_id: Optional[str] = None  # deprecated
    source_object_id: Optional[str] = None
    source_url: Optional[str] = None
    source_presigned_url: Optional[str] = None
    source_presigned_url_expires_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    deletion_status: Optional[str] = Field()
    batch_id: Optional[str] = Field(default=None, index=True)
    error_detail: Optional[str] = Field(default=None)
    task_duration_ms: Optional[int] = Field(default=None)
    extra_metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True)
    )


class Batch(SQLModel, table=True):
    __tablename__ = "batches"
    batch_id: str = Field(primary_key=True, description="Unique batch identifier")
    knowledge_id: str = Field(index=True, description="Knowledge service identifier")
    user_id: Optional[str] = Field(default=None, index=True, description="User identifier")
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow, index=True, description="Batch creation timestamp")
    status: Optional[str] = Field(default="processing", index=True, description="Batch processing status")
    total_documents: Optional[int] = Field(default=0, description="Total number of documents in the batch")


class KnowledgeConfig(SQLModel, table=True):
    __tablename__ = "knowledge_config"
    __table_args__ = {'extend_existing': True}
    knowledge_id: str = Field(primary_key=True)
    # Service fields (set by /knowledge/generate)
    owner_id: Optional[str] = Field(index=True)
    # User-facing collection name (G1). Distinct from service_description —
    # a short label for Home cards / Create Collection, not the longer
    # free-text description. Optional: a collection created before this
    # column existed, or via the master API key with no name supplied,
    # simply has none.
    collection_name: Optional[str] = None
    service_description: Optional[str] = None
    # Free-text intent behind an LLM-generated enrichment schema (migration 051)
    # — e.g. "filter by vendor, contract value, renewal date". Distinct from
    # service_description the same way collection_name is: see
    # utils/config/field_generation.py.
    extraction_instructions: Optional[str] = None
    # Configuration fields (set by /set-knowledge-config)
    embedding_model: Optional[str] = None
    hybrid_search_enabled: Optional[bool] = None
    document_level_metadata: Optional[bool] = None
    chunk_level_metadata: Optional[bool] = None
    # Enrichment fields
    enrichment_provider: Optional[str] = None
    enrichment_model: Optional[str] = None
    enrichment_temperature: Optional[float] = None
    enrichment_prompt: Optional[str] = None
    # See utils/config/knowledge_defaults.USE_DETERMINISTIC_CHUNKING — kept in sync with it.
    use_deterministic_chunking: Optional[bool] = Field(default=True)
    # False = index our own extra_metadata (filterable) WITHOUT the LLM enrichment call.
    enrichment_enabled: Optional[bool] = Field(default=True)
    # Common fields
    created_at: Optional[datetime] = Field(default_factory=datetime.now)
    updated_at: Optional[datetime] = Field(default_factory=datetime.now)
    deleted: bool = Field(default=False)
    deleted_at: Optional[datetime] = None


class KnowledgeQualityScore(SQLModel, table=True):
    """One evaluation score for one subject (migration 049).

    Generic by design: a new metric is a config row plus an insert, not another
    ALTER TABLE. See docs/plan/quality-baselines/storage.md for why this exists
    alongside the typed columns on KnowledgeHealthEvent rather than replacing
    them — hot fields every aggregation scans stay columns; sampled and
    judge-produced scores live here.

    A WITHHELD SCORE IS AN ABSENT ROW. There is no such thing as a row with a
    NULL numeric value: the DB CHECK constraint rejects it. That is the point of
    the shape — "NULL means no opinion, 0.0 means everything checked was wrong"
    stops being a comment above a column and becomes impossible to violate.

    `source` separates a judge's score from a human annotation for the same
    metric on the same subject; both may coexist, and disagreement between them
    is a signal worth keeping rather than a conflict to resolve.
    """
    __tablename__ = "knowledge_quality_scores"
    __table_args__ = {'extend_existing': True}

    id: Optional[int] = Field(default=None, primary_key=True)
    knowledge_id: str = Field(index=True)
    # 'chat_turn' | 'enrichment' | 'benchmark_run'. subject_id is TEXT because
    # those three are a uuid, a bigint and a label respectively — one text column
    # keeps all three addressable without three nullable foreign keys.
    subject_type: str
    subject_id: str
    name: str
    value: Optional[float] = None
    string_value: Optional[str] = None
    data_type: str = Field(default="NUMERIC")
    # 'judge' | 'deterministic' | 'human' | 'api' — who produced this score.
    source: str
    # The GRADER's model, its own column rather than metadata so "was this model
    # asked to grade its own output" is answerable in a GROUP BY.
    grader_model: Optional[str] = None
    comment: Optional[str] = None
    # The denominator. A score without one is unreadable — `1.000 over 5
    # observations` and `0.964 over 56` are not comparable claims, and a comment
    # string cannot be aggregated.
    observations: Optional[int] = None
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class KnowledgeQualityScoreConfig(SQLModel, table=True):
    """The definition of one metric, enforced by the database (migration 049).

    This is the metrics charter as a table: name, type, allowed range, and what
    the metric means. It exists because a metric whose definition drifts silently
    between two measurements is worse than no metric — the comparison looks valid
    and isn't.

    Archived rather than deleted (`is_archived`), so historical scores keep a
    definition to be read against after a metric is retired.
    """
    __tablename__ = "knowledge_quality_score_configs"
    __table_args__ = {'extend_existing': True}

    name: str = Field(primary_key=True)
    data_type: str = Field(default="NUMERIC")
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    categories: Optional[List[str]] = Field(default=None, sa_column=Column(JSONB, nullable=True))
    description: str
    is_archived: bool = Field(default=False)
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class EnrichmentDefaultConfig(SQLModel, table=True):
    __tablename__ = "enrichment_default_config"
    __table_args__ = {'extend_existing': True}
    id: Optional[int] = Field(default=None, primary_key=True)  # SERIAL — assigned by DB on insert
    enrichment_model: str
    enrichment_provider: str
    enrichment_temperature: float = Field(default=0.0)
    enrichment_fallback_model: str
    enrichment_fallback_provider: str
    updated_at: Optional[datetime] = Field(default_factory=datetime.now)


class UserDefinedFieldManifest(SQLModel, table=True):
    """Persisted registry of user-defined (passthrough) metadata field names per
    knowledge_id. Source of truth for which extra_metadata fields are assigned to a
    knowledge base, so the enrichment endpoint can return them deterministically instead
    of sampling a handful of documents. Decoupled from field_configurations — the Celery
    enrichment worker never reads or writes this table, so it has zero effect on LLM
    extraction. field_type is frozen at first-seen; rows are soft-retired via is_active,
    never hard-deleted.
    """
    __tablename__ = "user_defined_field_manifest"
    __table_args__ = (
        UniqueConstraint("knowledge_id", "field_name", name="uq_udfm_knowledge_field"),
        Index("ix_udfm_knowledge_id", "knowledge_id"),
        {"extend_existing": True},
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    knowledge_id: str = Field(index=True)
    field_name: str
    field_type: str = Field(default="str")
    sample_value: Optional[Any] = Field(default=None, sa_column=Column(JSONB, nullable=True))
    is_active: bool = Field(default=True)
    first_seen_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    last_seen_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)


class ConnectorSyncConfig(SQLModel, table=True):
    """Per-knowledge, per-source configuration for self-serve external indexing
    (ClickUp first; source-agnostic so Notion etc. reuse the same scaffolding).

    Stores the connection (encrypted token + workspace), the user's selection
    (spaces/folders + space-level docs toggle), deletion behaviour, and the
    recurring schedule. The knowledge-side Celery beat scans enabled rows whose
    next_sync_at is due and enqueues a sync run, advancing next_sync_at.
    """
    __tablename__ = "connector_sync_config"
    __table_args__ = (
        UniqueConstraint("knowledge_id", "source", name="uq_connector_knowledge_source"),
        Index("ix_connector_sync_config_due", "enabled", "next_sync_at"),
        {"extend_existing": True},
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    knowledge_id: str = Field(index=True)
    source: str = Field(default="clickup")  # 'clickup' | future 'notion' ...
    enabled: bool = Field(default=False)
    # Fernet ciphertext of the source API token — never returned by any endpoint.
    encrypted_token: Optional[str] = None
    # Source-specific selection blob — each connector reads/writes its own shape, so a
    # new source needs no schema change. For ClickUp:
    #   {"workspace_id", "space_ids", "folder_ids", "folder_names",
    #    "exclude_folder_name", "exclude_folder_id", "exclude_name_keywords",
    #    "space_docs_enabled", "space_doc_allowlist", "deletion_enabled",
    #    "force_reindex"}  # one-shot, set by a manual trigger and cleared by the run

    selection: Optional[Any] = Field(default=None, sa_column=Column(JSONB, nullable=True))
    # Recurring schedule. Either a simple interval (e.g. 360 = every 6h) OR a cron
    # expression (e.g. "0 2 * * *" = daily 02:00 UTC). If both set, cron wins.
    # None/None = manual only.
    interval_minutes: Optional[int] = Field(default=None)
    schedule_cron: Optional[str] = Field(default=None)
    last_sync_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    next_sync_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)


class KnowledgeAuditLog(SQLModel, table=True):
    """One standardized row per dashboard-initiated request, written by the
    `audit_access` dependency (see utils/auth/dependencies.py). Mirrors the audit
    pattern documented in fs-agent-actions/docs/runbook-audit-logging.md.

    Answers "who did what, when, to which knowledge base." Every router is audited,
    including the high-volume `search` endpoint — coverage was chosen over noise
    (the table is append-only and indexed, so retrieval rows are cheap).
    """
    __tablename__ = "knowledge_audit_log"
    __table_args__ = {'extend_existing': True}

    id: Optional[int] = Field(default=None, primary_key=True)
    # Authenticated actor: the session email, "master_api_key" for master-key
    # calls, or NULL when the request is unauthenticated.
    user_id: Optional[str] = Field(default=None, index=True)
    # Resource owner the request concerns (distinct from the actor above).
    owner_id: Optional[str] = Field(default=None, index=True)
    # The knowledge base the request targets (resolved from path/query/body).
    # This is the same id space the usage service records as `service_id`.
    knowledge_id: Optional[str] = Field(default=None, index=True)
    # The operation = route handler name, e.g. "get_knowledge_configuration_v2".
    # The HTTP verb lives in `method` and is not folded in here.
    action: str
    method: Optional[str] = None
    path: Optional[str] = None
    # TIMESTAMPTZ to match the rest of this DB. The column type MUST be
    # timezone-aware (timezone=True) — otherwise SQLAlchemy binds it as
    # "TIMESTAMP WITHOUT TIME ZONE" and asyncpg rejects the tz-aware value below.
    # server_default=now() also covers any insert that omits created_at.
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )


class ApiKey(SQLModel, table=True):
    """Per-user/per-knowledge-base API key, ported from fs-agent-dashboard's
    api_key_management/ (see docs/plan/access-and-sharing/archive/design-api-keys.md).

    knowledge_id, not chatbot_id — this service has no chatbot/agent concept
    of its own (that's fs-agent-chat's ApiKeys table, a separate DB, scoped
    the other way).

    Hardened vs. upstream: the raw key is never stored — only its hash
    (key_hash, looked up by the auth dependency) and a short prefix
    (key_prefix, for display in a list UI). The raw value is returned to the
    caller exactly once, at generation time, and cannot be recovered after.
    """
    __tablename__ = "api_keys"
    __table_args__ = {'extend_existing': True}

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    knowledge_id: Optional[str] = None
    key_prefix: str
    key_hash: str = Field(unique=True, index=True)
    is_active: bool = Field(default=True)
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )
    revoked_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class ConnectorSyncRun(SQLModel, table=True):
    """One row per sync run, for UI progress polling and an audit trail.

    Stats are folded from the crawl's per-page outcomes (indexed/skipped/errors)
    and the reconcile sweep (deleted). status moves queued -> running ->
    completed | failed.
    """
    __tablename__ = "connector_sync_run"
    __table_args__ = (
        Index("ix_connector_sync_run_knowledge", "knowledge_id", "source"),
        {"extend_existing": True},
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    knowledge_id: str = Field(index=True)
    source: str = Field(default="clickup")
    status: str = Field(default="queued")   # queued | running | completed | failed
    trigger: str = Field(default="manual")  # manual | scheduled
    indexed: int = Field(default=0)
    skipped: int = Field(default=0)
    errors: int = Field(default=0)
    deleted: int = Field(default=0)
    error_detail: Optional[str] = Field(default=None)
    started_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    completed_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)


# class ChatbotDetails(SQLModel, table=True):
#     __tablename__ = "chatbot_details"
#     __table_args__ = {'extend_existing': True}
#     user_id: str = Field()
#     chatbot_id: str = Field(primary_key=True)
#     chatbot_name: str
#     system_prompt: str
#     llm_provider: str = Field(default='openai')
#     model: str = Field(default='gpt-4.1-mini')
#     temperature: float = Field(default=0.7)
#     collect_user_info: bool = Field(default=True)
#     show_sources: bool = Field(default=True)
#     follow_up: bool = Field(default=False)
#     restrict_to_knowledge_base: bool = Field(default=True)
#     hybrid_search: bool = Field(default=True)
#     embedding_model: str = Field(default=None)
#     document_level_metadata: bool = Field(default=True)
#     chunk_level_metadata: bool = Field(default=False)
#     created_at: Optional[datetime] = Field(default_factory=datetime.now)
#     deleted: bool = Field(default=False)
#     deleted_at: Optional[datetime] = None


# class ExtractedMetadata(SQLModel, table=True):
#     __tablename__ = "extracted_metadata"
#     __table_args__ = {'extend_existing': True}
#     id: Optional[int] = Field(default=None, primary_key=True)
#     object_id: Optional[str] = Field(default=None, index=True)
#     field_configuration_id: int = Field(foreign_key="field_configurations.id")
#     knowledge_id: str = Field(index=True)
#     extracted_content: Optional[Any] = Field(default=None, sa_column=Column(JSON))
#     extraction_timestamp: Optional[datetime] = Field(default_factory=datetime.utcnow)


class KnowledgeChatSessions(SQLModel, table=True):
    """One row per chat session on the Knowledge Service's own chat endpoint
    (see claude-logs/plan/knowledge-owned-chat.md). Shaped after
    fs-agent-chat's ChatSessions minus the agent/widget-specific columns
    (agent_id, chatbot_id, from_chat_widget, display_message, changed_fields,
    updated_by, deleted_at/deleted_by audit pair) — this service has exactly
    one "agent" (itself, scoped by knowledge_id) and no widget embedding.
    """
    __tablename__ = "knowledge_chat_sessions"
    __table_args__ = {'extend_existing': True}

    session_id: str = Field(primary_key=True)
    knowledge_id: str = Field(index=True)
    user_id: Optional[str] = None
    session_title: Optional[str] = None
    deleted: bool = Field(default=False)
    created_at: Optional[datetime] = Field(default_factory=datetime.now)
    # Stamped on every persisted turn (chat/db/chat_logs.py::insert_chat_log ->
    # touch_session), so "last activity" means the newest turn, not row creation.
    # NULL only for a session row that has no turn yet.
    updated_at: Optional[datetime] = None

    # Origin + caller identity (migration 044), for Share -> Activity. `source` is
    # the caller surface only — "widget" | "dashboard" | "api"; owner-vs-invited is
    # derived at read time from user_email vs knowledge_config.owner_id, since
    # ownership can change after the conversation happened. user_email/user_name are
    # captured from the caller's session at write time because this service does not
    # own the `users` table (get_user_id_from_email is stubbed to return None), so
    # there is nothing to resolve an account uuid against later.
    source: Optional[str] = None
    user_email: Optional[str] = None
    user_name: Optional[str] = None


class KnowledgeChatLogs(SQLModel, table=True):
    """One row per chat turn on the Knowledge Service's own chat endpoint.
    Shaped after fs-agent-chat's ChatLogs minus Orchestrator/widget/billing
    columns (chatbot_id, agent_id, context, gpt_response_time,
    tokens_consumed, debug_info, services_used, chart_output, attachment,
    end_of_conversation, stream, use_user_info, from_chat_widget,
    parent_session_id) — those belong to composition, billing tiers, and
    widget embedding, none of which this service owns. `feedback` (migration
    027) is the one exception brought back: per-turn 👍/👎, this service's own
    concern once the Conversations screen renders an answer (G13).

    chat_id (migration 025) is a server-generated UUID distinct from the int
    `id` PK — added back because the response envelope needs a per-turn id
    matching the Orchestrator's ChatResponse.chat_id (used for turn-level
    addressing, e.g. regenerate_response), while `id` stays the DB's own PK.

    service_data carries the grouped retrieval evidence for the turn
    (build_grouped_evidence-shaped) — read back next turn by
    chat/db/chat_logs.py::get_recent_evidence as
    service_data["knowledge_evidence"] directly (one level shallower than the
    Orchestrator's service_data["knowledge"]["knowledge_evidence"], since this
    table is knowledge-only, not multi-service). It also carries
    relevant_sources/summaries/detailed_chunks verbatim (G14) — the same
    fields the live response returns, not a relabeled stand-in.
    """
    __tablename__ = "knowledge_chat_logs"
    __table_args__ = {'extend_existing': True}

    id: Optional[int] = Field(default=None, primary_key=True)
    chat_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(PG_UUID(as_uuid=True), nullable=False, unique=True),
    )
    session_id: str = Field(index=True)
    knowledge_id: str = Field(index=True)
    user_id: Optional[str] = None
    query: Optional[str] = None
    response: Optional[str] = None
    service_data: Optional[Any] = Field(default=None, sa_column=Column(JSONB, nullable=True))
    refined_query: Optional[str] = None
    llm_provider: Optional[str] = None
    model: Optional[str] = None
    no_of_chunks: Optional[int] = Field(default=4)
    has_attachment: bool = Field(default=False)
    attachment_url: Optional[str] = None
    is_answered: Optional[bool] = None
    feedback: Optional[str] = Field(default=None, sa_column=Column(String(10), nullable=True))
    created_at: Optional[datetime] = Field(default_factory=datetime.now)


class KnowledgeHealthEvent(SQLModel, table=True):
    """One row per chat turn's analytics/outcome facts (migration 034).

    Ported from fs-agent-chat's agent_health_events (see
    docs/plan/analytics-and-health/events.md), re-keyed by `knowledge_id`
    instead of `agent_id`. Deliberately separate from KnowledgeChatLogs even
    though both are one-row-per-turn and share chat_id/session_id/
    knowledge_id/user_id — this table never stores response text or
    retrieval evidence, so aggregation queries (scorecard/trends, which may
    scan a full month) don't pay for scanning those columns, and analytics
    writes stay independent of the chat-log write path.

    cost_usd/cost_complete/cost_breakdown replace the source's flat
    cost_usd per the confirmed multi-model-cost fix: a turn can fan out
    across several independently-configured models, so cost is priced per
    (provider, model) subtotal in cost_breakdown and summed; cost_complete
    is False whenever any subtotal's model has no known price (that line's
    cost stays null rather than being silently counted as zero).

    key_type is one of this service's own auth modes (see
    docs/modules/auth-and-rbac.md): master_key | user_api_key | session |
    public — the source's owner-key/platform-key pair doesn't apply here.

    chat_id is UNIQUE — the dedupe key for a retried emit.
    """
    __tablename__ = "knowledge_health_events"
    __table_args__ = {'extend_existing': True}

    id: Optional[int] = Field(default=None, primary_key=True)
    knowledge_id: str = Field(index=True)
    owner_id: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    chat_id: uuid.UUID = Field(
        sa_column=Column(PG_UUID(as_uuid=True), nullable=False, unique=True),
    )
    trace_id: Optional[str] = None
    # LangSmith run id of this turn's root `KnowledgeChat` run (migration 045).
    # DISTINCT from trace_id above, which is an OpenTelemetry/Tempo span id and
    # is NOT resolvable against LangSmith — conflating the two is the trap the
    # donor service calls out in agents/run.py::_current_langsmith_run_id. This
    # is the join key the cost finalizer/backfill reads the turn's real priced
    # cost back by (chat/langsmith_cost.py). NULL when LangSmith tracing was
    # inactive for the turn, or for rows written before migration 045.
    langsmith_run_id: Optional[str] = None

    query: str
    refined_query: str = Field(default="")
    route: Optional[str] = None
    services_used: List[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False, server_default="[]"))

    status: str
    is_answered: bool
    failure_category: Optional[str] = None
    failed_stage: Optional[str] = None
    failure_detail: Optional[str] = None
    confidence: Optional[str] = None
    stop_reason: Optional[str] = None

    provider: str = Field(default="openai")
    model: str
    from_widget: bool = Field(default=False)
    key_type: str

    latency_ms: int
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    retry_count: int = Field(default=0)

    cost_usd: Optional[float] = None
    cost_complete: bool = Field(default=True)
    cost_breakdown: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False, server_default="[]"))

    # ── RAG eval Tier 0 (migration 048) ──────────────────────────────────
    # Structural signals the chat pipeline already computed and then discarded
    # (docs/plan/quality-baselines/coverage.md). Free: no LLM call, no added
    # latency. Written only for owners in RAG_EVAL_OWNER_IDS.
    #
    # NULL means NOT RECORDED — a pre-048 row, an owner outside the allowlist,
    # or the error path where no retrieval ran. It never means zero: a turn that
    # retrieved 0 chunks is a real and interesting outcome, and rendering NULL
    # as 0 would invent a fleet of no-retrieval turns.
    retrieved_chunk_count: Optional[int] = None
    retrieved_doc_count: Optional[int] = None
    # What the agent reported it actually used to ground the answer
    # (RagAgentOutput.relevant_docs). cited/retrieved is the context-utilization
    # ratio, computed at READ time — storing a ratio beside its own numerator
    # and denominator invites the three drifting apart.
    cited_chunk_count: Optional[int] = None
    cited_doc_count: Optional[int] = None
    # Retrieval loop passes. 1 on the common path; >1 means the CRAG verifier
    # sent the agent back to re-plan.
    retrieval_attempts: Optional[int] = None
    # The verifier's own verdict, previously used for a branch then dropped.
    # THREE states, not two: 'sufficient' | 'insufficient' | 'not_run'. The
    # verifier is deliberately skipped on the final attempt (its only job is
    # deciding whether to retry, already decided by then), so a single-attempt
    # turn legitimately has no verdict — distinct from NULL, which means not
    # recorded at all.
    verifier_verdict: Optional[str] = None
    verifier_missing: Optional[str] = None
    # Cross-turn evidence reuse: how much recency-loaded prior evidence survived
    # the relevance gate (chat/evidence_relevance.py). Both NULL when the turn
    # loaded no prior evidence, which differs from loaded=3 / kept=0.
    evidence_groups_loaded: Optional[int] = None
    evidence_groups_kept: Optional[int] = None

    # Per-node Agent Health breakdown (migration 036): one entry per node
    # that ran this turn — {node_name, provider, model, calls, errors,
    # latency_ms, input_tokens, output_tokens, total_tokens, cost_usd}.
    # Built at emit time from the graph's already-tagged service_calls (see
    # chat/rag_agent/agent_execution.py, chat/chart_node.py) — see
    # chat/db/health_events.py::_build_node_data.
    node_data: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False, server_default="[]"))

    event_type: str = Field(default="knowledge_query")
    schema_version: int = Field(default=1)
    # Explicit timezone=True: the DB column is TIMESTAMPTZ (migration 034).
    # Without this, SQLModel infers a naive TIMESTAMP bind type, and
    # comparing it against a tz-aware datetime (utils/knowledge_health/
    # analytics.py's since/until windows) raises asyncpg.DataError.
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class KnowledgeChatConfig(SQLModel, table=True):
    """Per-knowledge_id chat settings for the Knowledge Service's own chat
    endpoint (see claude-logs/plan/knowledge-owned-chat.md's Config section).

    Level 2 of the 4-level provider/model/temperature/system_prompt
    resolution chain (request body field > this row > env default >
    hardcoded default) — see chat/config.py::resolve_chat_config. Lets the
    Knowledge Service configure its own chat behavior independently of the
    Orchestrator's Agent Registry, for direct/standalone callers with no
    per-agent config of their own to pass through.

    Absence of a row for a knowledge_id is normal (falls through to level 3/4),
    not an error — most knowledge bases will never need to override the
    defaults.
    """
    __tablename__ = "knowledge_chat_config"
    __table_args__ = {'extend_existing': True}

    knowledge_id: str = Field(primary_key=True)
    system_prompt: Optional[str] = None
    llm_provider: Optional[str] = Field(default="openai")
    llm_model: Optional[str] = Field(default="gpt-5.4-mini")
    temperature: Optional[float] = Field(default=0.0)
    show_sources: bool = Field(default=True)
    chat_enabled: bool = Field(default=True)
    follow_up: bool = Field(default=True)
    # G10. suggested_questions is editorial content the collection owner
    # writes directly (starter prompts shown on an empty conversation) — no
    # generation step involved, so storing and returning it is a complete
    # feature on its own. voice_input_enabled/voice_output_enabled are pure
    # client-facing preferences: this service has no speech-to-text or
    # text-to-speech pipeline anywhere, so these two gate NOTHING server-side
    # — they only tell the dashboard whether to render mic/speaker controls,
    # same posture as a theme toggle. Document that distinction wherever
    # these are surfaced, so a "voice on" toggle is never read as "this
    # service now understands audio".
    suggested_questions: List[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False, server_default="[]"))
    voice_input_enabled: bool = Field(default=False)
    voice_output_enabled: bool = Field(default=False)
    # max_tokens: unlike temperature above, this IS threaded into the answer
    # node's get_llm call (chat/rag_agent/answer_node.py) — see migration 030.
    # include_charts: behavioral instruction only (chat/rag_node.py appends a
    # "no tables/charts" directive when False); no structured chart_output
    # field exists on ChatResponse — that stays the open gap G16.
    # show_raw_response: gates debug_info out of the client response the same
    # way show_sources gates the evidence fields (routers/chat.py).
    max_tokens: Optional[int] = Field(default=1024)
    include_charts: bool = Field(default=True)
    show_raw_response: bool = Field(default=True)
    # Public-access gating for the two embed surfaces (migration 032), ported
    # from fs-agent-chat's agent_params. Fully independent pairs — neither
    # surface's toggle or allowlist inherits the other's. Empty/null allowlist
    # means "any origin" once the surface's toggle is on. See
    # docs/plan/access-and-sharing/archive/design-widget-fullscreen.md (Piece 2) and
    # utils/auth/public_access.py, which enforces these.
    is_public: bool = Field(default=False)
    allowed_domains: Optional[List[str]] = Field(default=None, sa_column=Column(JSONB, nullable=True))
    fullscreen_is_public: bool = Field(default=False)
    fullscreen_allowed_domains: Optional[List[str]] = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: Optional[datetime] = Field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None


class KnowledgeWidgetSettings(SQLModel, table=True):
    """Widget branding settings for one knowledge_id — heading/greeting/color/
    icon/avatar shown by the embeddable chat widget.

    Ported from fs-agent-dashboard's ``widgets_settings`` table (keyed there by
    ``chatbot_id``), rekeyed to ``knowledge_id`` since this service has no
    agent/chatbot concept of its own. See
    docs/plan/access-and-sharing/archive/design-widget-fullscreen.md (Piece 1).
    """
    __tablename__ = "knowledge_widget_settings"
    __table_args__ = {'extend_existing': True}

    knowledge_id: str = Field(primary_key=True)
    heading: Optional[str] = Field(default="Welcome")
    sub_heading: Optional[str] = Field(default="Got a Question? Just Ask!")
    greet_message: Optional[str] = Field(default="Hello! What's up?")
    color: Optional[str] = Field(default="#3a5fe3")
    show_sources: bool = Field(default=False)
    widget_icon: Optional[str] = None
    widget_avatar: Optional[str] = None
    created_at: Optional[datetime] = Field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None
    deleted: bool = Field(default=False)


class KnowledgeChatNodeConfig(SQLModel, table=True):
    """Per-NODE chat settings for one knowledge_id — the DB tier of the
    per-node config chain (see claude-logs/plan/chat-migration/
    knowledge-owned-chat.md's "Per-node model + prompt config" section).

    A chat turn runs several distinct LLM roles with genuinely different
    cost/latency profiles: a large-prompt retrieval ReAct loop, an answer
    writer, and a query rewriter. KnowledgeChatConfig above can only express
    ONE provider/model for all of them; this table lets each be set
    separately, so a knowledge base can pair an expensive retrieval model
    with a cheap rewriter.

    Sits between the request body and KnowledgeChatConfig in precedence:
        request node_configs > THIS ROW > KnowledgeChatConfig > env > hardcoded
    A null column means "inherit from the next level down", NOT "unset to
    empty" — which is why every override column is nullable with no default.

    node_name is validated against routers/chat.py::SUPPORTED_NODES
    ('rag_agent', 'generate_answer', 'refined_query_node'). The Orchestrator's
    'supervisor'/'response_node' have no counterpart in this graph, and the
    retrieval verifier stays env-only (RAG_RETRIEVAL_VERIFIER_PROVIDER/_MODEL)
    — its small-fast-model default is deliberately decoupled from the answer
    model, per the donor's own design.

    Absence of a row for a (knowledge_id, node_name) pair is the normal case,
    not an error.
    """
    __tablename__ = "knowledge_chat_node_config"
    __table_args__ = {'extend_existing': True}

    # Composite PK: one row per (knowledge base, node).
    knowledge_id: str = Field(primary_key=True)
    node_name: str = Field(primary_key=True)

    # All nullable — null means inherit, so a row overriding only `prompt`
    # still picks up provider/model from the knowledge-level config.
    provider: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    prompt: Optional[str] = None  # USER SUFFIX only; base prompts stay in code

    # Meaningful for nodes that can be skipped entirely. Retained for all
    # nodes so the column has one uniform meaning.
    enabled: bool = Field(default=True)

    created_at: Optional[datetime] = Field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None


class KnowledgeInvitation(SQLModel, table=True):
    """One row per (knowledge base, invited user) share (migration 037).

    Ported from fs-agent-chat's ``agent_invitations`` table (see
    docs/plan/access-and-sharing/archive/design-share-invitations.md) — re-keyed by ``knowledge_id``
    instead of ``agent_id``, this service's identity unit. Feeds this
    service's own ``require_permission()`` non-owner branch directly (see
    ``utils/auth/dependencies.py``), replacing the HTTP call this service
    used to make to fs-agent-chat's ``GET /v1/auth/check-access`` for every
    non-owner permission check.

    Deliberately simpler than the source in two ways:
      - No ``invited_by_email`` resolution step: the source resolves an
        agent's owner from a separate ``Agent``/``Users`` table pair because
        its ``owner_id`` can be either a user_id or an email depending on
        how the agent was created. This service's ``KnowledgeConfig.owner_id``
        is always the owner's email directly (see
        ``utils/auth/auth_utils.py::extract_owner_id_from_session``), so
        there is nothing to resolve — ``invited_by_email`` is recorded
        verbatim from the caller's session at write time.
      - No ``changed_fields`` JSONB audit trail on role updates — this
        service already has a separate, universal request-level audit log
        (``knowledge_audit_log`` / ``audit_access``, migration 017) that
        every mutating endpoint writes to; a second, table-specific audit
        column would duplicate that without adding information the standard
        `utils.logger` info-level logging in
        ``utils/db/knowledge_invitations.py`` doesn't already capture.

    invited_user_email is stored lowercased (see
    ``utils/db/knowledge_invitations.py::create_invitations``), so the
    unique constraint below collapses case variants of the same person and
    role lookups never miss on casing alone.
    """
    __tablename__ = "knowledge_invitations"
    __table_args__ = (
        UniqueConstraint("knowledge_id", "invited_user_email", name="unique_knowledge_invitation"),
        {'extend_existing': True},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    knowledge_id: str = Field(index=True)
    invited_user_email: str = Field(index=True)
    invited_by_email: str
    role: str = Field(default="viewer")

    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    # NULL until the invitation is first updated (role change).
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


# ---------------------------------------------------------------------------
# Batch Query Reports (Feature 8 — migration 038)
#
# Ported from fs-agent-dashboard's `batch_query_reports/pydantic_model_db.py`
# (see docs/plan/batch-query-reports/schema.md). Re-keyed by
# `knowledge_id` (this service's identity unit) instead of `agent_id`, and
# `owner_id` (always an email here, matching `KnowledgeConfig.owner_id` /
# `KnowledgeInvitation.invited_by_email`) instead of the source's `user_id`.
#
# Resource model: Report (reusable definition/template) -> ReportRun (one
# execution) -> ReportRunGroup/ReportRunQuery (pre-materialized, stable IDs
# the Worker-facing internal API references) -> ReportRunResult (per-query
# output, upserted on (run_id, query_id) for retry idempotency).
#
# TIMESTAMPTZ throughout (not the source's naive TIMESTAMP) — matches this
# service's own `KnowledgeInvitation` convention, so the DB layer needs no
# naive-UTC conversion step.
# ---------------------------------------------------------------------------


class ReportSettings(SQLModel, table=True):
    """Per-knowledge_id runtime override of the per-report query cap.

    When `max_queries_per_report` is NULL (or the knowledge base has no row),
    the effective limit falls back to the `REPORTS_MAX_QUERIES_PER_REPORT`
    env default (see `utils/reports/db_utils.py::get_effective_query_limit`).
    """
    __tablename__ = "report_settings"
    __table_args__ = {"extend_existing": True}

    knowledge_id: str = Field(primary_key=True)
    max_queries_per_report: Optional[int] = None
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    updated_by: Optional[str] = None  # caller email who last changed it


class Report(SQLModel, table=True):
    """A reusable report definition/template — query groups, optional report
    instructions/analysis prompt, optional schedule. Stable across runs."""
    __tablename__ = "reports"
    __table_args__ = (
        Index("idx_reports_knowledge_deleted", "knowledge_id", "deleted"),
        {"extend_existing": True},
    )

    report_id: str = Field(primary_key=True)
    knowledge_id: str = Field(index=True)
    owner_id: str  # creator's email
    name: str
    report_instructions: Optional[str] = None
    query_groups: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    analysis_prompt: Optional[str] = None
    schedule: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    # When the next scheduled run is due. NULL when the report has no
    # schedule. The scheduler tick fires reports whose next_run_at <= now,
    # then advances this (utils/reports/db_utils.py::claim_due_reports).
    next_run_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
    )
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    # NULL until the first actual update — creation leaves it unset.
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    deleted: bool = Field(default=False)
    updated_by: Optional[str] = None
    deleted_by: Optional[str] = None
    deleted_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class ReportRun(SQLModel, table=True):
    """One execution of a Report — status, timing, analysis output."""
    __tablename__ = "report_runs"
    __table_args__ = (
        Index("idx_report_runs_report", "report_id"),
        Index("idx_report_runs_knowledge", "knowledge_id"),
        {"extend_existing": True},
    )

    run_id: str = Field(primary_key=True)
    report_id: str = Field(index=True)
    knowledge_id: str = Field(index=True)
    owner_id: str  # acting caller's email at run-creation time
    status: str = Field(default="pending")  # pending|running|completed|failed|cancelled
    trigger: str = Field(default="manual")  # manual|scheduled
    started_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    ended_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    failure_reason: Optional[str] = None
    overall_summary: Optional[str] = None
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    updated_by: Optional[str] = None  # caller email who last changed it (e.g. cancel)


class ReportRunGroup(SQLModel, table=True):
    """Pre-materialized query group of a run (stable group_id)."""
    __tablename__ = "report_run_groups"
    __table_args__ = (
        Index("idx_run_groups_run", "run_id"),
        {"extend_existing": True},
    )

    group_id: str = Field(primary_key=True)
    run_id: str = Field(index=True)
    type: str  # independent|sequential
    position: int


class ReportRunQuery(SQLModel, table=True):
    """Pre-materialized query of a run (stable query_id)."""
    __tablename__ = "report_run_queries"
    __table_args__ = (
        Index("idx_run_queries_run", "run_id"),
        Index("idx_run_queries_group", "group_id"),
        {"extend_existing": True},
    )

    query_id: str = Field(primary_key=True)
    run_id: str = Field(index=True)
    group_id: str = Field(index=True)
    position: int
    text: str


class ReportRunResult(SQLModel, table=True):
    """Per-query output, written by whoever executes the run (this
    service's own in-process executor, or the Worker via the internal API).
    Upserted on (run_id, query_id) — the idempotency key for retries."""
    __tablename__ = "report_run_results"
    __table_args__ = (
        UniqueConstraint("run_id", "query_id", name="uq_run_results_run_query"),
        Index("idx_run_results_run", "run_id"),
        {"extend_existing": True},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    group_id: Optional[str] = None
    query_id: str = Field(index=True)
    session_id: Optional[str] = None
    answer: Optional[str] = None
    answer_ref: Optional[str] = None  # S3 key, for a large answer stored out of line (unused today)
    sources_used: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    retrieved_documents: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    response_time_ms: Optional[int] = None
    token_usage: Optional[int] = None
    error: Optional[str] = None  # populated -> this query failed
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class KnowledgeEnrichmentEvent(SQLModel, table=True):
    """One row per enrichment attempt on one document (migration 043).

    The enrichment-side counterpart to :class:`KnowledgeHealthEvent`. Written
    by the worker (`fs-agent-workers`) over HTTP via
    `POST /v1/{knowledge_id}/enrichment/health/events`, not in-process — the
    enrichment step runs in a separate Celery process in a separate repo, so
    unlike chat there is no local graph state to build the row from.

    Read by `utils/knowledge_health/enrichment_analytics.py`. See
    docs/plan/enrichment-analytics/plan.md for why this is a separate table
    from both `usage_events` (a metering table, with nowhere to record an
    outcome — and no row at all for a failed enrichment) and
    `knowledge_health_events` (keyed by a NOT NULL chat_id, written in-process).

    Note there is deliberately **no cost column**. Tokens and the model are
    stored; cost is computed at read time from the versioned
    `usage_service_pricing` table at the rate effective on `created_at`'s day,
    so a corrected rate fixes history instead of leaving every already-written
    row wrong. Chat does the opposite (freezes `cost_usd` on the row from a
    hardcoded map) — see docs/modules/analytics-data-sources.md.
    """

    __tablename__ = "knowledge_enrichment_events"
    __table_args__ = {'extend_existing': True}

    id: Optional[int] = Field(default=None, primary_key=True)
    knowledge_id: str = Field(index=True)
    owner_id: Optional[str] = None
    # The document this attempt enriched. Nullable because a failure can happen
    # before the object_id is resolved.
    object_id: Optional[str] = None
    # Dedupe key, generated per attempt by the worker so a retried POST is
    # idempotent (ON CONFLICT DO NOTHING at the insert site) — same role
    # chat_id plays on KnowledgeHealthEvent.
    event_key: uuid.UUID = Field(
        sa_column=Column(PG_UUID(as_uuid=True), nullable=False, unique=True),
    )
    trace_id: Optional[str] = None

    # Input size: both latency and token count scale with document length, so a
    # model comparison that ignores it is comparing the documents each model
    # happened to receive rather than the models themselves.
    document_name: Optional[str] = None
    char_count: int = Field(default=0)
    chunk_count: int = Field(default=0)

    # success | validation_failed | error — see chat/db/health_events.py's
    # _resolve_status for the equivalent 3-way split on the chat side.
    status: str
    failure_category: Optional[str] = None
    failure_detail: Optional[str] = None

    enrichment_strategy: Optional[int] = None
    enrichment_strategy_name: Optional[str] = None

    # requested_* is what the config asked for; provider/model is what actually
    # served the document. invoke_with_retry can fall through to the fallback
    # model, and attributing that document's cost/latency to the requested model
    # would make a failing model look like whatever the fallback costs.
    requested_provider: Optional[str] = None
    requested_model: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    used_fallback: bool = Field(default=False)
    fallback_reason: Optional[str] = None

    duration_ms: int = Field(default=0)
    retry_count: int = Field(default=0)

    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)

    # Completeness — the proportion of the configured schema that came back
    # filled. The enrichment analogue of chat's node_data: field_data carries
    # one entry per configured field, {field_name, level, populated,
    # value_count}, so the screen can show which fields a model keeps missing
    # rather than only that its coverage is low.
    fields_configured: int = Field(default=0)
    fields_populated: int = Field(default=0)
    field_data: List[Dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False, server_default="[]")
    )

    # How the document was READ (text_based | scanned | layout_aware | visual |
    # mixed) — migration 046. Not a duplicate of the same-named column on the
    # document row: this one records the mode the enrichment attempt actually ran
    # against, which is what a mode comparison must group by. Also the only way
    # to tell "this model scores better" from "this model only ever saw
    # layout_aware input", since llm_factory.get_enrichment_config substitutes a
    # stronger model for every mode except text_based — so mode and model move
    # together unless the knowledge base pins enrichment_model.
    document_processing_mode: Optional[str] = None

    # LangSmith cost finalization (migration 047). Enrichment cost was a LOCAL
    # estimate off config/usage/model_prices.yaml while chat cost was finalized
    # from LangSmith -- an asymmetry with no reason behind it but history. The
    # catalog goes stale SILENTLY: gemini-3.1-flash-lite was absent from it
    # entirely, so every document it enriched reported no cost while ranking as
    # the cheapest model on the comparison table.
    #
    # This is the RUN id, distinct from trace_id above: only a run id resolves
    # against LangSmith read_run.
    langsmith_run_id: Optional[str] = None
    # False, never NULL: an un-finalized row is a fact about the row, not a
    # missing observation. Read it before cost_usd -- a False here means the
    # figure is the local estimate, which measured ~2.9x high where the same
    # comparison could be made on chat.
    cost_complete: bool = Field(default=False)
    # The finalized figure. Deliberately NOT written over the token columns the
    # worker reported: keeping both is the only record of how far the estimate
    # was off, which is the entire evidence that finalizing is worth doing.
    cost_usd: Optional[float] = None

    # Validation — a quality signal that can FAIL, unlike field coverage.
    # `fields_populated` / `field_data[].populated` answer only "is this field
    # non-empty", so they cannot separate a correct extraction from a
    # hallucinated one. Measured 2026-08-27: the same PDF read two ways scored
    # an identical 100% coverage across an 11x row-level accuracy gap
    # (docs/plan/enrichment-analytics/mode-model-experiment.md).
    #
    # NULL means NOT VALIDATED and must never be rendered as 0.0 — 0.0 is a real
    # score meaning everything checked was wrong, the opposite of no opinion.
    validation_status: Optional[str] = None      # pending|passed|failed|skipped|error
    # The JUDGE's model, deliberately separate from `model` above. A model
    # grading its own output has a self-preference bias, and since the enriching
    # model varies by mode, without both columns a "model X validates well" row
    # is indistinguishable from "X was asked to grade itself".
    validation_model: Optional[str] = None
    correctness_score: Optional[float] = None    # 0..1 of checked values supported by source
    completeness_score: Optional[float] = None   # 0..1 of source instances actually extracted
    validation_data: Optional[List[Dict[str, Any]]] = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )
    # Judge latency and cost are kept OUT of duration_ms / cost: folding them in
    # would make every validated document look slower and dearer than its
    # pre-046 equivalent for no reason a reader of the chart could see.
    validation_ms: Optional[int] = None
    validation_cost_usd: Optional[float] = None

    key_type: Optional[str] = None
    event_type: str = Field(default="document_enrichment")
    schema_version: int = Field(default=1)
    # Explicit timezone=True for the same reason as KnowledgeHealthEvent's:
    # the column is TIMESTAMPTZ and the read path compares it against tz-aware
    # window bounds, which raises asyncpg.DataError against a naive bind type.
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
