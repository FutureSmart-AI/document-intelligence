from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from utils.auth.session_config import session_cookie_name
from starlette.formparsers import MultiPartParser
from utils.tracing import init_tracing, TracingMiddleware
import uvicorn
import nest_asyncio
import asyncio
import time
import logging
import os
from contextlib import asynccontextmanager

# Setup basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from utils.setup_env import (
    FASTAPI_SECRET_KEY, OTEL_SERVICE_NAME, CONNECTOR_RUNNER_INPROCESS,
    REPORTS_SCHEDULER_ENABLED, REPORTS_SCHEDULER_INTERVAL_SECONDS,
    COST_SWEEPER_ENABLED, COST_SWEEPER_INTERVAL_SECONDS, COST_SWEEPER_GRACE_SECONDS,
    COST_SWEEPER_MAX_AGE_HOURS, COST_SWEEPER_MAX_KNOWLEDGE_BASES,
)
# You can set your secret key here or use environment variables
# Authentication secrets are supplied by the installation environment.

fastapi_metadata = [
    {
        "name": "Knowledge Management",
        "description": "Provides functionality to manage the knowledge service"
    },
    {
        "name": "Knowledge Configuration",
        "description": "Provides functionality to configure the knowledge service"
    },
    {
        "name": "Document Management",
        "description": "Provides functionality to manage the documents"
    },
    {
        "name": "Batch Document Management",
        "description": "Provides functionality to manage the batch documents"
    },
    {
        "name": "Search",
        "description": "Provides functionality to search the knowledge"
    },
    {
        "name": "Chunk Management",
        "description": "Provides functionality to manage chunks including search, retrieval, update, and deletion"
    },
    {
        "name": "Metadata Extraction",
        "description": "Provides functionality to extract metadata from documents and chunks"
    },
    {
        "name":"Enrichment Configuration",
        "description": "Provides functionality to configure the metadata enrichment parameters"
    },
    {
        "name": "ClickUp Sync",
        "description": "Self-serve ClickUp indexing: connect, select docs, schedule, and run syncs"
    },
    {
        "name": "Chat",
        "description": "The Knowledge Service's own chat endpoint — RAG retrieval, answer generation, and session/evidence persistence, independent of the Orchestrator"
    },
    {
        "name": "Widget Configuration",
        "description": "Embeddable chat widget branding settings — heading, greeting, color, icon, avatar"
    },
    {
        "name": "API Key Management",
        "description": "Self-service generation, revocation, and listing of per-user API keys"
    },
    {
        "name": "Knowledge Invitations",
        "description": "Share a knowledge base with another user (editor/viewer) and enforce that role — replaces the HTTP RBAC delegation to fs-agent-chat"
    },
    {
        "name": "Batch Query Reports",
        "description": "Define a batch of chat queries against a knowledge base, run them on demand or on a schedule, and retrieve the structured report (results, progress, export)"
    },
    {
        "name": "Batch Query Reports (Internal)",
        "description": "Worker-facing surface for Batch Query Reports — fetch a run definition, report status/results/analysis, or trigger in-process execution. Master API key only, never browser-facing."
    }
]

