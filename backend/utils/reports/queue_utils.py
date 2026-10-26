"""Enqueue report runs for the Worker's Celery/SQS fleet.

Ported from fs-agent-dashboard's `batch_query_reports/queue_utils.py` (see
docs/plan/batch-query-reports/schema.md's Worker-wiring decision) —
this service becomes the **producer** instead of the dashboard, sending the
same shape of message to the same kind of consumer. Unlike the donor, this
module does NOT build a broker config from scratch: this service is already
a Celery producer for the document-ingestion queue
(`utils/celery_workers/app.py`, `utils/setup_env.CELERY_CONFIG`), so this
mirrors that exact pattern (`Celery(name, broker=..., backend=...)` +
`.conf.update(**config)` + `send_task`) with a second, dedicated queue
(`REPORTS_QUEUE_NAME`/`REPORTS_QUEUE_URL`) so report jobs never collide with
document-ingestion jobs.

The Worker fetches the run definition (`GET /internal/reports/runs/{run_id}`)
and posts results back (`POST .../results` etc) — see
`routers/reports_internal.py`. The task carries only `run_id`; the Worker
resolves everything else. Best-effort: enqueue never raises — if the broker
is unreachable/unconfigured, the run stays `pending` and can be re-enqueued
or picked up later (e.g. via `POST .../runs/{run_id}/retry` in a future
iteration, or by re-running the report).
"""
from typing import Optional

from utils.logger import logger
from utils import setup_env

_celery_app = None


def _get_app():
    """Lazily build a dedicated Celery producer app for report jobs.

    Reuses this service's existing broker/backend URLs and base CELERY_CONFIG
    (SQS transport options, serializers, time limits) but overrides the queue
    name/routing/predefined_queues to point at the reports queue instead of
    the ingestion queue.
    """
    global _celery_app
    if _celery_app is not None:
        return _celery_app

    from celery import Celery

    queue = setup_env.REPORTS_QUEUE_NAME
    app = Celery(
        "fs_agent_knowledge_reports_producer",
        broker=setup_env.BROKER_URL,
        backend=setup_env.BACKEND_URL,
    )
    config = dict(setup_env.CELERY_CONFIG)
    config["task_default_queue"] = queue
    config["task_routes"] = {"workers.tasks.*": {"queue": queue}}
    config["task_queues"] = {
        queue: {
            "exchange": queue,
            "routing_key": queue,
            "queue_arguments": {
                "FifoQueue": "true",
                "ContentBasedDeduplication": "true",
                "MessageDeduplicationId": "{message-deduplication-id}",
                "MessageGroupId": "{message-group-id}",
            },
        }
    }
    transport_options = dict(config.get("broker_transport_options") or {})
    if setup_env.REPORTS_QUEUE_URL:
        queue_conf = {"url": setup_env.REPORTS_QUEUE_URL}
        if setup_env.SQS_AWS_ACCESS_KEY and setup_env.SQS_AWS_SECRET_KEY:
            queue_conf["access_key"] = setup_env.SQS_AWS_ACCESS_KEY
            queue_conf["secret_key"] = setup_env.SQS_AWS_SECRET_KEY
        transport_options["predefined_queues"] = {queue: queue_conf}
    config["broker_transport_options"] = transport_options

    app.conf.update(**config)
    _celery_app = app
    return app


async def enqueue_run(run_id: str) -> bool:
    """Publish a Celery task for `run_id`. Returns True if sent, False otherwise."""
    queue = setup_env.REPORTS_QUEUE_NAME
    if not setup_env.REPORTS_QUEUE_URL and not setup_env.LOCAL_BROKER_URL:
        logger.warning(
            f"[reports.queue] No SQS broker configured (REPORTS_QUEUE_URL/CELERY_BROKER_URL) — "
            f"run {run_id} not enqueued (stays pending)."
        )
        return False

    try:
        app = _get_app()
        send_kwargs = dict(
            name=setup_env.REPORTS_TASK_NAME,
            kwargs={"run_id": run_id},
            queue=queue,
        )
        # FIFO queues: one group per run_id so runs aren't serialized behind each
        # other; explicit dedup id guards accidental double-publish.
        if str(queue).endswith(".fifo"):
            send_kwargs["MessageGroupId"] = run_id
            send_kwargs["MessageDeduplicationId"] = run_id

        app.send_task(**send_kwargs)
        logger.info(f"[reports.queue] Enqueued task {setup_env.REPORTS_TASK_NAME}(run_id={run_id}) -> '{queue}'")
        return True
    except Exception as e:
        logger.error(f"[reports.queue] Failed to enqueue run {run_id}: {e}")
        return False
