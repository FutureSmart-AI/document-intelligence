import os
from dotenv import load_dotenv
from utils.logger import logger
import urllib.parse

# Determine the mode (DEV or PROD)
MODE = os.getenv("MODE", "LOCAL").upper()

# env_file = ".env.prod"
env_file = os.getenv("ENV_FILE") or (".env" if MODE == "STANDALONE" else ".env.prod" if MODE == "PROD" else ".env.dev" if MODE == "DEV" else ".env.local")

logger.info(f"Loading environment variables from {env_file}")
# Load the environment variables from the appropriate file
load_dotenv(env_file, override=False)

# FASTAPI Configuration
FASTAPI_SECRET_KEY = os.getenv("FASTAPI_SECRET_KEY")
# Keep persisted generated-key hashes stable when deployment auth/session keys rotate.
API_KEY_HASH_SECRET = os.getenv("API_KEY_HASH_SECRET") or FASTAPI_SECRET_KEY

# Fernet key (urlsafe base64, 32 bytes) used to encrypt connector secrets
# (e.g. ClickUp API tokens) stored per-knowledge in connector_sync_config.
CONNECTOR_SECRET_KEY = os.getenv("CONNECTOR_SECRET_KEY")

POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_PORT = os.getenv("POSTGRES_PORT")
POSTGRES_DB_NAME = os.getenv("POSTGRES_DB_NAME")

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
BUCKET_NAME = os.getenv("BUCKET_NAME")