init_tracing(OTEL_SERVICE_NAME)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Launch the connector (ClickUp) sync runner in-process, unless disabled.

    Reuses this always-on service instead of a dedicated runner container. A Postgres
    advisory lock inside run_loop() ensures only one loop is active across uvicorn
    workers/replicas, so launching it in every process is safe.
    """
    runner_task = None
    if CONNECTOR_RUNNER_INPROCESS:
        from utils.connectors.runner import run_loop
        runner_task = asyncio.create_task(run_loop())
        logger.info("Connector in-process runner launched")

    # Batch Query Reports scheduler (docs/plan/batch-query-reports/execution.md
    # §9) — gated off by default, same posture as the donor's REPORTS_SCHEDULER_ENABLED.
    reports_scheduler_task = None
    if REPORTS_SCHEDULER_ENABLED:
        from utils.reports.scheduler import run_scheduler_loop
        reports_scheduler_task = asyncio.create_task(run_scheduler_loop(REPORTS_SCHEDULER_INTERVAL_SECONDS))
        logger.info("Batch Query Reports scheduler launched")
    # Chat cost sweeper. Chat cost already finalizes per turn in routers/chat.py;
    # this catches the rows where that attempt lost (LangSmith not yet priced
    # within the retry delays, task killed by a deploy, feature disabled at the
    # time) and would otherwise keep the local estimate until somebody pressed
    # sync. One sweeper per deployment via a Postgres advisory lock, so launching
    # it in every worker is safe.
    cost_sweeper_task = None
    if COST_SWEEPER_ENABLED:
        from utils.knowledge_health.cost_sweeper import run_sweeper_loop
        cost_sweeper_task = asyncio.create_task(run_sweeper_loop(
            interval_seconds=COST_SWEEPER_INTERVAL_SECONDS,
            grace_seconds=COST_SWEEPER_GRACE_SECONDS,
            max_age_hours=COST_SWEEPER_MAX_AGE_HOURS,
            max_knowledge_bases=COST_SWEEPER_MAX_KNOWLEDGE_BASES,
        ))
        logger.info("Chat cost sweeper launched")

    try:
        yield
    finally:
        if cost_sweeper_task:
            cost_sweeper_task.cancel()
            try:
                await cost_sweeper_task
            except asyncio.CancelledError:
                pass
        if runner_task:
            runner_task.cancel()
            try:
                await runner_task
            except asyncio.CancelledError:
                pass
        if reports_scheduler_task:
            reports_scheduler_task.cancel()
            try:
                await reports_scheduler_task
            except asyncio.CancelledError:
                pass


app = FastAPI(openapi_tags=fastapi_metadata, root_path="/api/knowledge", lifespan=lifespan)

app.add_middleware(TracingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or specify allowed origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Add Session middleware
app.add_middleware(SessionMiddleware, secret_key=FASTAPI_SECRET_KEY, session_cookie=session_cookie_name(), https_only=os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true")

if os.getenv("AUTH_MODE") == "standalone":
    from utils.auth.standalone import router as standalone_auth_router
    app.include_router(standalone_auth_router)
MultiPartParser.max_part_size = 10 * 1024 * 1024  # 10MB

# Logging time taken for each api request
@app.middleware("http")
async def log_response_time(request: Request, call_next):
    start_time = time.time()
    logger.info(
        f"---> Request started: Method: {request.method} Path: {request.url.path}, URL: {request.url} "
    )
    response = await call_next(request)
    process_time = time.time() - start_time
    logger.info(f"<--- Request Completed: {request.url.path} completed in {process_time:.4f} seconds")
    return response 

# from router.knowledge_upload import router as knowledge_upload_router
# from router.knowledge_config import router as knowledge_config_router
# from router.knowledge_retrieval import router as knowledge_retrieval_router
# from router.enrichment_configuration import router as enrichment_configuration_router

# app.include_router(knowledge_upload_router, tags=["Knowledge Upload"])
# app.include_router(knowledge_config_router, tags=["Knowledge Configuration"])
# app.include_router(knowledge_retrieval_router, tags=["Knowledge Retrieval"])
# app.include_router(enrichment_configuration_router, tags=["Enrichment Configuration"])

from routers.knowledge_management import router as knowledge_management_router
from routers.document_management import router as document_management_router
from routers.batch_document_management import router as batch_document_management_router
from routers.chunk_management import router as chunk_management_router
from routers.search import router as search_router
from routers.metadata_extraction_api import router as metadata_extraction_router
from routers.enrichment_configuration_new import router as enrichment_configuration_router_new
from routers.knowledge_config_new import router as knowledge_config_router_new
from routers.admin_config import router as admin_config_router
from routers.clickup_sync import router as clickup_sync_router
from routers.chat import router as chat_router
from routers.api_key_management import router as api_key_management_router
from routers.widget_config import router as widget_config_router
from routers.usage_events import router as usage_events_router
from routers.usage_analytics import router as usage_analytics_router
from routers.usage_credentials import router as usage_credentials_router
from routers.usage_limits import router as usage_limits_router
from routers.usage_owner_config import router as usage_owner_config_router
from routers.usage_llm_alerts import router as usage_llm_alerts_router
from routers.knowledge_health import router as knowledge_health_router
from routers.knowledge_enrichment_health import router as knowledge_enrichment_health_router
from routers.knowledge_invitations import router as knowledge_invitations_router
from routers.reports import router as reports_router
from routers.reports_internal import router as reports_internal_router
from routers.samples import router as samples_router

# Universal per-request access audit (writes one row to knowledge_audit_log).
# Attached at every router mount so it covers all current and future routes.
# See fs-agent-actions/docs/runbook-audit-logging.md.
from utils.auth.dependencies import audit_access

_audit = [Depends(audit_access)]

# Before every /v1/{knowledge_id}/... router, so "/v1/samples/usage" is never read as a
# knowledge id.
app.include_router(samples_router, tags=["Sample Collections"], dependencies=_audit)
app.include_router(knowledge_management_router, tags=["Knowledge Management"], dependencies=_audit)
app.include_router(document_management_router, tags=["Document Management"], dependencies=_audit)
app.include_router(batch_document_management_router, tags=["Batch Document Management"], dependencies=_audit)
app.include_router(chunk_management_router, tags=["Chunk Management"], dependencies=_audit)
app.include_router(search_router, tags=["Search"], dependencies=_audit)
app.include_router(metadata_extraction_router, tags=["Metadata Extraction"], dependencies=_audit)
app.include_router(enrichment_configuration_router_new, tags=["Enrichment Configuration"], dependencies=_audit)
app.include_router(knowledge_config_router_new, tags=["Knowledge Configuration"], dependencies=_audit)
app.include_router(admin_config_router, tags=["Admin Enrichment Configuration"], dependencies=_audit)
app.include_router(clickup_sync_router, tags=["ClickUp Sync"], dependencies=_audit)
app.include_router(chat_router, tags=["Chat"], dependencies=_audit)
app.include_router(api_key_management_router, tags=["API Key Management"], dependencies=_audit)
app.include_router(widget_config_router, tags=["Widget Configuration"], dependencies=_audit)

# Usage-service migration (fs-agent-usage-limits -> in-process). See
# docs/plan/usage-and-limits/archive/design-service-migration.md. All routes are /v1/{knowledge_id}/usage/...
app.include_router(usage_events_router, tags=["Usage Events"], dependencies=_audit)
app.include_router(usage_analytics_router, tags=["Usage Analytics"], dependencies=_audit)
app.include_router(usage_credentials_router, tags=["Usage Credentials"], dependencies=_audit)
app.include_router(usage_limits_router, tags=["Usage Limits"], dependencies=_audit)
app.include_router(usage_owner_config_router, tags=["Usage Owner Config"], dependencies=_audit)
app.include_router(usage_llm_alerts_router, tags=["Usage LLM Alerts"], dependencies=_audit)

# Agent Health / Analytics migration (fs-agent-chat -> in-process). See
# docs/plan/analytics-and-health/endpoints.md. Per-knowledge_id routes are
# /v1/{knowledge_id}/health/...; the cross-tenant grid is /v1/health/knowledge-bases.
app.include_router(knowledge_health_router, tags=["Agent Health"], dependencies=_audit)

# Enrichment Analytics — the ingest-side counterpart of Agent Health. See
# docs/plan/enrichment-analytics/plan.md. Reads are
# /v1/{knowledge_id}/health/enrichment/... (grouped under health so the
# Analytics screen calls one path family); the worker-facing ingest is
# /internal/enrichment-health/events/{knowledge_id}.
app.include_router(knowledge_enrichment_health_router, tags=["Enrichment Analytics"], dependencies=_audit)

# Share agent migration (fs-agent-chat -> in-process). See
# docs/plan/access-and-sharing/archive/design-share-invitations.md. Replaces the HTTP RBAC delegation
# to CHAT_AUTH_BASE for non-owner permission checks (require_permission's
# non-owner branch now reads this router's knowledge_invitations table
# directly instead).
app.include_router(knowledge_invitations_router, tags=["Knowledge Invitations"], dependencies=_audit)

# Batch Query Reports migration (Feature 8, fs-agent-dashboard -> in-process
# storage/CRUD here + in-process chat execution). See
# docs/plan/batch-query-reports/execution.md. Worker/Celery/SQS itself is
# untouched — this service is now the queue producer + the Worker-facing
# internal API, same as it already is for document ingestion.
app.include_router(reports_router, tags=["Batch Query Reports"], dependencies=_audit)
app.include_router(reports_internal_router, tags=["Batch Query Reports (Internal)"], dependencies=_audit)

@app.get("/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    nest_asyncio.apply()
    uvicorn.run(app, host="0.0.0.0", port=8000)
