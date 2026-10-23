import os
import urllib.parse
from dotenv import load_dotenv
from services import celery_setup_env

# Load environment variables from a .env file if needed
# load_dotenv(".env.dev", override=True) # Example: uncomment if you use .env files

# --- Database Configuration ---
# Attempt to get DB settings from environment variables
POSTGRES_HOST = celery_setup_env.POSTGRES_HOST
POSTGRES_USER = celery_setup_env.POSTGRES_USER
POSTGRES_PASSWORD = celery_setup_env.POSTGRES_PASSWORD
POSTGRES_DB_NAME = celery_setup_env.POSTGRES_DB_NAME

# --- SQS Broker Configuration ---
SQS_AWS_REGION = celery_setup_env.SQS_AWS_REGION
SQS_AWS_ACCESS_KEY = celery_setup_env.SQS_AWS_ACCESS_KEY
SQS_AWS_SECRET_KEY = celery_setup_env.SQS_AWS_SECRET_KEY
# Actual SQS queue URL — set via QUEUE_URL env var.
# PROD (.env.prod): https://sqs.../fs-agent-workers.fifo
# DEV  (.env.dev):  https://sqs.../futuresmart_agents_queue_staging.fifo
# This URL is mapped to QUEUE_NAME below via predefined_queues, so the
# local alias name stays "fs-agent-workers.fifo" in all environments.
SQS_QUEUE_URL = celery_setup_env.SQS_QUEUE_URL

# Local Celery alias for the main indexing queue.
# Both PROD and DEV use this same alias — the actual SQS URL they resolve to
# differs per environment (see SQS_QUEUE_URL above and predefined_queues below).
# NOTE: The QUEUE_NAME key in .env.dev is intentionally ignored — routing is
# driven by the URL, not this alias.
QUEUE_NAME = os.getenv("SQS_QUEUE_NAME", "fs-agent-workers.fifo")
LOCAL_QUEUE_NAME = "file-indexer"  # used when MODE=LOCAL (.env.local)
STAGING_QUEUE_NAME = "futuresmart_agents_staging_queue.fifo"  # legacy, not actively used

# Batch Query Reports run on their own SQS FIFO queue so report work and
# document-indexing work don't compete on the same queue.
# REPORTS_QUEUE_URL is only set in PROD (.env.prod). In DEV it is left unset
# (commented out in .env.dev), so the worker skips this queue entirely and
# report tasks fall back to the main indexing queue.
# Derive the local queue alias from the physical queue URL so it matches each
# environment's ACTUAL queue name (prod → fs-agent-report-jobs-queue.fifo,
# staging → fs-agent-staging-report-jobs-queue.fifo). This lets WORKER_QUEUES in
# each ECS task def use the real queue name instead of a shared hardcoded alias.
# Falls back to the prod name when the URL isn't set.
REPORTS_QUEUE_URL = celery_setup_env.REPORTS_QUEUE_URL
if REPORTS_QUEUE_URL:
    REPORTS_QUEUE_NAME = REPORTS_QUEUE_URL.rstrip("/").split("/")[-1]
else:
    REPORTS_QUEUE_NAME = "fs-agent-report-jobs-queue.fifo"
LOCAL_REPORTS_QUEUE_NAME = "file-indexer-reports"


# URL encode the secret key for the broker URL
try:
    ENCODED_AWS_SECRET_KEY = urllib.parse.quote(SQS_AWS_SECRET_KEY or "", safe='')
except Exception as e:
    if celery_setup_env.env_file != ".env.local":
        print(f"Error encoding AWS secret key: {e}")
    ENCODED_AWS_SECRET_KEY = SQS_AWS_SECRET_KEY

# --- Celery Configuration Settings ---
BROKER_URL = os.getenv("CELERY_BROKER_URL") or f"sqs://{SQS_AWS_ACCESS_KEY}:{ENCODED_AWS_SECRET_KEY}@sqs.{SQS_AWS_REGION}.amazonaws.com/"
BACKEND_URL = f"db+postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}/{POSTGRES_DB_NAME}"
LOCAL_BROKER_URL = os.getenv("CELERY_BROKER_URL")
LOCAL_BACKEND_URL = os.getenv("CELERY_RESULT_BACKEND")
if celery_setup_env.env_file == ".env.local":
    BACKEND_URL = LOCAL_BACKEND_URL
    BROKER_URL = LOCAL_BROKER_URL
    QUEUE_NAME = LOCAL_QUEUE_NAME
    REPORTS_QUEUE_NAME = LOCAL_REPORTS_QUEUE_NAME

