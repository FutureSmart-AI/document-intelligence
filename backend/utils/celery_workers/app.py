from celery import Celery
from utils.setup_env import BROKER_URL as CELERY_BROKER_URL, BACKEND_URL as CELERY_RESULT_BACKEND, CELERY_CONFIG
# from utils.setup_env import LOCAL_BROKER_URL as CELERY_BROKER_URL, LOCAL_BACKEND_URL as CELERY_RESULT_BACKEND, CELERY_CONFIG
app = Celery(
    'file_indexer',
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    # include=['celery_workers.tasks'] # Point to the tasks module
)

app.conf.update(**CELERY_CONFIG)

# Optional: If you need the DatabaseTask or similar base classes often,
# you could define or import them here and have tasks inherit from them.
# from .worker_utils import DatabaseTask # Example 