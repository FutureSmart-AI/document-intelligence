"""Client for the Dashboard's internal Batch Query Reports API.

The dashboard is the system of record for reports created there. This module is the only place
the worker writes that state back to, and it is used exclusively by the
`workers.tasks.process_report_run` task.

The HTTP mechanics (retries, idempotency, auth) live in `reports_api_client.ReportsAPIClient`,
shared with `nl2sql_client` — the two services expose the same internal contract and differ only
in base URL. The module-level functions below are kept as-is so every existing caller and test
that monkeypatches them keeps working unchanged.

Auth: master x-api-key (FASTAPI_SECRET_KEY), service-to-service only.
Idempotency: status transitions are idempotent on (run_id, target_state) and results are
upserted per query_id, so retries from Celery are safe.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from services import celery_setup_env

from celery_utils.reports.reports_api_client import ReportsAPIClient

# The base is resolved per call (hence the lambda), so a changed env var or a test override
# takes effect without re-importing this module.
_client = ReportsAPIClient(
    base_url=lambda: celery_setup_env.DASHBOARD_INTERNAL_BASE,
    label="dashboard",
)


def get_run_definition(run_id: str) -> Optional[Dict[str, Any]]:
    """Fetch the pre-materialized run definition the dashboard owns.

    Returns the dict described in the dashboard spec §5.1 (run_id, report_id, agent_id,
    report_instructions, analysis_prompt, query_groups[…]).

    Returns None ONLY on a real 404 (missing run → the task skips it); transient failures are
    retried and then raise, so Celery retries instead of skipping a run over a blip.
    """
    return _client.get_run_definition(run_id)


def mark_running(run_id: str, started_at: Optional[datetime] = None) -> bool:
    return _client.mark_running(run_id, started_at)


def mark_completed(run_id: str, ended_at: Optional[datetime] = None) -> bool:
    return _client.mark_completed(run_id, ended_at)


def mark_failed(
    run_id: str,
    failure_reason: str,
    ended_at: Optional[datetime] = None,
) -> bool:
    return _client.mark_failed(run_id, failure_reason, ended_at)


def submit_results(run_id: str, results: List[Dict[str, Any]]) -> bool:
    """Submit a batch of per-query results. Upsert on the dashboard side, so a retried batch
    is safe.
    """
    return _client.submit_results(run_id, results)


def submit_analysis(run_id: str, overall_summary: str) -> bool:
    return _client.submit_analysis(run_id, overall_summary)
