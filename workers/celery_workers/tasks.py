from celery_workers.app import app # Import the Celery app instance
from celery_utils.processing.file_utils import process_and_index_file
from celery_utils.processing.text_utils import process_and_index_text
from celery_utils.processing.weblink_utils import process_and_index_weblink
from celery_utils.db.db_utils import update_processing_status
from celery_utils.shared.tracing import traced_step
from opentelemetry import trace as otel_trace
from opentelemetry.trace import StatusCode, SpanKind
from services import celery_setup_env
from services.celery_logger import logger # Use the internal logger

# All helpers and tasks are now synchronous

@app.task(name="workers.tasks.process_and_index_file")
def process_and_index_file_task(object_id, file_url, user_id, knowledge_id, file_name, content_type, use_llama_parse, contextualise_chunks, chunk_size, hybrid_search, embedding_model, transcript_provider, block_chunking=False, enable_chunking=True, extra_metadata=None, reindex=False, document_processing_mode=None):
    """Celery task to process and index a file. Accepts and returns object_id.

    ``document_processing_mode`` defaults to None so that messages queued by an older
    Knowledge Service — which do not carry the field at all — still deserialize into
    this signature instead of failing on an unexpected keyword.
    """
    tracer = otel_trace.get_tracer(__name__)
    with tracer.start_as_current_span("Queue: Process File", kind=SpanKind.SERVER) as span:
        span.set_attribute("object_id", str(object_id))
        span.set_attribute("file_name", str(file_name))
        span.set_attribute("knowledge_id", str(knowledge_id))
        span.set_attribute("embedding_model", str(embedding_model))
        span.set_attribute("document_processing_mode", str(document_processing_mode or ""))
        span.set_attribute("celery.task_id", str(process_and_index_file_task.request.id or ""))
        try:
            logger.info(f"Received file indexing task for object_id: {object_id}, file_name: {file_name}")
            # Root LangSmith run for the whole task, so the Celery identity (task id,
            # retry count, queue) lands on the trace. Everything below — Process
            # Document, the extraction steps, Enrich Document, the embeddings —
            # nests underneath it, giving one readable tree per ingested document.
            with traced_step(
                "Queue: Process File",
                run_type="chain",
                inputs={"file_name": file_name, "content_type": content_type},
                object_id=str(object_id),
                knowledge_id=str(knowledge_id),
                user_id=str(user_id),
                file_name=str(file_name),
                content_type=str(content_type),
                embedding_model=str(embedding_model),
                document_processing_mode=str(document_processing_mode or ""),
                reindex=bool(reindex),
                celery_task_id=str(process_and_index_file_task.request.id or ""),
                celery_retries=int(process_and_index_file_task.request.retries or 0),
            ):
                result = process_and_index_file(
                    object_id=object_id, file_url=file_url,
                    user_id=user_id, knowledge_id=knowledge_id,
                    file_name=file_name, content_type=content_type,
                    use_llama_parse=use_llama_parse, contextualise_chunks=contextualise_chunks,
                    chunk_size=chunk_size, hybrid_search=hybrid_search,
                    embedding_model=embedding_model, transcript_provider=transcript_provider,
                    enable_chunking=enable_chunking,
                    extra_metadata=extra_metadata,
                    reindex=reindex,
                    document_processing_mode=document_processing_mode,
                )
            logger.info(f"Completed file indexing task for object_id: {object_id}")
            return result
        except Exception as e:
            span.record_exception(e)
            span.set_status(StatusCode.ERROR, str(e))
            logger.error(f"Task failed for object_id={object_id}, file_url={file_url}, user_id={user_id}, knowledge_id={knowledge_id}, file_name={file_name}: {str(e)}")
            update_processing_status(knowledge_id, object_id, "failed", error_detail=str(e))
            raise

