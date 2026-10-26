import os
from dotenv import load_dotenv
from services.celery_logger import logger

# Determine the mode (DEV or PROD)
MODE = os.getenv("MODE", "LOCAL").upper()
print(f"MODE IS {MODE}")
env_file = os.getenv("ENV_FILE") or (".env" if MODE == "STANDALONE" else ".env.prod" if MODE == "PROD" else ".env.dev" if MODE == "DEV" else ".env.local")
logger.info(f"Loading environment variables from {env_file}")
# Load the environment variables from the appropriate file
load_dotenv(env_file, override=False)

# FASTAPI Configuration
FASTAPI_SECRET_KEY = os.getenv("FASTAPI_SECRET_KEY")

# MySQL Database Configuration
# DB_HOST = os.getenv("DB_HOST")
# DB_USER = os.getenv("DB_USER")
# DB_PASSWORD = os.getenv("DB_PASSWORD")
# DB_PORT = os.getenv("DB_PORT")
# DB_NAME = os.getenv("DB_NAME")

# PostgreSQL Database Configuration
POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_PORT = os.getenv("POSTGRES_PORT")
POSTGRES_DB_NAME = os.getenv("POSTGRES_DB_NAME")

# OpenAI and Other LLM API Keys
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
# MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY")
MINIMAX_BASE_URL = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1")
llm_provider = os.getenv("llm_provider")
model = os.getenv("model")
temperature = float(os.getenv("temperature", 0.7))

# Possible Models (Dictionary)
# Hardcoded defaults are the last resort in the config chain (per-knowledge -> global
# DB -> env -> here) and must name models that still exist in
# llm_factory.ENRICHMENT_MODELS. Keep them in step with .env* when those change.
# OpenAI-only on purpose: the default must run on an install that has nothing but
# OPENAI_API_KEY (deploy/model-profiles.json openai-v1). It was openai/gpt-oss-120b
# (Groq) until 2026-10-26, which made every extraction on such an install fail over
# to the fallback. The hosted deployment sets ENRICHMENT_MODEL explicitly.
ENRICHMENT_MODEL = os.getenv("ENRICHMENT_MODEL", "").strip() or "gpt-5.4-mini"
ENRICHMENT_FALLBACK_MODEL = os.getenv("ENRICHMENT_FALLBACK_MODEL", "").strip() or "gpt-5.4-nano"
# possible_models = {
#     "gpt-3.5": os.getenv("GPT_3.5", "gpt-3.5-turbo-1106"),
#     "gpt-4": os.getenv("GPT_4", "gpt-4-turbo-preview"),
#     "gpt-4-turbo": os.getenv("GPT_4_TURBO", "gpt-4-turbo"),
#     "gpt-4-omni": os.getenv("GPT_4_OMNI", "gpt-4.1"),
#     "gpt-4-snapshot": os.getenv("GPT_4_SNAPSHOT", "gpt-4.1-2025-04-14")
# }

# Vector DB Configuration
# VECTOR_DB = os.getenv("VECTOR_DB", "Chroma")
# PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
# PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME")
# chroma_path = os.getenv("chroma_path")
MEM0_API_KEY = os.getenv("MEM0_API_KEY")
MEM0_TELEMETRY = os.getenv("MEM0_TELEMETRY", "false").lower() == "true"
embedding_provider = os.getenv("embedding_provider")
no_of_chunks = int(os.getenv("no_of_chunks", 4))
chunk_size = int(os.getenv("chunk_size", 2000))
qdrant_url = os.getenv("QDRANT_URL") or os.getenv("qdrant_url")
qdrant_api_key = os.getenv("QDRANT_API_KEY") or os.getenv("qdrant_api_key")

# Scrapers and Parsers
LLAMA_PARSE_API_KEY = os.getenv("LLAMA_PARSE_API_KEY")
text_extraction = os.getenv("text_extraction", "Llama Parse")
scrapper = os.getenv("scrapper")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
transcript_provider = os.getenv("transcript_provider", "openai")

# Langchain Configuration
LANGCHAIN_TRACING_V2 = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true"
LANGCHAIN_ENDPOINT = os.getenv("LANGCHAIN_ENDPOINT")
LANGCHAIN_PROJECT = os.getenv("LANGCHAIN_PROJECT")
LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY")

