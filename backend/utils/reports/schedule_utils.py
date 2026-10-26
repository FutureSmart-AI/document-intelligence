"""Pure schedule math for Batch Query Reports.

Ported near-verbatim from fs-agent-dashboard's `batch_query_reports/
schedule_utils.py` (see docs/plan/batch-query-reports/schema.md) —
this module is dependency-free of `utils/reports/db_utils.py`/`queue_utils.py`
so it can be imported anywhere without circular imports.

`compute_next_run_at` maps a report's `schedule` config to the next run time,
in **naive UTC** (matching `datetime.utcnow()`, used consistently by
`utils/reports/db_utils.py` for this feature's own scheduling columns —
kept naive here, even though the DB columns themselves are TIMESTAMPTZ, so
comparisons against `datetime.utcnow()` stay simple; asyncpg/SQLAlchemy
correctly binds a naive UTC datetime to a `timestamptz` column as UTC).

Schedule shape (all optional except frequency):
    {"frequency": "daily" | "weekly" | "monthly" | "cron", "cron": "*/5 * * * *", "timezone": "UTC"}
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from dateutil.relativedelta import relativedelta

try:  # py3.9+
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

from utils.logger import logger


def _naive_utc(dt: datetime) -> datetime:
    """Drop tzinfo, normalizing to UTC first if aware."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def compute_next_run_at(
    schedule: Optional[Dict[str, Any]],
    base: Optional[datetime] = None,
) -> Optional[datetime]:
    """Return the next run time (naive UTC) after `base`, or None if not schedulable.

    `base` defaults to "now" (naive UTC). For fixed intervals the next run is `base + interval`.
    For cron, it's the next matching instant at/after `base` in the schedule's timezone.
    Returns None for an absent/unrecognized schedule, or a cron schedule with no expression.
    """
    if not schedule:
        return None

    base = _naive_utc(base) if base else datetime.utcnow()
    frequency = (schedule.get("frequency") or "").lower()

    if frequency == "daily":
        return base + timedelta(days=1)
    if frequency == "weekly":
        return base + timedelta(weeks=1)
    if frequency == "monthly":
        return base + relativedelta(months=1)
    if frequency == "cron":
        cron = schedule.get("cron")
        if not cron:
            logger.warning("[reports.schedule] cron frequency with no expression — not scheduling")
            return None
        return _next_cron(cron, base, schedule.get("timezone") or "UTC")

    logger.warning(f"[reports.schedule] unknown frequency '{frequency}' — not scheduling")
    return None


def _next_cron(cron: str, base_utc: datetime, tz_name: str) -> Optional[datetime]:
    try:
        from croniter import croniter
    except Exception:  # pragma: no cover
        logger.error("[reports.schedule] croniter not installed — cannot evaluate cron schedules")
        return None

    try:
        tz = ZoneInfo(tz_name) if ZoneInfo else timezone.utc
    except Exception:
        logger.warning(f"[reports.schedule] unknown timezone '{tz_name}', falling back to UTC")
        tz = timezone.utc

    try:
        base_local = base_utc.replace(tzinfo=timezone.utc).astimezone(tz)
        nxt = croniter(cron, base_local).get_next(datetime)  # tz-aware in `tz`
        return _naive_utc(nxt)
    except Exception as e:
        logger.error(f"[reports.schedule] invalid cron '{cron}': {e}")
        return None