@app.task(name="workers.tasks.process_and_index_text")
def process_and_index_text_task(user_id, knowledge_id, object_id, text, text_url, file_name, chunk_size, hybrid_search, embedding_model, contextualise_chunks, enable_chunking=True):
    """Celery task to process and index raw text. Accepts and returns object_id."""
    tracer = otel_trace.get_tracer(__name__)
    with tracer.start_as_current_span("Queue: Process Text", kind=SpanKind.SERVER) as span:
        span.set_attribute("object_id", str(object_id))
        span.set_attribute("knowledge_id", str(knowledge_id))
        span.set_attribute("embedding_model", str(embedding_model))
        span.set_attribute("celery.task_id", str(process_and_index_text_task.request.id or ""))
        try:
            logger.info(f"Received text indexing task for object_id: {object_id}")
            # Root LangSmith run for the whole task — see the file task above.
            with traced_step(
                "Queue: Process Text",
                run_type="chain",
                inputs={"file_name": file_name},
                object_id=str(object_id),
                knowledge_id=str(knowledge_id),
                user_id=str(user_id),
                file_name=str(file_name),
                embedding_model=str(embedding_model),
                celery_task_id=str(process_and_index_text_task.request.id or ""),
                celery_retries=int(process_and_index_text_task.request.retries or 0),
            ) as step:
                step.set_text(text, key="input_text")
                result = process_and_index_text(
                    user_id=user_id, knowledge_id=knowledge_id,
                    object_id=object_id, text=text, text_url=text_url,
                    file_name=file_name, chunk_size=chunk_size,
                    hybrid_search=hybrid_search, embedding_model=embedding_model,
                    contextualise_chunks=contextualise_chunks, enable_chunking=enable_chunking
                )
            logger.info(f"Completed text indexing task for object_id: {object_id}")
            return result
        except Exception as e:
            span.record_exception(e)
            span.set_status(StatusCode.ERROR, str(e))
            logger.error(f"Task failed for user_id={user_id}, knowledge_id={knowledge_id}, object_id={object_id}, file_name={file_name}: {str(e)}")
            update_processing_status(knowledge_id, object_id, "failed", error_detail=str(e))
            raise

@app.task(name="workers.tasks.process_and_index_weblink")
def process_and_index_weblink_task(user_id, knowledge_id, object_id, weblink, use_llama_parse, scrape_using, scraping_method, max_depth, chunk_size, hybrid_search, embedding_model, contextualise_chunks, enable_chunking=True, document_processing_mode=None):
    """Celery task to process and index a weblink. Accepts and returns object_id.

    ``document_processing_mode`` defaults to None so that messages queued by an older
    producer keep working; it only affects a weblink that resolves to a file
    (PDF/DOCX/plain text) — see ``_process_file_content`` in weblink_utils.
    """
    tracer = otel_trace.get_tracer(__name__)
    with tracer.start_as_current_span("Queue: Process Weblink", kind=SpanKind.SERVER) as span:
        span.set_attribute("object_id", str(object_id))
        span.set_attribute("weblink", str(weblink))
        span.set_attribute("knowledge_id", str(knowledge_id))
        span.set_attribute("embedding_model", str(embedding_model))
        span.set_attribute("document_processing_mode", str(document_processing_mode or ""))
        span.set_attribute("celery.task_id", str(process_and_index_weblink_task.request.id or ""))
        try:
            logger.info(f"Received weblink indexing task for object_id: {object_id}, weblink: {weblink}, mode={document_processing_mode}")
            # Root LangSmith run for the whole task — see the file task above. In
            # crawl mode this one root covers the parent URL and every child page.
            with traced_step(
                "Queue: Process Weblink",
                run_type="chain",
                inputs={"weblink": weblink},
                object_id=str(object_id),
                knowledge_id=str(knowledge_id),
                user_id=str(user_id),
                weblink=str(weblink),
                scraping_method=str(scraping_method or ""),
                max_depth=int(max_depth or 0),
                embedding_model=str(embedding_model),
                document_processing_mode=str(document_processing_mode or ""),
                celery_task_id=str(process_and_index_weblink_task.request.id or ""),
                celery_retries=int(process_and_index_weblink_task.request.retries or 0),
            ):
                result = process_and_index_weblink(
                    user_id=user_id, knowledge_id=knowledge_id,
                    object_id=object_id, weblink=weblink,
                    use_llama_parse=use_llama_parse, scrape_using=scrape_using,
                    scraping_method=scraping_method, max_depth=max_depth,
                    chunk_size=chunk_size, hybrid_search=hybrid_search,
                    embedding_model=embedding_model, contextualise_chunks=contextualise_chunks,
                    enable_chunking=enable_chunking, document_processing_mode=document_processing_mode,
                )
            logger.info(f"Completed weblink indexing task for object_id: {object_id}")
            return result
        except Exception as e:
            span.record_exception(e)
            span.set_status(StatusCode.ERROR, str(e))
            logger.error(f"Task failed for user_id={user_id}, knowledge_id={knowledge_id}, object_id={object_id}, weblink={weblink}: {str(e)}")
            update_processing_status(knowledge_id, object_id, "failed", error_detail=str(e))
            raise