# User Limits
USER_LIMIT = int(os.getenv("USER_LIMIT", 10))
CHAT_USER_LIMIT = int(os.getenv("CHAT_USER_LIMIT", 25))
AMBASSADOR_USER_LIMIT = int(os.getenv("AMBASSADOR_USER_LIMIT", 25))
TEAM_MEMBER_USER_LIMIT = int(os.getenv("TEAM_MEMBER_USER_LIMIT", 500))
ADMIN_USER_LIMIT = int(os.getenv("ADMIN_USER_LIMIT", 100000))

test_user_limit = int(os.getenv("test_user_limit", 1000))
test_user_id = os.getenv("test_user_id")
test_email_id = os.getenv("test_email_id")
test_session_id = os.getenv("test_session_id")

REFRESH_LIMIT = float(os.getenv("REFRESH_LIMIT", 0.001))

# AWS S3 Configuration
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
BUCKET_NAME = os.getenv("BUCKET_NAME")

# SQS Configuration
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL") or os.getenv("QUEUE_URL")
SQS_QUEUE_NAME = os.getenv("SQS_QUEUE_NAME") or os.getenv("QUEUE_NAME")
SQS_AWS_REGION = os.getenv("SQS_AWS_REGION")
SQS_AWS_ACCESS_KEY = os.getenv("SQS_AWS_ACCESS_KEY_ID")
SQS_AWS_SECRET_KEY = os.getenv("SQS_AWS_SECRET_ACCESS_KEY")

# Batch Query Reports — dedicated SQS FIFO queue, separate from the indexing
# queue. Left unset in environments that don't run reports; the worker then
# simply doesn't consume a reports queue.
REPORTS_QUEUE_URL = os.getenv("REPORTS_QUEUE_URL")

def get_database_url():
    """Construct the database URL from environment variables."""
    return f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB_NAME}"

# Usage tracking and BYOK credential lookup both go to fs-agent-knowledge now
# (KNOWLEDGE_API_BASE_URL / KNOWLEDGE_API_KEY, read directly in
# celery_utils/shared/{user_usage_tracking,credentials_utils}.py). The standalone
# fs-agent-usage-limits service this used to call is being decommissioned.

OTEL_SERVICE_NAME=os.getenv("OTEL_SERVICE_NAME", "fs-worker")

# Batch Query Reports — the worker is a stateless consumer for report runs.
# It fetches the run definition + writes results back via the Dashboard's
# /internal/runs/* API (master x-api-key only), and executes each query
# through the Chat Service.
#
# DASHBOARD_INTERNAL_BASE: includes the /api root_path the dashboard mounts,
#   so the final URL is `${DASHBOARD_INTERNAL_BASE}/internal/runs/{run_id}`.
# CHAT_SERVICE_BASE: same base the dashboard uses for the chat service.
# CHAT_SERVICE_QUERY_PATH: chat-service path that takes a question + session
#   and returns an answer. The real endpoint is POST {base}/v1/chat_completion
#   (confirmed via the chat service's Swagger UI). It streams by default, so
#   the worker sends stream=false to get a single response.
# Groups and the queries within an independent group all run fully in parallel
# (no concurrency cap) — the chat service handles the load. Per-query requests
# have no client timeout, and results are flushed per group as each completes.
# The hosted FutureSmart profile is the only one with a default: a standalone
# installation must name its own services, so it never sends its master key to
# another operator's host.
_HOSTED_AGENT_BASE = "" if MODE == "STANDALONE" else "https://agent.futuresmart.ai"
DASHBOARD_INTERNAL_BASE = os.getenv(
    "DASHBOARD_INTERNAL_BASE",
    f"{_HOSTED_AGENT_BASE}/api" if _HOSTED_AGENT_BASE else "",
)
CHAT_SERVICE_BASE = os.getenv(
    "CHAT_SERVICE_BASE",
    f"{_HOSTED_AGENT_BASE}/api/chat" if _HOSTED_AGENT_BASE else "",
)
CHAT_SERVICE_QUERY_PATH = os.getenv("CHAT_SERVICE_QUERY_PATH", "/v1/chat_completion")

