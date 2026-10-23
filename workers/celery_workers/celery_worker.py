import asyncio
import asyncpg
import logging
from celery import Task
from celery.signals import worker_ready, worker_process_shutdown
import os
from celery_utils.db import db_utils
from .app import app
from celery_utils.db.db_pool import db_pool_manager
from celery_workers.config import QUEUE_NAME, REPORTS_QUEUE_NAME, REPORTS_QUEUE_URL
from services import celery_setup_env
from celery_utils.shared.tracing import init_tracing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

@worker_ready.connect
def init_worker(sender, **kwargs):
    """Initialize worker process."""
    logging.info(f"Initializing worker process {os.getpid()}...")
    init_tracing(celery_setup_env.OTEL_SERVICE_NAME)

    # Don't initialize the database pool here - let it be created lazily
    # when first needed in the correct context
    logging.info("Worker process initialized. Database pool will be created when first needed.")

@worker_process_shutdown.connect
def shutdown_worker(**kwargs):
    """Shut down worker process."""
    logging.info(f"Shutting down worker process {os.getpid()}...")

    # Clean up the database pool if needed
    try:
        # Assuming db_pool_manager.close() is a synchronous method
        db_pool_manager.close()
        logging.info("Database connection pool closed.")
    except Exception as e:
        logging.error(f"Error closing database pool: {e}")

    logging.info("Worker process shut down.")

# Entry point for running the worker
if __name__ == '__main__':
    # Determine which queues to listen to based on environment
    mode = os.environ.get("MODE", "DEV").upper()
    
    # Build argument list using imported config
    argv = [
        "worker",
        "--loglevel=info"   
    ]
    if celery_setup_env.env_file == ".env.local":
        argv.append("--pool=solo")
        argv.append("--concurrency=1")
    else:
        argv.append("--pool=prefork")
        argv.append("--concurrency=4")
    # Same worker consumes both the indexing queue and the reports queue.
    # The reports queue is only added when its URL is configured, so envs
    # without REPORTS_QUEUE_URL keep listening to just the indexing queue.
    queues = QUEUE_NAME
    if REPORTS_QUEUE_URL:
        queues = f"{QUEUE_NAME},{REPORTS_QUEUE_NAME}"
    argv.append(f"--queues={queues}")
    
    # # Add queue parameter
    # if mode == "STAGING":
    #     argv.append(f"--queues={STAGING_QUEUE_NAME}")
    #     print(f"Starting Celery worker in STAGING mode, listening to queue: {STAGING_QUEUE_NAME}")
    # else:
    #     # In development or production mode, listen to the regular queue
    #     # Optionally listen to both queues if needed
    #     argv.append(f"--queues={QUEUE_NAME}")
    #     print(f"Starting Celery worker in {mode} mode, listening to queues: {QUEUE_NAME}")
    
    # Print configuration for debugging
    broker_url = os.getenv("CELERY_BROKER_URL", app.conf.broker_url)
    backend_url = os.getenv("CELERY_RESULT_BACKEND", app.conf.result_backend)
    print("Worker broker and result backend configured")
    
    print(f"Starting Celery worker with command: celery -A celery_workers.app { ' '.join(argv) }")
    
    # Start the worker using the app instance from app.py
    app.worker_main(argv=argv)