if celery_setup_env.MODE == "STANDALONE":
    BROKER_URL = os.getenv("CELERY_BROKER_URL", "sqs://")
    BACKEND_URL = os.environ["CELERY_RESULT_BACKEND"]

def _fifo_queue_arguments():
    return {
        'FifoQueue': 'true',
        'ContentBasedDeduplication': 'true',
        # Deduplication and Grouping IDs might be better set dynamically per task if needed
        'MessageDeduplicationId': '{message-deduplication-id}',  # Example placeholder
        'MessageGroupId': '{message-group-id}',                  # Example placeholder
    }


# Declare the queues. The reports queue is only wired up when REPORTS_QUEUE_URL
# is set, so environments without it (e.g. local/dev) still start cleanly.
TASK_QUEUES = {
    QUEUE_NAME: {
        "exchange": QUEUE_NAME,
        "routing_key": QUEUE_NAME,
        "queue_arguments": _fifo_queue_arguments(),
    }
}
# predefined_queues tells kombu-SQS the real URL for each local alias.
# This is why PROD and DEV can share the alias "fs-agent-workers.fifo"
# while routing to different physical SQS queues.
PREDEFINED_QUEUES = {
    QUEUE_NAME: {
        "url": SQS_QUEUE_URL,  # PROD → fs-agent-workers.fifo, DEV → futuresmart_agents_queue_staging.fifo
        "access_key": SQS_AWS_ACCESS_KEY,
        "secret_key": SQS_AWS_SECRET_KEY,
    }
}
if REPORTS_QUEUE_URL:
    TASK_QUEUES[REPORTS_QUEUE_NAME] = {
        "exchange": REPORTS_QUEUE_NAME,
        "routing_key": REPORTS_QUEUE_NAME,
        "queue_arguments": _fifo_queue_arguments(),
    }
    PREDEFINED_QUEUES[REPORTS_QUEUE_NAME] = {
        "url": REPORTS_QUEUE_URL,
        "access_key": SQS_AWS_ACCESS_KEY,
        "secret_key": SQS_AWS_SECRET_KEY,
    }

CELERY_CONFIG = {
    "task_acks_late": False,
    "task_reject_on_worker_lost": True,
    "worker_concurrency": 2,
    # "worker_max_memory_per_child": 800000, # Exceeded → current task finishes, then child restarts (frees memory)
    # "worker_max_tasks_per_child": 100, # Restarts each child process after N tasks
    "worker_prefetch_multiplier": 1,
    "task_serializer": 'json',
    "accept_content": ['json'],
    "result_serializer": 'json',
    "task_default_queue": QUEUE_NAME,
    "task_queues": TASK_QUEUES,
    "broker_transport_options": {
        "region": SQS_AWS_REGION,
        "is_secure": not bool(os.getenv("AWS_ENDPOINT_URL")),
        **({"endpoint_url": os.environ["AWS_ENDPOINT_URL"]} if os.getenv("AWS_ENDPOINT_URL") else {}),
        "visibility_timeout": 3600, # 1 hour
        "polling_interval": 1,
        "predefined_queues": PREDEFINED_QUEUES
    },
    "task_time_limit": 3600, # 1 hour
    "task_soft_time_limit": 3000, # 50 minutes
    "result_expires": 3600, # 1 hour
    "result_backend": BACKEND_URL,
    "task_routes": {
        # Report runs go to their dedicated queue; the exact task names are
        # matched before the wildcard below, so they win. Every report task must be
        # listed explicitly — the wildcard would otherwise put them on the indexing
        # queue, where report work would compete with file ingestion.
        "workers.tasks.process_report_run": {"queue": REPORTS_QUEUE_NAME},
        "workers.tasks.process_nl2sql_report_run": {"queue": REPORTS_QUEUE_NAME},
        "workers.tasks.process_knowledge_report_run": {"queue": REPORTS_QUEUE_NAME},
        "workers.tasks.*": {"queue": QUEUE_NAME}, # Everything else -> indexing queue
    },
}

# --- Worker Settings ---
WORKER_LOG_LEVEL = "INFO"
# WORKER_POOL = "solo" # 'solo' pool is often recommended for async tasks

# --- Application Specific Settings ---
# Add any other application-level constants or settings here
# Example:
# DEFAULT_CHUNK_SIZE = 1024

# Note: The setup_env import is removed as configuration is now handled directly
# via environment variables using os.getenv, providing defaults as fallbacks.
# Ensure necessary environment variables are set in your deployment environment. 