def _process_report_run(
    run_id,
    span,
    *,
    service,
    chat,
    label,
    max_concurrent_queries=None,
    max_concurrent_groups=None,
    poll_cancellation=False,
):
    """Run one Batch Query Report run end-to-end, against `service`.

    Pipeline (matches dashboard `docs/batch-query-reports-spec.md`):
      1. GET  /internal/runs/{run_id}            — fetch pre-materialized definition
      2. POST /internal/runs/{run_id}/status     — running
      3. Execute groups (independent in parallel; sequential in order; groups concurrent)
         and POST /internal/runs/{run_id}/results in batches as they complete
      4. POST /internal/runs/{run_id}/analysis   — if analysis_prompt is set
      5. POST /internal/runs/{run_id}/status     — completed (or failed on hard error)

    Two services own reports and expose this same internal contract — the Dashboard and
    NL2SQL — so the pipeline is shared here and `service`/`chat` choose which pair to talk
    to. Both are idempotent (status on run_id+target_state, results upserted by query_id),
    so a Celery retry replays cleanly. Per-query failures populate the result row's `error`
    field rather than failing the whole run — only catastrophic issues mark the run failed.
    """
    from celery_utils.reports import analysis as reports_analysis
    from celery_utils.reports import runner as reports_runner
    logger.info(f"[{label}] Received report run task for run_id={run_id}")

    definition = service.get_run_definition(run_id)
    if not definition:
        # 404 or unreachable — the owning service is the system of record, so
        # without a definition there's nothing meaningful to mark.
        msg = f"run definition not found for run_id={run_id}"
        logger.error(f"[{label}] {msg}")
        span.set_status(StatusCode.ERROR, msg)
        return {"run_id": run_id, "status": "skipped", "reason": msg}

    span.set_attribute("report_id", str(definition.get("report_id") or ""))
    span.set_attribute("agent_id", str(definition.get("agent_id") or ""))

    service.mark_running(run_id)

    # 3. Execute groups & flush results in batches.
    transcript = reports_runner.execute_run(
        definition,
        chat=chat,
        results_client=service,
        max_concurrent_queries=max_concurrent_queries,
        max_concurrent_groups=max_concurrent_groups,
        # Polled between queries, so a cancel stops the work instead of only having its
        # results discarded. Opt-in per backend: it costs one small GET per query, worth it
        # where a query is an agent pipeline, not worth it for a run that is already uncapped.
        is_cancelled=(lambda: service.is_run_cancelled(run_id)) if poll_cancellation else None,
    )

    # 4. Optional analysis prompt — best-effort. Failure here does
    #    NOT fail the run; the user still has all query results.
    analysis_prompt = definition.get("analysis_prompt")
    if analysis_prompt:
        summary = reports_analysis.run_analysis(analysis_prompt, transcript)
        if summary:
            service.submit_analysis(run_id, summary)
        else:
            logger.warning(
                f"[{label}] analysis generation returned no summary for run {run_id}"
            )

    # 5. Close the run out.
    service.mark_completed(run_id)
    logger.info(f"[{label}] Completed report run for run_id={run_id}")
    return {"run_id": run_id, "status": "completed"}