LLAMA_PARSE_API_KEY = os.getenv("LLAMA_PARSE_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TRANSCRIPT_PROVIDER = os.getenv("TRANSCRIPT_PROVIDER", "openai")

# Chat LLM providers (chat/llm_factory.py). Platform-default keys, used when
# an owner has no credential of their own for the provider.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY")
MINIMAX_BASE_URL = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1")

# Chat LLM rate limiting (chat/llm_limiter.py) — process-wide token bucket
# shared by every chat model, so a burst of tool calls doesn't exceed the
# provider's requests-per-minute limit. See chat/llm_limiter.py docstring.
LLM_MAX_RPS = float(os.getenv("LLM_MAX_RPS", "8"))
LLM_MAX_BURST = float(os.getenv("LLM_MAX_BURST", str(LLM_MAX_RPS)))
LLM_REQUEST_TIMEOUT = float(os.getenv("LLM_REQUEST_TIMEOUT", "60"))

# How many prior turns' retrieval evidence (chat/db/chat_logs.py::get_recent_evidence)
# to inject as already-in-play evidence into the RAG agent. Turns that retrieved
# nothing are skipped, not counted — so a no-retrieval follow-up doesn't burn a
# slot. Matches fs-agent-chat's chat_utils/chat_setup_env.py default.
RAG_RECENT_EVIDENCE_TURNS = int(os.getenv("RAG_RECENT_EVIDENCE_TURNS", "2"))

# Model used by chat/chart_generator.py for its CSV field-determination and row
# extraction calls. Configurable because it is pinned to one provider's catalogue,
# and a project losing access to a model breaks charts outright with a 403 —
# observed live 2026-08-20: "Project ... does not have access to model
# gpt-4o-mini", which failed every chart attempt and was unfixable without a
# redeploy. Keep this pointing at a model the platform OpenAI project can reach.
CHART_EXTRACTION_MODEL = os.getenv("CHART_EXTRACTION_MODEL", "gpt-4o-mini")

# External Chart API called by chat/chart_generator.py (chart_node, G16).
# Same default URL as fs-agent-chat/chat_utils/chart_utils_module/config.py.
CHART_API_URL = os.getenv(
    "CHART_API_URL", ""
)

# How many prior session-history TURNS (user+assistant pairs) to inject into
# the RAG agent's message thread so it can resolve pronouns/follow-ups
# ("tell me more about that"). build_history_messages is called with
# limit=RAG_SESSION_HISTORY_TURNS * 2 (2 messages per turn). Matches
# fs-agent-chat's chat_utils/chat_setup_env.py default.
RAG_SESSION_HISTORY_TURNS = int(os.getenv("RAG_SESSION_HISTORY_TURNS", "5"))

# Level 3 (env default) of chat/config.py::resolve_chat_config's 4-level
# provider/model/temperature/system_prompt chain (request body field >
# knowledge_chat_config row > these env vars > hardcoded default). Used by
# the main retrieval + answer-generation + query-refinement LLM calls — NOT
# the retrieval verifier (RAG_RETRIEVAL_VERIFIER_PROVIDER/MODEL above, a
# separate always-env-configured knob) or the ModelFallbackMiddleware model
# (RAG_FALLBACK_PROVIDER/MODEL, read directly in agent_creation.py).
RAG_CHAT_LLM_PROVIDER = os.getenv("RAG_CHAT_LLM_PROVIDER", "").strip() or None
RAG_CHAT_LLM_MODEL = os.getenv("RAG_CHAT_LLM_MODEL", "").strip() or None
RAG_CHAT_LLM_TEMPERATURE = os.getenv("RAG_CHAT_LLM_TEMPERATURE", "").strip() or None
RAG_CHAT_MAX_TOKENS = os.getenv("RAG_CHAT_MAX_TOKENS", "").strip() or None
RAG_CHAT_SYSTEM_PROMPT = os.getenv("RAG_CHAT_SYSTEM_PROMPT", "").strip() or None

# Per-node provider/model, one pair per graph node. These sit ABOVE the flat
# RAG_CHAT_LLM_PROVIDER/_MODEL above in chat/config.py's chain, because the flat
# pair applies to *every* node at once: setting it to the answer model would also
# drag refined_query_node off its cheap model and raise cost on every turn. These
# let each node be pinned independently, which is what the three roles actually
# want — an expensive reasoning model for retrieval/answering, a cheap one for
# query rewriting.
#
# Keyed by the node names in chat/config.py::NODE_HARDCODED_DEFAULTS. Provider and
# model resolve as a PAIR (see resolve_chat_config): a node supplying only one of
# the two is ignored for both, since e.g. anthropic + gpt-5.4-mini is a runtime
# error rather than a configuration.
RAG_CHAT_NODE_LLM_ENV = {
    "generate_answer": (
        os.getenv("RAG_CHAT_ANSWER_LLM_PROVIDER", "").strip() or None,
        os.getenv("RAG_CHAT_ANSWER_LLM_MODEL", "").strip() or None,
    ),
    "rag_agent": (
        os.getenv("RAG_CHAT_AGENT_LLM_PROVIDER", "").strip() or None,
        os.getenv("RAG_CHAT_AGENT_LLM_MODEL", "").strip() or None,
    ),
    "refined_query_node": (
        os.getenv("RAG_CHAT_REFINED_QUERY_LLM_PROVIDER", "").strip() or None,
        os.getenv("RAG_CHAT_REFINED_QUERY_LLM_MODEL", "").strip() or None,
    ),
}

# CRAG-style retrieval verifier (chat/rag_agent/retrieval_verifier.py). Defaults
# ON; ops can disable via env when debugging or when latency budget is tight.
# max_retries caps how many extra retrieval passes the verifier can trigger
# (1 = up to 2 total passes). Verifier LLM is decoupled from the answer-node
# model — a small fast model is a good fit for a sufficiency judgement.
# Matches fs-agent-chat's agents/nodes/rag.py defaults.
RAG_RETRIEVAL_VERIFIER_ENABLED = os.getenv("RAG_RETRIEVAL_VERIFIER_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
RAG_RETRIEVAL_VERIFIER_MAX_RETRIES = int(os.getenv("RAG_RETRIEVAL_VERIFIER_MAX_RETRIES", "1"))
RAG_RETRIEVAL_VERIFIER_PROVIDER = os.getenv("RAG_RETRIEVAL_VERIFIER_PROVIDER", "openai").strip() or None
RAG_RETRIEVAL_VERIFIER_MODEL = os.getenv("RAG_RETRIEVAL_VERIFIER_MODEL", "gpt-5.4-nano").strip() or None

# Small KB direct mode (chat/rag_node.py::_try_small_kb_direct): bypass the
# agentic ReAct loop for knowledge bases at or under SMALL_KB_DOC_THRESHOLD
# documents, doing one direct hybrid search instead. Matches fs-agent-chat's
# chat_utils/chat_setup_env.py defaults.
SMALL_KB_DIRECT_MODE = os.getenv("SMALL_KB_DIRECT_MODE", "true").lower() in ("true", "1", "yes")
SMALL_KB_DOC_THRESHOLD = int(os.getenv("SMALL_KB_DOC_THRESHOLD", "15"))

# Evidence relevance gate (chat/evidence_relevance.py) — embedding-cosine
# filter applied to cross-turn evidence loaded by get_recent_evidence, so an
# unrelated follow-up query isn't handed the previous query's stale
# documents. Fail-open on any error. Matches fs-agent-chat's
# chat_utils/chat_setup_env.py defaults exactly.
RAG_EVIDENCE_RELEVANCE_ENABLED = os.getenv(
    "RAG_EVIDENCE_RELEVANCE_ENABLED", "true"
).strip().lower() not in {"0", "false", "no", "off"}
RAG_EVIDENCE_RELEVANCE_THRESHOLD = float(
    os.getenv("RAG_EVIDENCE_RELEVANCE_THRESHOLD", "0.30")
)
RAG_EVIDENCE_EMBED_MODEL = os.getenv(
    "RAG_EVIDENCE_EMBED_MODEL", "text-embedding-3-small"
)

# Follow-up question generation (chat/follow_up.py) — post-answer step, gated
# by knowledge_chat_config.follow_up. Reuses the rag_answer_node model (no
# separate node config, per the plan's "Follow-up questions — IN scope"
# section) rather than a fixed small model, unlike fs-agent-chat's donor
# (chat_utils/gpt_processing/followup_questions.py, always openai/gpt-5.4-nano)
# — this service has no Agent-Registry-style per-node config for a dedicated
# follow-up node, so borrowing the answer model avoids inventing a 5th
# hardcoded model default. Timeout mirrors fs-agent-chat's own
# CHART_GEN_TIMEOUT_SEC guard shape (agents/run.py) so a slow follow-up call
# never delays the turn.
RAG_FOLLOW_UP_TIMEOUT_SEC = float(os.getenv("RAG_FOLLOW_UP_TIMEOUT_SEC", "8"))

# Optional env-level override for the follow-up model, decoupling it from
# the answer model above without adding a DB-level knob. Checked first in
# chat/follow_up.py::generate_follow_up_questions; when unset (the default),
# follow-up keeps reusing whichever provider/model generate_answer resolved
# to for this turn — today's behavior, unchanged. provider and model are
# read as a pair: setting only one is ignored for both, same posture as
# resolve_chat_config's provider/model pairing (chat/config.py) — a model id
# resolves against a provider's catalogue, so a half-override would silently
# mix an overridden provider with the answer model's own model id.
#
# Set in .env.local/.env.prod as openai/gpt-5.4-nano (same choice as
# RAG_RETRIEVAL_VERIFIER_MODEL above) — a real, deliberate override, not
# left unset like CHART_API_URL/RAG_CHAT_LLM_PROVIDER/_MODEL. Not set in
# .env.dev; add it there too if dev needs the same override.
FOLLOW_UP_LLM_PROVIDER = os.getenv("FOLLOW_UP_LLM_PROVIDER", "").strip() or None
FOLLOW_UP_LLM_MODEL = os.getenv("FOLLOW_UP_LLM_MODEL", "").strip() or None

# Usage Tracking
USAGE_TRACKING_BASE_URL = os.getenv("USAGE_TRACKING_BASE_URL")

# Usage service migration (docs/plan/usage-and-limits/) — Fernet key +
# HMAC salt for utils/usage/encryption.py, encrypting BYOK provider credentials
# in owner_provider_keys. Same pattern as CONNECTOR_SECRET_KEY above but a
# distinct key/salt pair since credential encryption is a different trust boundary.
USAGE_CREDENTIALS_MASTER_KEY = os.getenv("USAGE_CREDENTIALS_MASTER_KEY")
USAGE_CREDENTIALS_HASH_SALT = os.getenv("USAGE_CREDENTIALS_HASH_SALT")

OTEL_SERVICE_NAME=os.getenv("OTEL_SERVICE_NAME")

# CHAT_AUTH_BASE removed (docs/plan/access-and-sharing/archive/design-share-invitations.md) — non-owner
# RBAC no longer calls out to fs-agent-chat; utils/auth/dependencies.py now
# resolves roles from the local knowledge_invitations table instead. The env
# var itself is left set in .env.dev/.env.prod (harmless, unread) rather than
# edited as part of this code change.

SQS_AWS_REGION = os.getenv("SQS_AWS_REGION")
SQS_AWS_ACCESS_KEY = os.getenv("SQS_AWS_ACCESS_KEY_ID")
SQS_AWS_SECRET_KEY = os.getenv("SQS_AWS_SECRET_ACCESS_KEY")
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL")

QUEUE_NAME = os.getenv("SQS_QUEUE_NAME", "fs-agent-workers.fifo")
LOCAL_QUEUE_NAME = "file-indexer"
STAGING_QUEUE_NAME = "futuresmart_agents_staging_queue.fifo"

# Batch Query Reports (Feature 8 — docs/plan/batch-query-reports/execution.md).
# A DEDICATED queue, separate from the document-ingestion QUEUE_NAME above, so the
# two job types never collide (ported from fs-agent-dashboard's REPORTS_QUEUE_URL/
# REPORTS_QUEUE_NAME convention). Reuses this service's own SQS_AWS_*/CELERY_BROKER_URL
# creds — this service is already a Celery producer for the ingestion queue.
REPORTS_QUEUE_URL = os.getenv("REPORTS_QUEUE_URL")
REPORTS_QUEUE_NAME = os.getenv("REPORTS_QUEUE_NAME", "fs-agent-report-jobs-queue.fifo")
REPORTS_TASK_NAME = os.getenv("REPORTS_TASK_NAME", "workers.tasks.process_report_run")
# Default per-report query cap (DB override, if set, wins — see utils/reports/db_utils.py).
REPORTS_MAX_QUERIES_PER_REPORT = int(os.getenv("REPORTS_MAX_QUERIES_PER_REPORT", "20"))
# In-service scheduler (no external Celery Beat/cron needed — see utils/reports/scheduler.py).
REPORTS_SCHEDULER_ENABLED = os.getenv("REPORTS_SCHEDULER_ENABLED", "false").strip().lower() in ("1", "true", "yes")
REPORTS_SCHEDULER_INTERVAL_SECONDS = int(os.getenv("REPORTS_SCHEDULER_INTERVAL_SECONDS", "60"))

# ── Chat cost finalization from LangSmith ────────────────────────────────────
# chat/pricing.py prices each turn inline off a hardcoded price map, which is only
# a PLACEHOLDER (the donor service treats it the same way). When enabled, a
# fire-and-forget finalizer re-reads the turn's root LangSmith run and replaces
# cost + tokens with what LangSmith actually computed — see
# utils/knowledge_health/cost_finalizer.py and docs/api/agent-health.md.
# Requires LANGSMITH_API_KEY; a turn with no captured run id is left alone.
CHAT_COST_FINALIZE_ENABLED = os.getenv("CHAT_COST_FINALIZE_ENABLED", "true").strip().lower() in ("1", "true", "yes")
# Seconds to wait before each re-read attempt. LangSmith prices runs
# asynchronously, so an immediate read returns tokens but no cost; these delays
# are the donor's proven values. Comma-separated to allow tuning per environment.
CHAT_COST_FINALIZE_DELAYS = tuple(
    int(x) for x in os.getenv("CHAT_COST_FINALIZE_DELAYS", "20,40,90").split(",") if x.strip()
)
# Global cap on concurrent LangSmith reads, so a burst of turns can't saturate the
# event loop's shared thread-pool executor (the SDK client is synchronous).
CHAT_COST_FINALIZE_CONCURRENCY = int(os.getenv("CHAT_COST_FINALIZE_CONCURRENCY", "8"))

# Connector (ClickUp/Notion) crawl lane is DB-driven (no SQS queue): triggers insert a
# connector_sync_run row and a knowledge-side runner process claims + executes them.
# How often that runner polls for due schedules / queued runs (seconds).
CONNECTOR_POLL_INTERVAL_SECONDS = int(os.getenv("CONNECTOR_POLL_INTERVAL_SECONDS", "30"))
# Max crawls the runner executes concurrently (crawls are I/O-bound, so one process
# handles several at once). The cap bounds memory + how fast index jobs flow to the worker.
CONNECTOR_MAX_CONCURRENT = int(os.getenv("CONNECTOR_MAX_CONCURRENT", "3"))
# Run the connector loop inside the FastAPI process (no separate service). A Postgres
# advisory lock ensures only one loop is active across uvicorn workers/replicas.
# Set false if you instead run a dedicated `python -m utils.connectors.runner` service.
# Globally disabled by default (2026-08-28): ClickUp doc sync is paused for all knowledge
# bases. Set CONNECTOR_RUNNER_INPROCESS=true (env var, per-environment) to re-enable —
# no other code change needed; existing per-KB connector configs are left untouched.
CONNECTOR_RUNNER_INPROCESS = os.getenv("CONNECTOR_RUNNER_INPROCESS", "false").lower() in ("true", "1", "yes")
# Re-queue a run stuck 'running' longer than this (minutes) — recovers from a mid-crawl restart.
CONNECTOR_STALE_RUN_MINUTES = int(os.getenv("CONNECTOR_STALE_RUN_MINUTES", "90"))
# Base URL the connector crawl uses to call this service's own indexing endpoints.
KNOWLEDGE_API_BASE_URL = os.getenv("KNOWLEDGE_API_BASE_URL", "http://localhost:8000/api/knowledge/v1")

try:
    ENCODED_AWS_SECRET_KEY = urllib.parse.quote(SQS_AWS_SECRET_KEY or "", safe='')
except Exception as e:
    logger.error(f"Error encoding AWS secret key: {e}")
    ENCODED_AWS_SECRET_KEY = SQS_AWS_SECRET_KEY

OTEL_ENDPOINT = os.getenv("OTEL_ENDPOINT", "")

qdrant_url = os.getenv("QDRANT_URL") or os.getenv("qdrant_url")
qdrant_api_key = os.getenv("QDRANT_API_KEY") or os.getenv("qdrant_api_key", "")

BROKER_URL = os.getenv("CELERY_BROKER_URL") or f"sqs://{SQS_AWS_ACCESS_KEY}:{ENCODED_AWS_SECRET_KEY}@sqs.{SQS_AWS_REGION}.amazonaws.com/"
BACKEND_URL = f"db+postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}/{POSTGRES_DB_NAME}"
LOCAL_BROKER_URL = os.getenv("CELERY_BROKER_URL")
LOCAL_BACKEND_URL = os.getenv("CELERY_RESULT_BACKEND")
if env_file == ".env.local":
    BROKER_URL = LOCAL_BROKER_URL
    BACKEND_URL = LOCAL_BACKEND_URL
    QUEUE_NAME = LOCAL_QUEUE_NAME


# Explicit standalone transport settings; no credentials in broker URLs.
if MODE == "STANDALONE":
    BROKER_URL = os.getenv("CELERY_BROKER_URL", "sqs://")
    BACKEND_URL = os.environ["CELERY_RESULT_BACKEND"]

CELERY_CONFIG = {
    "task_acks_late": False,
    "task_reject_on_worker_lost": True,
    "worker_concurrency": 10,
    "worker_prefetch_multiplier": 1,
    "task_serializer": 'json',
    "accept_content": ['json'],
    "result_serializer": 'json',
    "task_default_queue": QUEUE_NAME,
    "task_queues": {
        QUEUE_NAME: {
            "exchange": QUEUE_NAME,
            "routing_key": QUEUE_NAME,
            "queue_arguments": {
                'FifoQueue': 'true',
                'ContentBasedDeduplication': 'true',
                # Deduplication and Grouping IDs might be better set dynamically per task if needed
                'MessageDeduplicationId': '{message-deduplication-id}', # Example placeholder
                'MessageGroupId': '{message-group-id}'          # Example placeholder
            }
        }
    },
    "broker_transport_options": {
        "region": SQS_AWS_REGION,
        "is_secure": not bool(os.getenv("AWS_ENDPOINT_URL")),
        **({"endpoint_url": os.environ["AWS_ENDPOINT_URL"]} if os.getenv("AWS_ENDPOINT_URL") else {}),
        "visibility_timeout": 3600, # 1 hour
        "polling_interval": 1,
        "predefined_queues": {
            QUEUE_NAME: {
                "url": SQS_QUEUE_URL,
                "access_key": SQS_AWS_ACCESS_KEY,
                "secret_key": SQS_AWS_SECRET_KEY
            }
        }
    },
    "task_time_limit": 3600, # 1 hour
    "task_soft_time_limit": 3000, # 50 minutes
    "result_expires": 3600, # 1 hour
    "result_backend": BACKEND_URL,
    "task_routes": {
        "workers.tasks.*": {"queue": QUEUE_NAME}, # Route tasks from tasks.py
    }
}

# --- RAG / chat evaluation (utils/knowledge_health/rag_eval.py) ---
# Scoped by OWNER, not globally on/off, because the two tiers have very different
# costs and a pilot should not bill every tenant for a judge call:
#
#   Tier 0  structural counts and the verifier verdict. Free — no LLM, no extra
#           latency, values the pipeline already computed. Written inline.
#   Tier 1  the reference-free judge (faithfulness / answer relevance /
#           coherence). Costs money per turn, so it runs as a SEPARATE on-demand
#           pass over stored turns, never inline in a chat request — same reason
#           extraction validation is a separate pass (validation_runner.py).
#
# Allowlist of owner_ids both tiers are enabled for. Comma-separated.
#   ""   (default) → disabled for everyone. Deploying this change alone is a no-op.
#   "*"            → every owner, including turns with no resolved owner.
# A credential-less/public caller has no owner_id, so it can only ever be
# included by "*" — an allowlist cannot match an absent identity, and defaulting
# it in would silently bill the wrong tenant.
#
# ⚠️ THIS DEFAULT IS THE OPPOSITE OF EXTRACTION_VALIDATION_ALLOWED_OWNERS BELOW,
# deliberately. There, an empty list means "gate not configured, judge ALLOWED",
# because that tier already ran in production and an empty env var must not
# silently switch a live feature off. Here, empty means OFF, because this tier is
# new and an empty env var must not silently switch paid spend ON. Retrofit and
# greenfield want opposite fail-safe directions.
#
# So do not infer one from the other. If they are ever unified, the unified
# default has to break one of those two properties — decide which on purpose.
RAG_EVAL_OWNER_IDS = os.getenv("RAG_EVAL_OWNER_IDS", "").strip()
# Tier 1 on/off independently of Tier 0, so an owner can be given the free
# structural metrics without any judge spend.
RAG_EVAL_JUDGE_ENABLED = os.getenv("RAG_EVAL_JUDGE_ENABLED", "false").strip().lower() in ("1", "true", "yes")
# The JUDGE's model, pinned and stored on every score row. Same reasoning as
# EXTRACTION_VALIDATION_MODEL below: what is under test already varies per turn
# (chat model, prompt, per-node config), so a judge that also moved would leave
# every comparison confounded in two places at once and neither factor readable.
RAG_EVAL_PROVIDER = os.getenv("RAG_EVAL_PROVIDER", "anthropic").strip() or None
RAG_EVAL_MODEL = os.getenv("RAG_EVAL_MODEL", "claude-sonnet-5").strip() or None
# Cap per run so a backfill over a wide window cannot spend without bound. The
# runner reports how many turns it left unscored.
RAG_EVAL_MAX_TURNS = int(os.getenv("RAG_EVAL_MAX_TURNS", "50"))

# --- Extraction validation (utils/knowledge_health/extraction_validator.py) ---
# Tier 1 (deterministic checks) is free and always runs. These gate Tier 2, the
# LLM judge, which costs money per document.
EXTRACTION_VALIDATION_ENABLED = os.getenv("EXTRACTION_VALIDATION_ENABLED", "true").strip().lower() in ("1", "true", "yes")
# Tier 2 on/off separately from Tier 1: a deployment can keep the free
# arithmetic checks and skip the paid judge entirely.
EXTRACTION_VALIDATION_LLM_ENABLED = os.getenv("EXTRACTION_VALIDATION_LLM_ENABLED", "true").strip().lower() in ("1", "true", "yes")
# The JUDGE's model, deliberately configured apart from ENRICHMENT_MODEL. A model
# grading its own output has a self-preference bias, and because
# llm_factory.get_enrichment_config swaps the enriching model by processing mode,
# pinning the judge here is the only way to keep one fixed yardstick across modes
# — otherwise a mode comparison also changes the grader, and neither factor can
# be read.
# Anthropic's latest Sonnet. Pinned deliberately as ONE fixed yardstick: the
# enriching model changes with the processing mode, so a judge that moved too
# would leave every mode comparison confounded in two places at once.
# `claude-sonnet-5` is the exact model id — no date suffix.
EXTRACTION_VALIDATION_PROVIDER = os.getenv("EXTRACTION_VALIDATION_PROVIDER", "anthropic").strip() or None
EXTRACTION_VALIDATION_MODEL = os.getenv("EXTRACTION_VALIDATION_MODEL", "claude-sonnet-5").strip() or None
# How the judge is steered on the model generations that REJECT temperature
# (Opus 4.7 and later). Unset by default: those models already default to
# effort=high, so sending a value unasked would change behaviour silently.
# Accepted by both generations — claude-sonnet-4-6 takes effort and temperature.
EXTRACTION_VALIDATION_EFFORT = os.getenv("EXTRACTION_VALIDATION_EFFORT", "").strip() or None
# How many times the judge grades ONE document. Measured on a 55-row statement,
# four calls on byte-identical input returned correctness 0.75/0.75/1.0/0.7273
# and completeness 1.0/1.0/NULL/NULL — so a single call is a sample, not a
# measurement. Above 1, the stored score is the mean and the spread is reported
# beside it. Costs one judge call per repeat per document.
EXTRACTION_VALIDATION_REPEATS = max(1, int(os.getenv("EXTRACTION_VALIDATION_REPEATS", "1")))
# Cap per validate call so a backfill over a large window cannot spend without
# bound. The endpoint reports how many rows it left unvalidated.
EXTRACTION_VALIDATION_MAX_DOCS = int(os.getenv("EXTRACTION_VALIDATION_MAX_DOCS", "25"))

# Completeness ("did we get everything?") is gated SEPARATELY from correctness
# ("is what we got right?"), because they are not the same cost or the same
# maturity. Correctness needs only the field configs plus the extracted output;
# completeness also needs the source text, which is ~10x the judge input on a
# 14k-character document, and its deterministic half relies on structural
# anchors that are document-class specific and unproven outside bank statements.
# Turning this off must leave completeness NULL, never 0.0 — see
# ModeModelStat.avg_completeness and the completeness availability block on
# ModelComparisonResponse: a disabled metric is absent, not a failing score, and
# no endpoint may rank or compare on it while it is off.
EXTRACTION_COMPLETENESS_ENABLED = os.getenv("EXTRACTION_COMPLETENESS_ENABLED", "false").strip().lower() in ("1", "true", "yes")

# Owner allowlist for the PAID validation tier. The Tier 2 judge costs a real
# LLM call per document, so it must not be runnable against arbitrary knowledge
# bases just because the caller holds a write credential.
#
# Keyed on the knowledge base's OWNER (knowledge_config.owner_id), not on the
# caller: the master API key bypasses every permission check and resolves to a
# caller identity of None, so a caller-side gate would be blind on exactly the
# path our own scripts use. Owner-keyed also expresses the thing we actually
# care about — whose documents get sent to a judge.
#
# Semantics (chosen in product review 2026-08-27):
#   unset/empty  -> gate not configured, Tier 2 allowed (preserves behaviour)
#   owner is NULL-> allowed. Master-key-created collections have no owner, and
#                   our own test collections are exactly those. Known gap: this
#                   means an unowned knowledge base is never gated.
#   owner listed -> allowed
#   otherwise    -> Tier 2 WITHHELD; Tier 1 still runs, and the response says so.
#                   Never a 403 and never a 0.0 score: refusing to judge is not
#                   the same as judging badly.
EXTRACTION_VALIDATION_ALLOWED_OWNERS = [
    o.strip() for o in os.getenv("EXTRACTION_VALIDATION_ALLOWED_OWNERS", "").split(",")
    if o.strip()
]

# --- Self-firing chat cost sweep (utils/knowledge_health/cost_sweeper.py) ---
# Chat cost is finalized per turn by routers/chat.py already. This loop exists for
# the turns that attempt LOSES: LangSmith had not priced the run within the retry
# delays, the task died with a restart or deploy, or the feature was off when the
# turn was recorded. Those rows keep the LOCAL estimate (measured ~2.9x high on a
# real turn) and, without this, stay wrong until a human presses sync.
COST_SWEEPER_ENABLED = os.getenv("COST_SWEEPER_ENABLED", "true").strip().lower() in ("1", "true", "yes")
COST_SWEEPER_INTERVAL_SECONDS = int(os.getenv("COST_SWEEPER_INTERVAL_SECONDS", "600"))
# Don't race the per-turn finalizer: its delays total 20+40+90s, so nothing is
# eligible until comfortably past that.
COST_SWEEPER_GRACE_SECONDS = int(os.getenv("COST_SWEEPER_GRACE_SECONDS", "300"))
# LangSmith retention is finite. Past this a read cannot succeed, so retrying is a
# standing cost with no possible benefit.
COST_SWEEPER_MAX_AGE_HOURS = int(os.getenv("COST_SWEEPER_MAX_AGE_HOURS", "168"))
# Per-pass cap, biggest backlog first.
COST_SWEEPER_MAX_KNOWLEDGE_BASES = int(os.getenv("COST_SWEEPER_MAX_KNOWLEDGE_BASES", "10"))

# --- Enrichment cost finalization (utils/knowledge_health/enrichment_cost_finalizer.py) ---
# Fired automatically when an enrichment event is recorded, same posture as the
# per-turn chat finalizer. Enrichment cost was a LOCAL estimate off
# config/usage/model_prices.yaml while chat cost came from LangSmith -- an
# asymmetry with no reason behind it but ordering. The catalog goes stale
# SILENTLY: a model missing from it prices at nothing rather than erroring.
ENRICHMENT_COST_FINALIZE_ENABLED = os.getenv("ENRICHMENT_COST_FINALIZE_ENABLED", "true").strip().lower() in ("1", "true", "yes")
