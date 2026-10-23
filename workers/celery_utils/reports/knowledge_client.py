"""Client for the Knowledge Service's internal Batch Query Reports API.

The Knowledge Service is the third owner of Batch Query Reports (after the Dashboard and
NL2SQL) — see fs-agent-knowledge PR #13 and its
`docs/plan/batch-query-reports/`. Two things make it different from the other two:

1. **Path prefix.** It mounts the internal contract at `/internal/reports/runs`, not
   `/internal/runs`. That segment sits between the base URL and the run_id, so pointing an
   existing client at it by changing only the base URL does not work — every call would 404.
   Hence `path_prefix` here.

2. **It executes runs itself.** The Knowledge Service exposes
   `POST {prefix}/{run_id}/execute`, which runs the whole pre-materialized run in-process by
   calling its own chat graph per query. That is the integration this repo uses, via
   `trigger_execute` — the worker's whole job for a knowledge-owned run is to trigger it.

   The passive contract (`get_run_definition` / `mark_*` / `submit_results`) is also reachable
   through this client, but this repo does **not** drive it, and cannot: the passive shape needs
   a chat client for the owning service, and `chat_client` here speaks only fs-agent-chat's
   `/v1/chat_completion` (agent_id-based), which cannot serve knowledge-base chat. Building one
   would duplicate exactly what `/execute` exists to avoid.

Auth: master x-api-key (`KNOWLEDGE_API_KEY`), service-to-service only. This is the Knowledge
Service's own master key — the same one this repo already uses for its enrichment-context and
usage-event calls — not the dashboard's `FASTAPI_SECRET_KEY`.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from services import celery_setup_env

from celery_utils.reports.reports_api_client import ReportsAPIClient

# Base and key are resolved per call (hence the lambdas), so a changed env var or a test
# override takes effect without re-importing this module.
_client = ReportsAPIClient(
    base_url=lambda: celery_setup_env.KNOWLEDGE_REPORTS_INTERNAL_BASE,
    label="knowledge",
    api_key=lambda: celery_setup_env.KNOWLEDGE_API_KEY,
    path_prefix="/internal/reports/runs",
)


def trigger_execute(run_id: str) -> Optional[Dict[str, Any]]:
    """Ask the Knowledge Service to execute this run in-process.

    Returns the response body (`run_id`, `status`, `started`) on success, or None on a 404 or
    exhausted retries. `started: False` is a benign no-op — the run had already left `pending`.
    """
    return _client.trigger_execute(run_id)


def get_run_definition(run_id: str) -> Optional[Dict[str, Any]]:
    """Passive-contract read. Not used by this repo's task — see the module docstring."""
    return _client.get_run_definition(run_id)


def is_run_cancelled(run_id: str) -> bool:
    """Best-effort cancel check.

    Note: as of fs-agent-knowledge PR #13 the Knowledge Service exposes only `POST`
    `{prefix}/{run_id}/status`, not `GET`, so this currently always returns False (the client
    treats a non-200 as "not cancelled" by design). Harmless here because this repo doesn't poll
    cancellation for knowledge runs — it isn't executing the queries.
    """
    return _client.is_run_cancelled(run_id)