def _report_run_failed(run_id, span, exc, *, service, label):
    """Shared failure path: record it on the span, then tombstone the run."""
    span.record_exception(exc)
    span.set_status(StatusCode.ERROR, str(exc))
    logger.error(
        f"[{label}] Catastrophic failure for run_id={run_id}: {exc}",
        exc_info=True,
    )
    # Best-effort: tombstone the run so users don't see it stuck in
    # 'running' forever. If this POST itself fails, Celery's retry
    # will re-enter the task and the idempotent status guard will
    # keep things consistent.
    try:
        service.mark_failed(run_id, failure_reason=str(exc)[:500])
    except Exception as inner:
        logger.error(
            f"[{label}] also failed to post 'failed' status for {run_id}: {inner}"
        )


@app.task(name="workers.tasks.process_report_run", bind=True)
def process_report_run_task(self, run_id):
    """Execute a Batch Query Report run owned by the **Dashboard**.

    Queries are executed through the Chat Service. See `_process_report_run` for the pipeline.
    NL2SQL-owned runs arrive on `workers.tasks.process_nl2sql_report_run` instead.
    """
    from celery_utils.reports import chat_client as reports_chat
    from celery_utils.reports import dashboard_client as reports_dashboard
    tracer = otel_trace.get_tracer(__name__)
    with tracer.start_as_current_span("Queue: Process Report Run", kind=SpanKind.SERVER) as span:
        span.set_attribute("run_id", str(run_id))
        span.set_attribute("celery.task_id", str(self.request.id or ""))
        try:
            return _process_report_run(
                run_id,
                span,
                service=reports_dashboard,
                chat=reports_chat,
                label="reports",
            )
        except Exception as e:
            _report_run_failed(run_id, span, e, service=reports_dashboard, label="reports")
            raise


@app.task(name="workers.tasks.process_nl2sql_report_run", bind=True)
def process_nl2sql_report_run_task(self, run_id):
    """Execute a Batch Query Report run owned by **NL2SQL**.

    The same pipeline as the dashboard task, pointed at NL2SQL: definition and results go to
    `${NL2SQL_INTERNAL_BASE}/internal/runs/*`, and each query is asked of the agent itself via
    `POST ${NL2SQL_CHAT_BASE}/v1/agents/{agent_id}/chat`. The chat service cannot serve these
    agents — it 404s on any agent NL2SQL created — which is why they need their own clients.

    **Why a separate task instead of a flag on the existing one:** both services mint run ids as
    `run_<32hex>` and the message carries only the run_id, so nothing at runtime could tell whose
    run it is. The task name IS the routing decision, made by whoever enqueued it.

    Unlike the dashboard path this one is **capped**: there, queries hit the chat service, which
    absorbs the load; here each query runs a full agent pipeline holding a source-database
    connection inside the NL2SQL API process. The caps bound one run — several runs can still be
    in flight across the fleet, so size them against NL2SQL's replicas and DB pool.
    """
    from celery_utils.reports import nl2sql_chat_client as reports_nl2sql_chat
    from celery_utils.reports import nl2sql_client as reports_nl2sql
    tracer = otel_trace.get_tracer(__name__)
    with tracer.start_as_current_span(
        "Queue: Process NL2SQL Report Run", kind=SpanKind.SERVER
    ) as span:
        span.set_attribute("run_id", str(run_id))
        span.set_attribute("celery.task_id", str(self.request.id or ""))
        span.set_attribute("reports.service", "nl2sql")
        try:
            return _process_report_run(
                run_id,
                span,
                service=reports_nl2sql,
                chat=reports_nl2sql_chat,
                label="reports.nl2sql",
                max_concurrent_queries=celery_setup_env.NL2SQL_REPORTS_MAX_CONCURRENT_QUERIES,
                max_concurrent_groups=celery_setup_env.NL2SQL_REPORTS_MAX_CONCURRENT_GROUPS,
                poll_cancellation=True,
            )
        except Exception as e:
            _report_run_failed(run_id, span, e, service=reports_nl2sql, label="reports.nl2sql")
            raise


