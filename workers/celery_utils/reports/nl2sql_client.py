"""Client for NL2SQL's internal Batch Query Reports API.

Same contract, same retry and idempotency behaviour as `dashboard_client` — see
`reports_api_client.ReportsAPIClient` — pointed at NL2SQL instead
(`${NL2SQL_INTERNAL_BASE}/internal/runs/{run_id}`). Used exclusively by
`workers.tasks.process_nl2sql_report_run`.

NL2SQL owns the report tables for its own agents, in its own database. A run_id from NL2SQL is
meaningless to the dashboard and vice versa — both mint `run_<32hex>`, so nothing about the id
reveals which service owns it. That is precisely why NL2SQL runs arrive on their own task name
rather than being distinguished at runtime.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from services import celery_setup_env

from celery_utils.reports.reports_api_client import ReportsAPIClient

_client = ReportsAPIClient(
    base_url=lambda: celery_setup_env.NL2SQL_INTERNAL_BASE,
    label="nl2sql",
    # NL2SQL's own master key — not the dashboard's `FASTAPI_SECRET_KEY`, which these endpoints
    # would reject. In production the two services hold different values.
    api_key=lambda: celery_setup_env.NL2SQL_API_KEY,
)


def get_run_definition(run_id: str) -> Optional[Dict[str, Any]]:
    """Fetch the pre-materialized run definition NL2SQL owns.

    Shape: `{run_id, report_id, agent_id, user_id, report_instructions, analysis_prompt,
    query_groups: [{group_id, type, queries: [{query_id, text}]}]}`.
    """
    return _client.get_run_definition(run_id)


def is_run_cancelled(run_id: str) -> bool:
    """Whether NL2SQL has closed this run out (cancelled/failed/completed) since it started.

    The runner polls this between queries. It matters more here than on the dashboard path: an
    NL2SQL query is a full agent pipeline, so continuing a cancelled run would burn real compute
    for results the terminal-state guard then throws away.
    """
    return _client.is_run_cancelled(run_id)


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
    return _client.submit_results(run_id, results)


def submit_analysis(run_id: str, overall_summary: str) -> bool:
    return _client.submit_analysis(run_id, overall_summary)