# --- Batch Query Reports for NL2SQL agents -----------------------------------
# NL2SQL runs its own Batch Query Reports (it owns the report tables in its own
# database) and enqueues them as `workers.tasks.process_nl2sql_report_run`. That task is
# the same pipeline as the dashboard's, pointed at a different pair of services:
#
#   definition + results  ->  ${NL2SQL_INTERNAL_BASE}/internal/runs/{run_id}
#   each query            ->  POST ${NL2SQL_CHAT_BASE}/v1/agents/{agent_id}/chat
#
# The chat service is NOT usable for these agents: it 404s on any agent NL2SQL created,
# which is why NL2SQL owns the feature and why this task exists as its own entry point.
# Note the agent_id is in the PATH here, not the body — the dashboard's CHAT_SERVICE_*
# vars cannot express that, so NL2SQL gets its own base.
#
# Both default to the same host because NL2SQL serves its internal API and its chat
# endpoint from one mount (`/nl2sql/api`); they are separate vars only so a deployment can
# split them (e.g. an internal ALB for one).
# The master key NL2SQL validates against — its own FASTAPI_SECRET_KEY, which is NOT the same
# value as the dashboard's in production. `FASTAPI_SECRET_KEY` here is the dashboard's (that is
# what the dashboard's internal API and the chat service accept), so reusing it against NL2SQL
# would 401 every call. Falls back to FASTAPI_SECRET_KEY for environments where the two services
# do share a key.
NL2SQL_API_KEY = os.getenv("NL2SQL_API_KEY") or FASTAPI_SECRET_KEY

NL2SQL_INTERNAL_BASE = os.getenv(
    "NL2SQL_INTERNAL_BASE",
    f"{_HOSTED_AGENT_BASE}/nl2sql/api" if _HOSTED_AGENT_BASE else "",
)
NL2SQL_CHAT_BASE = os.getenv("NL2SQL_CHAT_BASE", NL2SQL_INTERNAL_BASE)

# --- Batch Query Reports for the Knowledge Service ---------------------------
# The Knowledge Service is the third owner of Batch Query Reports (after the dashboard and
# NL2SQL) and enqueues them as `workers.tasks.process_knowledge_report_run`. Unlike the other
# two, this worker does NOT execute the queries: the Knowledge Service exposes
#
#   POST ${KNOWLEDGE_REPORTS_INTERNAL_BASE}/internal/reports/runs/{run_id}/execute
#
# which runs the whole pre-materialized run in-process against its own chat graph. So the task
# is a thin trigger and the Knowledge Service owns the run's lifecycle end to end.
#
# Two gotchas, both real:
#  - The path prefix is `/internal/reports/runs`, NOT `/internal/runs` like the other two. It
#    cannot be absorbed by the base URL (it sits before the run_id), so the client takes it as
#    an explicit argument.
#  - The key is the Knowledge Service's own master key (`KNOWLEDGE_API_KEY`), not the
#    dashboard's `FASTAPI_SECRET_KEY`, which would 401 there.
#
# Defaults match KNOWLEDGE_API_BASE_URL, the same base/key pair this repo already uses for
# enrichment context and usage events — the internal reports routes are on the same mount.
KNOWLEDGE_API_BASE_URL = os.getenv(
    "KNOWLEDGE_API_BASE_URL",
    "http://api:8000/api/knowledge" if MODE == "STANDALONE" else "https://agent.futuresmart.ai/api/knowledge",
)
KNOWLEDGE_API_KEY = os.getenv("KNOWLEDGE_API_KEY", "")
KNOWLEDGE_REPORTS_INTERNAL_BASE = os.getenv(
    "KNOWLEDGE_REPORTS_INTERNAL_BASE",
    KNOWLEDGE_API_BASE_URL,
)

# Fan-out caps for NL2SQL report runs. The dashboard path is deliberately uncapped
# because the chat service absorbs the load, but that reasoning does not carry over:
# every NL2SQL query runs a full agent pipeline holding a source-database connection
# inside the NL2SQL API process, so an uncapped 20-query report would open 20 at once
# and starve interactive chat. These bound ONE run; several runs can still be in flight
# across the fleet, so size them against NL2SQL's replica count and DB pool.
NL2SQL_REPORTS_MAX_CONCURRENT_QUERIES = int(
    os.getenv("NL2SQL_REPORTS_MAX_CONCURRENT_QUERIES", "4")
)
NL2SQL_REPORTS_MAX_CONCURRENT_GROUPS = int(
    os.getenv("NL2SQL_REPORTS_MAX_CONCURRENT_GROUPS", "2")
)
# Unlike the dashboard path (no client timeout), a batch query gets a ceiling: nobody is
# watching it, and a hung request would hold a concurrency slot for the whole visibility
# timeout.
NL2SQL_REPORTS_QUERY_TIMEOUT_SECONDS = int(
    os.getenv("NL2SQL_REPORTS_QUERY_TIMEOUT_SECONDS", "300")
)