@app.task(name="workers.tasks.process_knowledge_report_run", bind=True)
def process_knowledge_report_run_task(self, run_id):
    """Trigger a Batch Query Report run owned by the **Knowledge Service**.

    Unlike the dashboard and NL2SQL tasks, this one does **not** run the pipeline. The Knowledge
    Service executes the whole pre-materialized run in-process against its own chat graph
    (`POST /internal/reports/runs/{run_id}/execute`), so there is no per-query HTTP hop and this
    task's only job is to trigger it.

    **Why not the shared `_process_report_run` pipeline:** that pipeline needs a chat client for
    the owning service, and this repo's `chat_client` speaks only fs-agent-chat's
    `/v1/chat_completion` (agent_id in the body). It cannot serve knowledge-base chat, and
    building a third chat client would duplicate exactly what `/execute` exists to avoid.

    **Why a separate task, not a flag:** same reason as the NL2SQL task — every service mints run
    ids in the same shape and the message carries only the run_id, so nothing at runtime can tell
    whose run it is. The task name IS the routing decision, made by whoever enqueued it.

    **Lifecycle ownership:** the Knowledge Service marks the run running/completed/failed itself.
    This task returns as soon as the trigger is accepted (the endpoint answers 202 and executes as
    a background task), so a successful return here means "accepted", NOT "finished". `started:
    False` in the response means the run had already left `pending` — a benign no-op on a Celery
    retry or a duplicate enqueue, not a failure.
    """
    from celery_utils.reports import knowledge_client as reports_knowledge
    tracer = otel_trace.get_tracer(__name__)
    with tracer.start_as_current_span(
        "Queue: Trigger Knowledge Report Run", kind=SpanKind.SERVER
    ) as span:
        span.set_attribute("run_id", str(run_id))
        span.set_attribute("celery.task_id", str(self.request.id or ""))
        span.set_attribute("reports.service", "knowledge")
        logger.info(f"[reports.knowledge] Triggering in-process execution for run_id={run_id}")
        try:
            result = reports_knowledge.trigger_execute(run_id)
        except Exception as e:
            span.record_exception(e)
            span.set_status(StatusCode.ERROR, str(e))
            logger.error(
                f"[reports.knowledge] trigger failed for run_id={run_id}: {e}", exc_info=True
            )
            # No tombstoning here, deliberately: the Knowledge Service owns this run's status and
            # we may not even have reached it. Raising lets Celery retry; the /execute guard makes
            # a duplicate trigger a no-op.
            raise

        if result is None:
            # Exhausted retries or a 4xx (missing run, auth). Raise so Celery retries rather than
            # silently reporting success for a run that was never started.
            msg = f"execute trigger not accepted for run_id={run_id}"
            logger.error(f"[reports.knowledge] {msg}")
            span.set_status(StatusCode.ERROR, msg)
            raise RuntimeError(msg)

        started = bool(result.get("started"))
        span.set_attribute("reports.started", started)
        span.set_attribute("reports.run_status", str(result.get("status") or ""))
        if not started:
            logger.info(
                f"[reports.knowledge] run {run_id} already underway or terminal "
                f"(status={result.get('status')}) — nothing to do"
            )
        return {
            "run_id": run_id,
            "status": result.get("status"),
            "started": started,
            "executed_by": "knowledge-service",
        }


@app.task(name="workers.tasks.check_db_connection")
def check_db_connection_task():
    """Task to check database connection health."""
    try:
        from celery_utils.db.db_pool import db_pool_manager
        result = db_pool_manager.check_pool_health()
        if result is not None:
            logger.info("Database connection pool is healthy")
            return {"pool_healthy": True}
        else:
            logger.warning("Database connection pool is unhealthy, attempting to recreate")
            db_pool_manager.close()
            db_pool_manager.get_pool()
            return {"pool_healthy": False}
    except Exception as e:
        logger.error(f"Database connection check failed: {e}")
        return {"pool_healthy": False, "error": str(e)}
