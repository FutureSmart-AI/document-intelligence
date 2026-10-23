"""HTTP client for a report service's internal runs API.

Three services now own Batch Query Reports and expose the same internal contract: the Agent
Dashboard (`dashboard_client`), NL2SQL (`nl2sql_client`), and the Knowledge Service
(`knowledge_client`). The endpoints, retry policy and idempotency guarantees are identical —
only the base URL, the master key, and the path prefix differ — so the logic lives here once
and each service is a thin binding.

**Path prefix.** The dashboard and NL2SQL both mount this contract at `/internal/runs`; the
Knowledge Service mounts it at `/internal/reports/runs` (its `routers/reports_internal.py`
declares `APIRouter(prefix="/internal/reports/runs")`). That extra segment is NOT reachable by
changing the base URL alone, because the prefix sits between base and run_id — hence
`path_prefix` is a constructor argument.

Auth: master x-api-key, service-to-service only. Each service validates against its own key.
Idempotency: status transitions are idempotent on (run_id, target_state) and results are
upserted per query_id, so Celery retries are safe.

All calls are synchronous (requests) to match the rest of the worker.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import requests

from services import celery_setup_env
from services.celery_logger import logger


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class ReportsAPIClient:
    """The internal reports API of one service.

    `base_url` and `api_key` are **callables**, not strings: both are read per call so a test
    (or a rotated env) takes effect without re-importing the module — the behaviour the previous
    module-level `_base()` helper had.

    `api_key` matters because each service validates against **its own** master key, and in
    production the dashboard's and NL2SQL's are different values. Defaults to the dashboard's
    `FASTAPI_SECRET_KEY`.

    `path_prefix` is where the service mounts this contract — `/internal/runs` for the dashboard
    and NL2SQL, `/internal/reports/runs` for the Knowledge Service. See the module docstring.
    """

    def __init__(
        self,
        base_url: Callable[[], str],
        label: str,
        api_key: Optional[Callable[[], str]] = None,
        path_prefix: str = "/internal/runs",
    ):
        self._base_url = base_url
        self._label = label  # appears in every log line, so failures name the target service
        self._api_key = api_key or (lambda: celery_setup_env.FASTAPI_SECRET_KEY)
        self._path_prefix = "/" + path_prefix.strip("/")

    # -- internals ---------------------------------------------------------

    def _base(self) -> str:
        return (self._base_url() or "").rstrip("/")

    def _runs(self) -> str:
        """Base URL for this service's runs collection, e.g. `{base}/internal/runs`."""
        return f"{self._base()}{self._path_prefix}"

    def _headers(self) -> Dict[str, str]:
        return {"x-api-key": self._api_key() or "", "Content-Type": "application/json"}

    def _post_with_retry(
        self,
        url: str,
        payload: Dict[str, Any],
        *,
        op: str,
        attempts: int = 3,
        backoff: float = 0.5,
        timeout: float = 30.0,
    ) -> Optional[requests.Response]:
        """POST with bounded retries on transient failures.

        Retries 5xx and connection errors; returns a 4xx as-is (a permanent problem — wrong
        run_id, validation, auth — that retrying cannot fix). Never raises.
        """
        last_exc: Optional[BaseException] = None
        for i in range(1, attempts + 1):
            try:
                resp = requests.post(url, headers=self._headers(), json=payload, timeout=timeout)
                if resp.status_code < 500:
                    return resp
                logger.warning(
                    f"[reports.{self._label}] {op} attempt {i}/{attempts} → "
                    f"{resp.status_code} {resp.text[:200]}"
                )
            except requests.RequestException as e:
                last_exc = e
                logger.warning(f"[reports.{self._label}] {op} attempt {i}/{attempts} failed: {e}")
            if i < attempts:
                time.sleep(backoff * (2 ** (i - 1)))
        logger.error(f"[reports.{self._label}] {op} exhausted retries; last_exc={last_exc}")
        return None

    # -- GET /internal/runs/{run_id} ---------------------------------------

    def get_run_definition(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Fetch the pre-materialized run definition the owning service holds.

        Returns None ONLY on a real 404 (a genuinely missing run — the task should skip it). On
        transient failures it retries and then RAISES, so Celery re-runs the task rather than
        permanently skipping a run because of a momentary blip.
        """
        url = f"{self._runs()}/{run_id}"
        last_exc: Optional[BaseException] = None
        for i in range(1, 4):
            try:
                resp = requests.get(url, headers=self._headers(), timeout=(5, 30))
                if resp.status_code == 404:
                    logger.error(f"[reports.{self._label}] run {run_id} not found")
                    return None  # permanent → caller skips
                if resp.status_code < 500:
                    resp.raise_for_status()
                    return resp.json()
                logger.warning(
                    f"[reports.{self._label}] get_run_definition {i}/3 → "
                    f"{resp.status_code} {resp.text[:200]}"
                )
            except requests.RequestException as e:
                last_exc = e
                logger.warning(f"[reports.{self._label}] get_run_definition {i}/3 failed: {e}")
            if i < 3:
                time.sleep(0.5 * (2 ** (i - 1)))
        raise RuntimeError(f"get_run_definition({run_id}) failed after retries: {last_exc}")

    # -- POST {prefix}/{run_id}/execute ------------------------------------

    def trigger_execute(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Ask the owning service to execute the whole run itself, in-process.

        The alternative to the passive contract: instead of this worker fetching the definition
        and asking each query over HTTP, the owning service runs every query internally. Only the
        Knowledge Service exposes this (see its routers/reports_internal.py) — it can call its own
        chat graph directly, so no per-query HTTP hop is needed.

        Returns the response body on a 2xx — `{"run_id", "status", "started"}`, where
        `started: False` means the run had already left `pending` (already running or terminal),
        which is a benign no-op, not a failure. Returns None on a 404 (missing run → caller skips)
        or after exhausted retries, so the caller can decide whether to fail the Celery task.
        """
        resp = self._post_with_retry(
            f"{self._runs()}/{run_id}/execute", {}, op="trigger_execute"
        )
        if resp is None:
            return None
        if resp.status_code == 404:
            logger.error(f"[reports.{self._label}] run {run_id} not found (execute)")
            return None
        if resp.status_code >= 400:
            logger.error(
                f"[reports.{self._label}] execute for {run_id} → "
                f"{resp.status_code} {resp.text[:200]}"
            )
            return None
        try:
            return resp.json() or {}
        except ValueError:
            # 2xx with an unparseable body still means the service accepted the trigger.
            return {"run_id": run_id, "started": True}

    # -- GET /internal/runs/{run_id}/status ---------------------------------

    def is_run_cancelled(self, run_id: str) -> bool:
        """Whether the run has been cancelled (or otherwise closed out) since it started.

        Polled between queries so a cancel stops the work instead of merely discarding its
        results. Best-effort by design: any failure to check returns False, because aborting a
        healthy run over an unreachable status endpoint would be far worse than finishing it.

        Returns False when the service does not expose this endpoint (404), so a backend without
        it simply behaves as it did before.
        """
        try:
            resp = requests.get(
                f"{self._runs()}/{run_id}/status",
                headers=self._headers(),
                timeout=(5, 10),
            )
            if resp.status_code != 200:
                return False
            return (resp.json() or {}).get("status") in ("cancelled", "failed", "completed")
        except (requests.RequestException, ValueError) as e:
            logger.warning(f"[reports.{self._label}] cancel check failed for {run_id}: {e}")
            return False

    # -- POST /internal/runs/{run_id}/status -------------------------------

    def mark_running(self, run_id: str, started_at: Optional[datetime] = None) -> bool:
        payload: Dict[str, Any] = {
            "status": "running",
            "started_at": _iso(started_at or datetime.now(timezone.utc)),
        }
        resp = self._post_with_retry(
            f"{self._runs()}/{run_id}/status", payload, op="mark_running"
        )
        return bool(resp and resp.status_code == 200)

    def mark_completed(self, run_id: str, ended_at: Optional[datetime] = None) -> bool:
        payload = {
            "status": "completed",
            "ended_at": _iso(ended_at or datetime.now(timezone.utc)),
        }
        resp = self._post_with_retry(
            f"{self._runs()}/{run_id}/status", payload, op="mark_completed"
        )
        return bool(resp and resp.status_code == 200)

    def mark_failed(
        self,
        run_id: str,
        failure_reason: str,
        ended_at: Optional[datetime] = None,
    ) -> bool:
        payload = {
            "status": "failed",
            "ended_at": _iso(ended_at or datetime.now(timezone.utc)),
            "failure_reason": failure_reason,
        }
        resp = self._post_with_retry(
            f"{self._runs()}/{run_id}/status", payload, op="mark_failed"
        )
        return bool(resp and resp.status_code == 200)

    # -- POST /internal/runs/{run_id}/results ------------------------------

    def submit_results(self, run_id: str, results: List[Dict[str, Any]]) -> bool:
        """Submit a batch of per-query results. Upserted by the owning service, so a retried
        batch is safe.
        """
        if not results:
            return True
        resp = self._post_with_retry(
            f"{self._runs()}/{run_id}/results",
            {"results": results},
            op="submit_results",
        )
        return bool(resp and resp.status_code == 200)

    # -- POST /internal/runs/{run_id}/analysis -----------------------------

    def submit_analysis(self, run_id: str, overall_summary: str) -> bool:
        resp = self._post_with_retry(
            f"{self._runs()}/{run_id}/analysis",
            {"overall_summary": overall_summary},
            op="submit_analysis",
        )
        return bool(resp and resp.status_code == 200)
